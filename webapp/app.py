"""
app.py - Flask webapp para configurar y lanzar el proceso de etiquetado
de principios de persuasion de Ana Ferreira.
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_from_directory, session, stream_with_context, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
# Firmas de magic bytes para validacion de uploads (pure Python, sin dependencias de sistema)
_UPLOAD_MAGIC: dict[str, bytes] = {
    ".xlsx": b"\x50\x4B\x03\x04",        # ZIP/OOXML
    ".xls":  b"\xD0\xCF\x11\xE0",        # OLE2 compound document
}

# Firmas binarias que nunca pueden ser un CSV de texto plano
_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"\x50\x4B\x03\x04",    # ZIP / XLSX / JAR / DOCX
    b"\xD0\xCF\x11\xE0",    # OLE2 / XLS / DOC
    b"\x7F\x45\x4C\x46",    # ELF (ejecutable Linux/Android)
    b"MZ",                   # PE / COFF (ejecutable Windows / DLL)
    b"\xCA\xFE\xBA\xBE",    # Mach-O fat binary (macOS)
    b"\xFE\xED\xFA\xCE",    # Mach-O 32-bit (macOS)
    b"\xFE\xED\xFA\xCF",    # Mach-O 64-bit (macOS)
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"\xFF\xD8\xFF",         # JPEG
    b"GIF8",                 # GIF
    b"%PDF",                 # PDF
)

def _validate_upload_content(header: bytes, suffix: str) -> bool:
    """Verifica que el contenido real del archivo coincida con la extension declarada."""
    sig = _UPLOAD_MAGIC.get(suffix)
    if sig:
        return header.startswith(sig)

    # CSV: rechazar primero cualquier firma binaria conocida
    for bin_sig in _BINARY_SIGNATURES:
        if header.startswith(bin_sig):
            return False

    # Intentar como UTF-8 (encoding moderno estandar para CSV)
    try:
        header.decode("utf-8")
        return True
    except (UnicodeDecodeError, ValueError):
        pass

    # Fallback para CSV legacy (Windows-1252, latin-1, etc.):
    # latin-1 decodifica cualquier byte, así que verificamos adicionalmente
    # que el contenido sea mayoritariamente texto imprimible (umbral 70%)
    sample = header[:512]
    printable = sum(1 for b in sample if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D))
    return (printable / len(sample)) > 0.70 if sample else False

# ---------------------------------------------------------------------------
# Inicializacion de CUDA (debe ir antes de cualquier import que use llama_cpp)
# ---------------------------------------------------------------------------
from labelforge_core.cuda_init import _ensure_cuda_ready as _cuda_ready
_cuda_ready()

# Forzar importacion de llama_cpp ahora (mientras CUDA esta listo) para que
# quede cacheado en sys.modules y los imports posteriores no fallen.
try:
    import llama_cpp as _llama_cpp_  # noqa: F401
except Exception:
    pass

# ---------------------------------------------------------------------------
# Modulos compartidos del nucleo
# ---------------------------------------------------------------------------
from labelforge_core.arbiters_io import (
    build_arbiters_export_payload,
    decrypt_arbiters_export,
    encrypt_arbiters_export,
    merge_arbiters,
    normalize_arbiter,
    require_export_password,
    validate_imported_arbiters,
)
from labelforge_core.checkpoint_io import clear_runtime_checkpoints, CLAIM_RESULTS_PATH
from labelforge_core.dataset_io import (
    DatasetError,
    inspect_dataset,
    load_dataset,
    profile_dataset,
    resolve_dataset_path,
)
from labelforge_core.run_reporting import (
    build_run_stats,
    describe_output_csv as _describe_output_csv_from_rows,
    principle_counts_from_rows,
)
from labelforge_core.secrets_io import provider_api_key_env, resolve_gcp_credentials, secrets_env_path, set_secret_value
from labelforge_core.rate_limits import (
    compute_min_delay,
    get_defaults as _rl_get_defaults,
    load_config as _rl_load_config,
    save_config as _rl_save_config,
)
from labelforge_core.provider_preflight import validate_provider_preflight
from labelforge_core.utils import safe_int as _safe_int
from labelforge_core.paths import (
    ARBITERS_CONFIG_PATH,
    BASE_DIR,
    CHECKPOINTS_DIR,
    CONFIG_DIR,
    DATA_DIR,
    HW_PROFILE_PATH,
    LOGS_DIR,
    MODELS_DIR,
    OUTPUT_DIR,
    resolve_config_file_path,
)

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).resolve().parent.parent
WEBAPP_DIR      = Path(__file__).resolve().parent
CONFIG_DIR.mkdir(exist_ok=True)
CONFIG_PATH     = resolve_config_file_path(ARBITERS_CONFIG_PATH)

SECRETS_ENV_PATH = secrets_env_path(BASE_DIR)
load_dotenv(SECRETS_ENV_PATH)
resolve_gcp_credentials(BASE_DIR)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB maximo por upload

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ---------------------------------------------------------------------------
# Logging HTTP y errores de servidor
# ---------------------------------------------------------------------------
import logging as _logging
from logging.handlers import RotatingFileHandler as _RotatingFileHandler

_logging.getLogger("werkzeug").setLevel(_logging.ERROR)

LOGS_DIR.mkdir(exist_ok=True)
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s %(message)s"
_LOG_FORMATTER = _logging.Formatter(_LOG_FORMAT)


def _configure_logger(logger: _logging.Logger, level: int = _logging.INFO) -> _logging.Logger:
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()

    stream_handler = _logging.StreamHandler()
    stream_handler.setFormatter(_LOG_FORMATTER)
    logger.addHandler(stream_handler)

    file_handler = _RotatingFileHandler(
        LOGS_DIR / "webapp.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(_LOG_FORMATTER)
    logger.addHandler(file_handler)
    return logger


_req_log = _logging.getLogger("labelforge.http")
_configure_logger(_req_log)

_log = _logging.getLogger(__name__)
_configure_logger(_log)
_configure_logger(app.logger)

_run_log = _logging.getLogger("labelforge.run")
_configure_logger(_run_log)

_HTTP_SKIP       = frozenset({"/favicon.ico"})
_HTTP_SKIP_PREFX = ("/static/",)


@app.before_request
def _req_timer():
    g._t0 = time.perf_counter()
    g.request_id = uuid.uuid4().hex[:8]


def _client_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    return forwarded_for or request.remote_addr or "-"


def _request_target() -> str:
    query = request.query_string.decode("utf-8", errors="replace")
    return request.path + (f"?{query}" if query else "")


def _byte_count(value) -> str:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    return f"{n}B" if n else "-"


@app.after_request
def _log_http(response):
    path = request.path
    if path in _HTTP_SKIP or any(path.startswith(p) for p in _HTTP_SKIP_PREFX):
        return response
    request_id = getattr(g, "request_id", "-")
    response.headers.setdefault("X-Request-ID", request_id)
    elapsed = (time.perf_counter() - getattr(g, "_t0", time.perf_counter())) * 1000
    status  = response.status_code
    if status == 304:
        return response
    user = (session.get("user") or {}).get("username", "-")
    msg = (
        f"rid={request_id} ip={_client_ip()} user={user} "
        f"{request.method} {_request_target()} status={status} "
        f"{elapsed:.1f}ms req={_byte_count(request.content_length)} "
        f"res={_byte_count(response.calculate_content_length())} "
        f"endpoint={request.endpoint or '-'}"
    )
    if status >= 500:
        _req_log.error(msg)
    elif status >= 400:
        _req_log.warning(msg)
    else:
        _req_log.info(msg)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure or app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # Cabeceras de seguridad: las paginas y APIs autenticadas no deben quedar
    # reutilizables por cache/back-forward cache despues de logout.
    if "text/html" in response.content_type or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if "text/html" in response.content_type:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self'"
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.teardown_request
def _log_unhandled_exception(exc):
    if exc is None:
        return
    request_id = getattr(g, "request_id", "-")
    _log.exception(
        "rid=%s unhandled_exception %s %s endpoint=%s",
        request_id,
        request.method,
        _request_target(),
        request.endpoint or "-",
        exc_info=True,
    )


# ---------------------------------------------------------------------------
# Autenticacion  -  login local SQLite
# ---------------------------------------------------------------------------
import secrets as _sec


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


# Secret key para sesiones Flask (necesaria para cookies seguras)
# En produccion, LABELFORGE_SECRET_KEY debe estar configurada obligatoriamente
_secret_key_env = os.environ.get("LABELFORGE_SECRET_KEY")
if not _secret_key_env:
    # Solo generar temporalmente en desarrollo si no esta configurada
    app.logger.warning("LABELFORGE_SECRET_KEY no configurada. Usando clave temporal (NO RECOMENDADO EN PRODUCCION).")
    app.secret_key = _sec.token_hex(32)
else:
    app.secret_key = _secret_key_env

_SESSION_IDLE_SECONDS = int(os.environ.get("LABELFORGE_SESSION_IDLE_SECONDS", "3600"))
_SESSION_ABSOLUTE_SECONDS = int(os.environ.get("LABELFORGE_SESSION_ABSOLUTE_SECONDS", "28800"))

# Cookie Secure: activada por defecto en produccion. Desactivar solo en desarrollo local HTTP.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get("LABELFORGE_COOKIE_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=_env_bool("LABELFORGE_COOKIE_SECURE", True),  # Default True para seguridad
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=_SESSION_ABSOLUTE_SECONDS),
    SESSION_REFRESH_EACH_REQUEST=True,
    TEMPLATES_AUTO_RELOAD=True,
)

_AUTH_EXEMPT = frozenset({"/favicon.ico", "/login", "/logout", "/forgot-password"})
_AUTH_EXEMPT_PREFIX = ("/static/", "/login/", "/reset-password/")
_CSRF_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOGIN_FAIL_WINDOW_SECONDS = 900
_LOGIN_FAIL_MAX = 8
_login_failures: dict[str, list[float]] = {}
_ADMIN_PAGE_PATHS = frozenset({"/arbiters", "/principles", "/processing", "/run", "/users"})
_ADMIN_API_EXACT = frozenset({
    "/api/config",
    "/api/models",
    "/api/provider-key",
    "/api/rate-limits",
    "/api/rate-limits/summary",
    "/api/rate-limits/config",
    "/api/rate-limits/config/defaults",
    "/api/dataset/config",
    "/api/dataset/inspect",
    "/api/dataset/profile",
    "/api/dataset/upload",
    "/api/prompt-template",
    "/api/processing",
    "/api/run/start",
    "/api/run/stop",
    "/api/run/status",
    "/api/run/snapshot",
    "/api/run/stats",
    "/api/run/principle-counts",
    "/api/checkpoint/status",
    "/api/checkpoint/clear",
    "/api/failed-queue",
})
_ADMIN_API_PREFIXES = (
    "/api/arbiters",
    "/api/principles",
    "/api/local",
    "/api/users",
)

AUTH_DB_PATH = Path(os.environ.get("LABELFORGE_AUTH_DB", SECRETS_ENV_PATH.parent / "auth.db"))
APP_STATE_DIR = BASE_DIR / "app_state"
AVATARS_DIR = APP_STATE_DIR / "avatars"
LEGACY_AVATARS_DIR = DATA_DIR / "avatars"
AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ADMIN_ROLE = "admin"
USER_ROLE = "user"


def _migrate_legacy_avatars() -> None:
    if not LEGACY_AVATARS_DIR.exists():
        return
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    for src in LEGACY_AVATARS_DIR.iterdir():
        if not src.is_file():
            continue
        dest = AVATARS_DIR / src.name
        if not dest.exists():
            src.replace(dest)
    try:
        LEGACY_AVATARS_DIR.rmdir()
    except OSError:
        pass


_migrate_legacy_avatars()


def _resolve_allowed_dataset_path(raw_path: str) -> Path:
    return resolve_dataset_path(
        raw_path,
        base_dir=BASE_DIR,
        allowed_root=DATA_DIR,
        suffixes={".csv", ".xlsx", ".xls"},
    )


def _auth_db() -> sqlite3.Connection:
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            avatar_filename TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1,
            session_version TEXT NOT NULL DEFAULT '',
            last_login_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _migrate_auth_db(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_ownership (
            output_file TEXT PRIMARY KEY,
            run_id TEXT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    return conn


def _migrate_auth_db(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    migrations = {
        "email": "ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''",
        "avatar_filename": "ALTER TABLE users ADD COLUMN avatar_filename TEXT NOT NULL DEFAULT ''",
        "role": "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
        "active": "ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
        "session_version": "ALTER TABLE users ADD COLUMN session_version TEXT NOT NULL DEFAULT ''",
        "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        "updated_at": "ALTER TABLE users ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }
    for column, sql in migrations.items():
        if column not in columns:
            conn.execute(sql)
    admin_count = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = ?", (ADMIN_ROLE,)).fetchone()["n"]
    if admin_count == 0:
        first = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if first:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (ADMIN_ROLE, first["id"]))
    elif admin_count > 1:
        first_admin = conn.execute("SELECT id FROM users WHERE role = ? ORDER BY id LIMIT 1", (ADMIN_ROLE,)).fetchone()
        conn.execute("UPDATE users SET role = ? WHERE role = ? AND id <> ?", (USER_ROLE, ADMIN_ROLE, first_admin["id"]))
    conn.execute("UPDATE users SET session_version = lower(hex(randomblob(16))) WHERE session_version = '' OR session_version IS NULL")


def _row_to_user(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    avatar_filename = row["avatar_filename"] or ""
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "email": row["email"] or "",
        "avatar_filename": avatar_filename,
        "avatar_url": f"/avatars/{avatar_filename}" if avatar_filename else "",
        "role": row["role"] or USER_ROLE,
        "active": bool(row["active"]),
        "session_version": row["session_version"],
        "provider": "local",
        "provider_label": "Local",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
    }


def _get_user_by_id(user_id: int) -> dict | None:
    with _auth_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


def _get_user_by_username(username: str) -> dict | None:
    with _auth_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", ((username or "").strip(),)).fetchone()
    return _row_to_user(row)


def _session_user() -> dict | None:
    now = int(time.time())
    auth_at = int(session.get("auth_at") or 0)
    last_seen = int(session.get("last_seen") or 0)
    if auth_at and now - auth_at > _SESSION_ABSOLUTE_SECONDS:
        session.clear()
        return None
    if last_seen and now - last_seen > _SESSION_IDLE_SECONDS:
        session.clear()
        return None

    user_id = (session.get("user") or {}).get("id")
    if not user_id:
        return None
    user = _get_user_by_id(int(user_id))
    if not user or not user["active"]:
        session.clear()
        return None
    if session.get("session_version") != user.get("session_version"):
        session.clear()
        return None
    session["user"] = user
    session["last_seen"] = now
    session.setdefault("csrf_token", _sec.token_urlsafe(32))
    return user


def _current_user_id() -> int | None:
    user = _session_user()
    return int(user["id"]) if user else None


def _current_user_is_admin() -> bool:
    user = _session_user()
    return bool(user and user.get("role") == ADMIN_ROLE)


def _require_admin():
    if _current_user_is_admin():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Permiso de admin requerido."}), 403
    return redirect(url_for("index"))


def _admin_required_response():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Permiso de admin requerido."}), 403
    return redirect(url_for("index"))


def _local_user_count() -> int:
    with _auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])


def _active_user_count() -> int:
    with _auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE active = 1").fetchone()
        return int(row["n"])


def _admin_user_count() -> int:
    with _auth_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = ? AND active = 1", (ADMIN_ROLE,)).fetchone()
        return int(row["n"])


def _validate_username(username: str) -> tuple[bool, str]:
    username = (username or "").strip()
    if not username:
        return False, "Usuario requerido."
    if not re.match(r"^[A-Za-z0-9_.-]{3,40}$", username):
        return False, "Usuario invalido. Usa 3-40 caracteres: letras, numeros, punto, guion o guion bajo."
    return True, ""


def _validate_password_strength(password: str) -> tuple[bool, str]:
    password = password or ""
    if len(password) < 12:
        return False, "La contrasena debe tener al menos 12 caracteres."
    classes = [
        any(ch.islower() for ch in password),
        any(ch.isupper() for ch in password),
        any(ch.isdigit() for ch in password),
        any(not ch.isalnum() for ch in password),
    ]
    if sum(classes) < 3:
        return False, "La contrasena debe combinar al menos 3 tipos: minusculas, mayusculas, numeros o simbolos."
    return True, ""


def _create_local_user(username: str, password: str, email: str = "", role: str = USER_ROLE) -> tuple[bool, str, dict | None]:
    username = (username or "").strip()
    ok, error = _validate_username(username)
    if not ok:
        return False, error, None
    ok, error = _validate_password_strength(password)
    if not ok:
        return False, error, None
    role = role if role in {ADMIN_ROLE, USER_ROLE} else USER_ROLE
    if role == ADMIN_ROLE and _admin_user_count() > 0:
        return False, "Ya existe un admin. Usa transferencia de admin.", None
    try:
        with _auth_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, email, role, session_version) VALUES (?, ?, ?, ?, lower(hex(randomblob(16))))",
                (username, generate_password_hash(password), (email or "").strip(), role),
            )
            conn.commit()
            user_id = cur.lastrowid
        return True, "", _get_user_by_id(user_id)
    except sqlite3.IntegrityError:
        return False, "Ese usuario ya existe.", None


def _local_credentials_user(username: str, password: str) -> dict | None:
    username = (username or "").strip()
    if not username or not password:
        return None
    with _auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (username,),
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            conn.commit()
            return _row_to_user(row)
    return None


_RESET_TOKEN_TTL_SECONDS = 3600


def _generate_reset_token(user_id: int) -> str:
    raw = _sec.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=_RESET_TOKEN_TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _auth_db() as conn:
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ? AND used = 0", (user_id,))
        conn.execute(
            "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )
        conn.commit()
    return raw


def _validate_reset_token(raw: str) -> tuple[dict | None, str]:
    if not raw:
        return None, "Enlace invalido."
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    with _auth_db() as conn:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ? AND used = 0",
            (token_hash,),
        ).fetchone()
    if not row:
        return None, "Enlace invalido o ya utilizado."
    expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        return None, "El enlace ha expirado. Solicita uno nuevo al administrador."
    user = _get_user_by_id(row["user_id"])
    if not user or not user["active"]:
        return None, "Usuario no encontrado o inactivo."
    return user, ""


def _consume_reset_token(raw: str) -> None:
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    with _auth_db() as conn:
        conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE token_hash = ?", (token_hash,))
        conn.commit()


def _set_session_user(user: dict) -> None:
    session.clear()
    session["user"] = user
    now = int(time.time())
    session["auth_at"] = now
    session["last_seen"] = now
    session["session_version"] = user.get("session_version")
    session["csrf_token"] = _sec.token_urlsafe(32)


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = _sec.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _is_localhost() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


def _safe_next_url(next_url: str | None) -> str:
    next_url = next_url or url_for("index")
    if not str(next_url).startswith("/") or str(next_url).startswith("//"):
        return url_for("index")
    return next_url


def _login_required_response(message: str):
    session.clear()
    _next = request.script_root + (request.full_path if request.query_string else request.path)
    login_url = url_for("login", next=_next)
    if request.path.startswith("/api/"):
        return jsonify({"error": message, "login_url": url_for("login")}), 401
    return redirect(login_url)


def _login_throttle_key(username: str) -> str:
    return f"{_client_ip()}:{(username or '').strip().lower()}"


def _login_is_throttled(username: str) -> bool:
    now = time.time()
    key = _login_throttle_key(username)
    attempts = [t for t in _login_failures.get(key, []) if now - t < _LOGIN_FAIL_WINDOW_SECONDS]
    _login_failures[key] = attempts
    return len(attempts) >= _LOGIN_FAIL_MAX


def _record_login_failure(username: str):
    key = _login_throttle_key(username)
    _login_failures.setdefault(key, []).append(time.time())


def _clear_login_failures(username: str):
    _login_failures.pop(_login_throttle_key(username), None)


def auth_status() -> dict:
    return {
        "mode": "local-login",
        "enabled": _local_user_count() > 0,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next_url(request.values.get("next"))
    first_setup = _local_user_count() == 0
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        provided_csrf = request.form.get("csrf_token", "")
        if not provided_csrf or not _sec.compare_digest(provided_csrf, _csrf_token()):
            error = "Sesion expirada o formulario invalido. Recarga la pagina e intenta de nuevo."
        elif _login_is_throttled(username):
            error = "Demasiados intentos fallidos. Espera unos minutos e intenta de nuevo."
            _log.warning("login_throttled ip=%s username=%s", _client_ip(), username)
        elif first_setup:
            confirm = request.form.get("password_confirm", "")
            if password != confirm:
                error = "Las contrasenas no coinciden."
            else:
                ok, error, user = _create_local_user(username, password, role=ADMIN_ROLE)
                if ok:
                    _clear_login_failures(username)
                    _set_session_user(user)
                    return redirect(next_url)
        elif (user := _local_credentials_user(username, password)):
            _clear_login_failures(username)
            _set_session_user(user)
            return redirect(next_url)
        elif not error:
            _record_login_failure(username)
            _log.warning("login_failed ip=%s username=%s", _client_ip(), username)
            error = "Usuario o contrasena incorrectos."
    return render_template(
        "login.html",
        next_url=next_url,
        login_error=error,
        first_setup=first_setup,
    )


@app.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "POST":
        provided = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if expected and (not provided or not _sec.compare_digest(provided, expected)):
            return jsonify({"error": "Token CSRF faltante o invalido."}), 403
        session.clear()
    else:
        # Evita logout por navegacion/preload GET. El boton real usa POST.
        if _session_user():
            return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.route("/avatars/<filename>")
def avatar_file(filename: str):
    if not re.match(r"^[A-Za-z0-9_.-]+$", filename or ""):
        return "Nombre de archivo invalido", 400
    path = AVATARS_DIR / filename
    if path.exists():
        response = send_from_directory(AVATARS_DIR, filename, conditional=False)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
    legacy_path = LEGACY_AVATARS_DIR / filename
    if legacy_path.exists():
        response = send_from_directory(LEGACY_AVATARS_DIR, filename, conditional=False)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
    return "Avatar no encontrado", 404


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email", ""),
        "avatar_url": user.get("avatar_url", ""),
        "role": user.get("role", USER_ROLE),
        "active": bool(user.get("active", True)),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "last_login_at": user.get("last_login_at"),
    }


def _record_run_owner(output_file: str, run_id: str | None, user: dict | None) -> None:
    if not output_file or not user:
        return
    with _auth_db() as conn:
        conn.execute(
            """
            INSERT INTO run_ownership (output_file, run_id, user_id, username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(output_file) DO UPDATE SET
                run_id = excluded.run_id,
                user_id = excluded.user_id,
                username = excluded.username
            """,
            (Path(output_file).name, run_id or "", user["id"], user["username"]),
        )
        conn.commit()


def _clear_runtime_checkpoint_files(
    include_run_state: bool = False,
    include_claim_results: bool = False,
) -> list[str]:
    return clear_runtime_checkpoints(
        include_run_state=include_run_state,
        include_claim_results=include_claim_results,
    )


def _output_owner(name: str) -> dict | None:
    with _auth_db() as conn:
        row = conn.execute(
            """
            SELECT ro.output_file, ro.run_id, ro.username, u.id, u.email, u.avatar_filename, u.role, u.active,
                   u.created_at, u.updated_at, u.last_login_at
            FROM run_ownership ro
            LEFT JOIN users u ON u.id = ro.user_id
            WHERE ro.output_file = ?
            """,
            (name,),
        ).fetchone()
    if not row:
        return None
    avatar_filename = row["avatar_filename"] or ""
    return {
        "id": int(row["id"]) if row["id"] is not None else None,
        "username": row["username"],
        "email": row["email"] or "",
        "avatar_filename": avatar_filename,
        "avatar_url": f"/avatars/{avatar_filename}" if avatar_filename else "",
        "role": row["role"] or USER_ROLE,
        "active": bool(row["active"]) if row["active"] is not None else False,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
    }


def _can_access_output(name: str) -> bool:
    if _current_user_is_admin():
        return True
    owner = _output_owner(name)
    return bool(owner and owner.get("id") == _current_user_id())


def _save_avatar_upload(file_storage, user_id: int) -> tuple[bool, str, str]:
    if not file_storage or not file_storage.filename:
        return True, "", ""
    original = secure_filename(file_storage.filename)
    ext = Path(original).suffix.lower()
    if ext not in AVATAR_EXTENSIONS:
        return False, "Formato de imagen no permitido.", ""
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > 2 * 1024 * 1024:
        return False, "La imagen no debe exceder 2 MB.", ""
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"user_{user_id}_{uuid.uuid4().hex[:12]}{ext}"
    file_storage.save(AVATARS_DIR / filename)
    return True, "", filename


@app.route("/profile")
def page_profile():
    return render_template("profile.html")


@app.route("/users")
def page_users():
    denied = _require_admin()
    if denied:
        return denied
    return render_template("users.html")


@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    user = _session_user()
    return jsonify(_public_user(user))


@app.route("/api/profile", methods=["PUT"])
def api_profile_update():
    user = _session_user()
    data = request.get_json() or {}
    username = (data.get("username") or user["username"]).strip()
    email = (data.get("email") or "").strip()
    ok, error = _validate_username(username)
    if not ok:
        return jsonify({"error": error}), 400
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    try:
        with _auth_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            if new_password:
                ok, error = _validate_password_strength(new_password)
                if not ok:
                    return jsonify({"error": error}), 400
                if not current_password or not check_password_hash(row["password_hash"], current_password):
                    return jsonify({"error": "Contrasena actual incorrecta."}), 400
                conn.execute(
                    "UPDATE users SET username = ?, email = ?, password_hash = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (username, email, generate_password_hash(new_password), user["id"]),
                )
            else:
                conn.execute(
                    "UPDATE users SET username = ?, email = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (username, email, user["id"]),
                )
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Ese usuario ya existe."}), 400
    updated = _get_user_by_id(user["id"])
    _set_session_user(updated)
    return jsonify(_public_user(updated))


@app.route("/api/profile/avatar", methods=["POST"])
def api_profile_avatar():
    user = _session_user()
    ok, error, filename = _save_avatar_upload(request.files.get("avatar"), user["id"])
    if not ok:
        return jsonify({"error": error}), 400
    if not filename:
        return jsonify({"error": "Archivo de imagen requerido."}), 400
    with _auth_db() as conn:
        old = conn.execute("SELECT avatar_filename FROM users WHERE id = ?", (user["id"],)).fetchone()
        conn.execute(
            "UPDATE users SET avatar_filename = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (filename, user["id"]),
        )
        conn.commit()
    if old and old["avatar_filename"]:
        old_path = AVATARS_DIR / old["avatar_filename"]
        if old_path.exists():
            old_path.unlink()
    updated = _get_user_by_id(user["id"])
    _set_session_user(updated)
    return jsonify(_public_user(updated))


@app.route("/api/users", methods=["GET"])
def api_users_list():
    denied = _require_admin()
    if denied:
        return denied
    with _auth_db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY active DESC, role ASC, username ASC").fetchall()
    return jsonify([_public_user(_row_to_user(row)) for row in rows])


@app.route("/api/users", methods=["POST"])
def api_users_create():
    denied = _require_admin()
    if denied:
        return denied
    data = request.get_json() or {}
    ok, error, user = _create_local_user(
        data.get("username", ""),
        data.get("password", ""),
        email=data.get("email", ""),
        role=USER_ROLE,
    )
    if not ok:
        return jsonify({"error": error}), 400
    _log.info("user_created admin=%s user=%s", _session_user()["username"], user["username"])
    return jsonify(_public_user(user)), 201


@app.route("/api/users/<int:user_id>", methods=["PUT"])
def api_users_update(user_id: int):
    denied = _require_admin()
    if denied:
        return denied
    data = request.get_json() or {}
    user = _get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "Usuario no encontrado."}), 404
    username = (data.get("username") or user["username"]).strip()
    email = (data.get("email") or "").strip()
    ok, error = _validate_username(username)
    if not ok:
        return jsonify({"error": error}), 400
    password = data.get("password") or ""
    active = 1 if bool(data.get("active", user["active"])) else 0
    if user["role"] == ADMIN_ROLE and active == 0:
        return jsonify({"error": "No puedes desactivar al admin. Transfiere admin primero."}), 400
    try:
        with _auth_db() as conn:
            if password:
                ok, error = _validate_password_strength(password)
                if not ok:
                    return jsonify({"error": error}), 400
                conn.execute(
                    "UPDATE users SET username = ?, email = ?, active = ?, password_hash = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (username, email, active, generate_password_hash(password), user_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET username = ?, email = ?, active = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (username, email, active, user_id),
                )
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Ese usuario ya existe."}), 400
    if user_id == _current_user_id():
        _set_session_user(_get_user_by_id(user_id))
    return jsonify(_public_user(_get_user_by_id(user_id)))


@app.route("/api/users/<int:user_id>/transfer-admin", methods=["POST"])
def api_users_transfer_admin(user_id: int):
    denied = _require_admin()
    if denied:
        return denied
    current = _session_user()
    target = _get_user_by_id(user_id)
    if not target or not target["active"]:
        return jsonify({"error": "El nuevo admin debe existir y estar activo."}), 400
    if target["id"] == current["id"]:
        return jsonify({"error": "Ese usuario ya es el admin actual."}), 400
    with _auth_db() as conn:
        conn.execute("UPDATE users SET role = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?", (USER_ROLE, current["id"]))
        conn.execute("UPDATE users SET role = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ADMIN_ROLE, target["id"]))
        conn.commit()
    _set_session_user(_get_user_by_id(current["id"]))
    _log.info("admin_transferred from=%s to=%s", current["username"], target["username"])
    return jsonify({"ok": True, "admin": _public_user(_get_user_by_id(target["id"]))})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
def api_users_disable(user_id: int):
    denied = _require_admin()
    if denied:
        return denied
    user = _get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "Usuario no encontrado."}), 404
    if user["role"] == ADMIN_ROLE:
        return jsonify({"error": "No puedes eliminar/desactivar al admin. Transfiere admin primero."}), 400
    with _auth_db() as conn:
        conn.execute("UPDATE users SET active = 0, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()
    _log.info("user_disabled admin=%s user=%s", _session_user()["username"], user["username"])
    return jsonify({"ok": True})


@app.route("/api/users/<int:user_id>/reset-link", methods=["POST"])
def api_users_reset_link(user_id: int):
    denied = _require_admin()
    if denied:
        return denied
    user = _get_user_by_id(user_id)
    if not user or not user["active"]:
        return jsonify({"error": "Usuario no encontrado o inactivo."}), 404
    raw = _generate_reset_token(user_id)
    reset_url = url_for("page_reset_password", token=raw, _external=True)
    _log.info("reset_link_generated admin=%s target=%s", _session_user()["username"], user["username"])
    return jsonify({"reset_url": reset_url})


@app.route("/forgot-password")
def page_forgot_password():
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def page_reset_password(token: str):
    user, token_error = _validate_reset_token(token)
    error = ""
    success = False
    if user and request.method == "POST":
        provided_csrf = request.form.get("csrf_token", "")
        if not provided_csrf or not _sec.compare_digest(provided_csrf, _csrf_token()):
            error = "Sesion expirada. Recarga la pagina."
        else:
            password = request.form.get("password", "")
            confirm = request.form.get("password_confirm", "")
            if password != confirm:
                error = "Las contrasenas no coinciden."
            else:
                ok, pw_error = _validate_password_strength(password)
                if not ok:
                    error = pw_error
                else:
                    with _auth_db() as conn:
                        conn.execute(
                            "UPDATE users SET password_hash = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (generate_password_hash(password), user["id"]),
                        )
                        conn.commit()
                    _consume_reset_token(token)
                    _log.info("password_reset_completed user=%s", user["username"])
                    success = True
    return render_template(
        "reset_password.html",
        token=token,
        reset_user=user,
        token_error=token_error,
        error=error,
        success=success,
    )


@app.context_processor
def _inject_csrf_token():
    return {
        "csrf_token": _csrf_token(),
        "current_user": _session_user(),
        "setup_values": {},
    }


@app.before_request
def _require_auth():
    path = request.path
    if path in _AUTH_EXEMPT or any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIX):
        return

    if _session_user():
        return

    return _login_required_response("Autenticacion requerida.")


@app.before_request
def _require_admin_for_project_management():
    path = request.path
    if path in _AUTH_EXEMPT or any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIX):
        return
    if path in _ADMIN_PAGE_PATHS or path in _ADMIN_API_EXACT or any(path.startswith(p) for p in _ADMIN_API_PREFIXES):
        if not _current_user_is_admin():
            return _admin_required_response()


# ---------------------------------------------------------------------------
# Hardware profile (no aborta la webapp si falta  -  solo lo logea)
# ---------------------------------------------------------------------------

@app.before_request
def _require_csrf_token():
    if request.method not in _CSRF_METHODS:
        return
    if request.path in {"/login", "/logout"}:
        return
    if request.path in _AUTH_EXEMPT or any(request.path.startswith(p) for p in _AUTH_EXEMPT_PREFIX):
        return
    provided = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not provided or not _sec.compare_digest(provided, expected):
        return jsonify({"error": "Token CSRF faltante o invalido."}), 403


def _load_hw_profile() -> dict:
    if not HW_PROFILE_PATH.exists():
        _log.warning(
            "models/hw_profile.json no encontrado. "
            "Los modelos locales GGUF no estaran disponibles. "
            "Ejecuta 'python setup_env.py' para habilitarlos."
        )
        return {}
    try:
        with open(HW_PROFILE_PATH, encoding="utf-8") as f:
            profile = json.load(f)
        if not profile.get("setup_ok"):
            _log.warning(
                "hw_profile.json indica setup_ok=false. "
                "Revisa logs/setup_*.log y vuelve a ejecutar 'python setup_env.py'."
            )
        return profile
    except Exception as exc:
        _log.warning("No se pudo leer hw_profile.json: %s", exc)
        return {}

_HW_PROFILE: dict = _load_hw_profile()

# ---------------------------------------------------------------------------
# Descripciones orientadas al problema por modelo Gemini
# ---------------------------------------------------------------------------
GEMINI_MODEL_DESCRIPTIONS = {
    "gemini-2.5-pro":               "Maximo razonamiento. Ideal para analisis matizado y profundo de persuasion.",
    "gemini-3.1-pro-preview":       "Siguiente generacion Pro. Razonamiento avanzado para deteccion compleja.",
    "gemini-3-pro-preview":         "Pro de nueva generacion. Muy alto razonamiento para analisis sutil.",
    "gemini-pro-latest":            "Ultima version Pro estable. Buena capacidad de razonamiento general.",
    "gemini-2.5-flash":             "Balance optimo calidad/velocidad. Recomendado para la mayoria de arbitros.",
    "gemini-3.5-flash":             "Flash de nueva generacion. Rapido con buen razonamiento contextual.",
    "gemini-3.1-flash-lite":        "Flash-Lite moderno. Rapido y eficiente para analisis estructurado.",
    "gemini-3-flash-preview":       "Flash de nueva generacion. Alta velocidad con capacidad de razonamiento.",
    "gemini-flash-latest":          "Ultima version Flash estable. Rapido y preciso para etiquetado masivo.",
    "gemini-2.0-flash":             "Flash rapido y versatil. Buena relacion velocidad/precision.",
    "gemini-2.0-flash-001":         "Version estable de 2.0 Flash. Comportamiento predecible en produccion.",
    "gemini-2.5-flash-lite":        "Version ligera de 2.5 Flash. Optimo para alto volumen con bajo costo.",
    "gemini-2.0-flash-lite":        "Version mas ligera. Maximo throughput, menor precision analitica.",
    "gemini-2.0-flash-lite-001":    "Flash-Lite estable. Para procesar grandes volumenes rapidamente.",
    "gemini-flash-lite-latest":     "Ultima Flash-Lite. Minimo costo por entrada, menor razonamiento.",
    "gemma-4-26b-a4b-it":           "Modelo abierto Gemma 26B. Buena capacidad para analisis textual.",
    "gemma-4-31b-it":               "Modelo abierto Gemma 31B. Mayor capacidad que 26B para razonamiento.",
}

ANTHROPIC_MODEL_DESCRIPTIONS = {
    "claude-opus-4-5":              "Maximo razonamiento de Anthropic. Analisis profundo y matizado.",
    "claude-opus-4-0":              "Opus de ultima generacion. Capacidad analitica excepcional.",
    "claude-sonnet-4-5":            "Balance ideal calidad/velocidad. Muy recomendado para arbitros.",
    "claude-sonnet-4-0":            "Sonnet de alta capacidad. Excelente comprension contextual.",
    "claude-haiku-4-5":             "Haiku rapido y eficiente. Ideal para alto volumen de entradas.",
    "claude-3-5-sonnet-20241022":   "Sonnet 3.5 estable. Excelente razonamiento y velocidad.",
    "claude-3-5-haiku-20241022":    "Haiku 3.5 rapido. Buen balance velocidad/precision.",
    "claude-3-opus-20240229":       "Opus 3 de alta capacidad. Muy buen razonamiento analitico.",
}

OPENAI_MODEL_DESCRIPTIONS = {
    "gpt-4o":               "GPT-4o multimodal. Maximo razonamiento y comprension contextual.",
    "gpt-4o-mini":          "GPT-4o Mini. Rapido y eficiente, buena precision para etiquetado.",
    "gpt-4-turbo":          "GPT-4 Turbo. Alta capacidad analitica con buen contexto.",
    "gpt-4":                "GPT-4 clasico. Razonamiento solido y confiable.",
    "gpt-3.5-turbo":        "GPT-3.5 Turbo. Rapido y economico, menor profundidad analitica.",
    "o1":                   "o1 con razonamiento extendido. Maximo para analisis complejo.",
    "o1-mini":              "o1-mini. Razonamiento avanzado en menor escala.",
    "o3-mini":              "o3-mini. Razonamiento de siguiente generacion, eficiente.",
}

# Cache por proveedor {provider_type: {data, ts}}
_models_cache: dict = {}
MODELS_CACHE_TTL = 300

# ---------------------------------------------------------------------------
# Estado global del proceso
# ---------------------------------------------------------------------------
METRIC_PREFIX = "__RUN_METRIC__ "


def _empty_current_run_stats() -> dict:
    return {
        "active": False,
        "finished": False,
        "run_id": None,
        "source_file": None,
        "output_path": None,
        "input_file": None,
        "config_file": None,
        "started_at": None,
        "updated_at": None,
        "completed_before_start": 0,
        "total_rows": 0,
        "dataset_total_rows": 0,
        "processed_rows": 0,
        "successful_rows": 0,
        "failed_rows": 0,
        "skipped_rows": 0,
        "queued_rows": 0,
        "pending_rows": 0,
        "total_tokens_global": 0,
        "total_latency_s": 0.0,
        "pause_kind": "",
        "pause_reason": "",
        "principles": [],
        "principle_counts": {},
        "checkpoint_every": 10,
        "arbiters": {},
        "last_claim": None,
        "_claim_statuses": {},
        "_queued_indices": set(),
    }


_process_state = {
    "running":   False,
    "pid":       None,
    "log_lines": [],
    "log_seq":   0,
    "run_log_path": None,
    "proc":      None,
    "current_stats": _empty_current_run_stats(),
    "stats_lock": threading.Lock(),
    "state_lock": threading.RLock(),
    "exit_code": None,
    "last_error": "",
}


def _public_current_stats(stats: dict = None) -> dict:
    stats = stats or _process_state["current_stats"]
    public = {k: v for k, v in stats.items() if not k.startswith("_")}
    processed_rows = int(public.get("processed_rows", 0) or 0)
    successful_rows = int(public.get("successful_rows", 0) or 0)
    total_tokens = int(public.get("total_tokens_global", 0) or 0)
    public["arbiters"] = {
        arb_id: {
            **arb_stats,
            "latency_avg_s": round(
                arb_stats.get("latency_total_s", 0) / arb_stats.get("calls", 1),
                3,
            ) if arb_stats.get("calls", 0) else 0,
            "latency_min_s": arb_stats.get("latency_min_s") or 0,
            "latency_max_s": arb_stats.get("latency_max_s") or 0,
            "latency_total_s": round(arb_stats.get("latency_total_s", 0), 3),
            "tokens_in":  arb_stats.get("prompt_tokens", 0),
            "tokens_out": arb_stats.get("completion_tokens", 0),
            "ok": arb_stats.get("calls", 0),
        }
        for arb_id, arb_stats in stats.get("arbiters", {}).items()
    }
    public["pending_rows"] = max(
        0,
        int(public.get("total_rows", 0) or 0) - processed_rows,
    )
    public["queued_rows"] = len(stats.get("_queued_indices", set()))
    public["total_latency_s"] = round(public.get("total_latency_s", 0) or 0, 3)
    public["tokens_per_processed_row"] = round(total_tokens / processed_rows) if processed_rows else 0
    public["tokens_per_successful_row"] = round(total_tokens / successful_rows) if successful_rows else 0
    return public


def _new_web_run_log_path() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return LOGS_DIR / f"web_run_{stamp}_{suffix}.log"


def _append_web_run_log_file(line: str):
    with _process_state["state_lock"]:
        path_value = _process_state.get("run_log_path")
    if not path_value:
        return
    try:
        path = Path(path_value)
        path.parent.mkdir(exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except Exception as exc:
        _run_log.warning("No se pudo escribir log de ejecucion %s: %s", path_value, exc)


_SENSITIVE_LOG_PATTERNS = [
    re.compile(r"(?i)\b(authorization)(\s*:\s*)(?:bearer|basic)?\s*[^\s,;]+"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|csrf)[\w .:-]*([=:]\s*)([^\s,;]+)"),
    re.compile(r"(?i)\b(OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|LABELFORGE_SECRET_KEY|LABELFORGE_CSRF_TOKEN)\b\s*=\s*[^\s,;]+"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/\-]+=*"),
]


def _sanitize_run_log_for_ui(line: str) -> str:
    """Redacta detalles operativos que no deben salir al navegador."""
    text = str(line or "")
    if text.startswith("Comando:"):
        return "Comando preparado. Detalles omitidos en la pantalla; quedan en la bitacora local del servidor."
    if text.startswith("Subprocess PID="):
        return "Subproceso de etiquetado iniciado."
    if text.startswith("[server-log]"):
        return "[server-log] Bitacora local del servidor activada para esta ejecucion."

    for pattern in _SENSITIVE_LOG_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2) if len(m.groups()) >= 2 else ' '}[REDACTADO]", text)

    base = re.escape(str(BASE_DIR))
    text = re.sub(base + r"[\\/][^\s\"']*", "[ruta-proyecto]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[A-Za-z]:\\[^\s\"']+", "[ruta-servidor]", text)
    text = re.sub(r"(?<!:)//?[A-Za-z0-9_.-]+(?:/[^\s\"']*)?", lambda m: m.group(0) if m.group(0).startswith(("http://", "https://")) else "[ruta-servidor]", text)
    return text


def _push_run_log(line: str, *, expose_ui: bool = True):
    line = str(line or "").strip()
    if not line:
        return
    _run_log.info(line)
    _append_web_run_log_file(line)
    if not expose_ui:
        return
    with _process_state["state_lock"]:
        _process_state.setdefault("log_lines", []).append(_sanitize_run_log_for_ui(line))
        _process_state["log_seq"] = int(_process_state.get("log_seq", 0) or 0) + 1
        if len(_process_state["log_lines"]) > 500:
            _process_state["log_lines"] = _process_state["log_lines"][-500:]


def _reset_current_stats():
    with _process_state["stats_lock"]:
        _process_state["current_stats"] = _empty_current_run_stats()


def _reset_runtime_state_for_new_run():
    with _process_state["state_lock"]:
        _process_state["run_log_path"] = str(_new_web_run_log_path())
        _process_state["log_lines"] = []
        _process_state["log_seq"] = 0
        _process_state["exit_code"] = None
        _process_state["last_error"] = ""
    _reset_current_stats()


def _runtime_snapshot(since: int = 0) -> dict:
    with _process_state["state_lock"]:
        seq = int(_process_state.get("log_seq", 0) or 0)
        lines = list(_process_state.get("log_lines", []))
        base_seq = max(0, seq - len(lines))
        start = max(0, int(since or 0) - base_seq)
        payload_lines = lines[start:]
        runtime = {
            "running": bool(_process_state.get("running")),
            "pid": _process_state.get("pid"),
            "seq": seq,
            "lines": payload_lines,
            "exit_code": _process_state.get("exit_code"),
            "last_error": _process_state.get("last_error", ""),
            "run_log_available": bool(_process_state.get("run_log_path")),
        }
    with _process_state["stats_lock"]:
        runtime["stats"] = _public_current_stats(_process_state["current_stats"])
    return runtime


def _ensure_live_arbiter(stats: dict, arb_id: str, name: str = None) -> dict:
    if arb_id not in stats["arbiters"]:
        stats["arbiters"][arb_id] = {
            "id": arb_id,
            "name": name or arb_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "latency_total_s": 0.0,
            "latency_min_s": None,
            "latency_max_s": None,
        }
    elif name:
        stats["arbiters"][arb_id]["name"] = name
    return stats["arbiters"][arb_id]


def _apply_initial_summary(stats: dict, summary: dict):
    if not summary:
        return
    for key in ["processed_rows", "successful_rows", "failed_rows", "skipped_rows",
                "total_tokens_global", "total_latency_s"]:
        stats[key] = summary.get(key, stats.get(key, 0))
    for arb_id, incoming in summary.get("arbiters", {}).items():
        arb_stats = _ensure_live_arbiter(stats, arb_id)
        for key in ["prompt_tokens", "completion_tokens", "total_tokens", "calls"]:
            arb_stats[key] = incoming.get(key, arb_stats.get(key, 0))
        arb_stats["latency_total_s"] = incoming.get("latency_total_s", 0) or 0
        arb_stats["latency_min_s"] = incoming.get("latency_min_s") or None
        arb_stats["latency_max_s"] = incoming.get("latency_max_s") or None


def _set_live_claim_status(stats: dict, row_index, status: str):
    key = str(row_index)
    old = stats["_claim_statuses"].get(key)
    if old == status:
        return
    counters = {
        "ok": "successful_rows",
        "failed": "failed_rows",
        "skipped": "skipped_rows",
    }
    if old in counters:
        stats[counters[old]] = max(0, stats.get(counters[old], 0) - 1)
    if status in counters:
        stats[counters[status]] = stats.get(counters[status], 0) + 1
    stats["_claim_statuses"][key] = status
    stats["processed_rows"] = (
        stats.get("completed_before_start", 0)
        + len(stats["_claim_statuses"])
    )


def _apply_run_metric_event(event: dict) -> dict:
    with _process_state["stats_lock"]:
        stats = _process_state["current_stats"]
        kind = event.get("event")
        stats["updated_at"] = event.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S")

        if kind == "start":
            stats = _empty_current_run_stats()
            _process_state["current_stats"] = stats
            stats["active"] = True
            stats["finished"] = False
            stats["run_id"] = event.get("run_id")
            stats["source_file"] = event.get("output_file")
            stats["output_path"] = event.get("output_path")
            stats["input_file"] = event.get("input_file")
            stats["config_file"] = event.get("config_file")
            stats["started_at"] = event.get("timestamp")
            stats["updated_at"] = event.get("timestamp")
            stats["total_rows"] = int(event.get("total_rows") or 0)
            stats["dataset_total_rows"] = int(event.get("dataset_total_rows") or 0)
            stats["completed_before_start"] = int(event.get("completed_before_start") or 0)
            stats["principles"] = event.get("principles", [])
            try:
                _cfg = read_config()
                stats["checkpoint_every"] = int(_cfg.get("processing", {}).get("checkpoint_every", 10))
            except Exception:
                pass
            for arb in event.get("arbiters", []):
                _ensure_live_arbiter(stats, arb.get("id"), arb.get("name"))
            _apply_initial_summary(stats, event.get("initial_summary", {}))

        elif kind == "claim_ok":
            _set_live_claim_status(stats, event.get("row_index"), "ok")
            stats["_queued_indices"].discard(str(event.get("row_index")))
            stats["total_tokens_global"] += int(event.get("total_tokens") or 0)
            if event.get("total_latency_s") is not None:
                stats["total_latency_s"] += float(event.get("total_latency_s") or 0)
            stats["last_claim"] = event
            for arb in event.get("arbiters", []):
                arb_stats = _ensure_live_arbiter(stats, arb.get("id"), arb.get("name"))
                arb_stats["prompt_tokens"] += int(arb.get("prompt_tokens") or 0)
                arb_stats["completion_tokens"] += int(arb.get("completion_tokens") or 0)
                arb_stats["total_tokens"] += int(arb.get("total_tokens") or 0)
                if arb.get("latency_s") is not None:
                    latency = float(arb.get("latency_s") or 0)
                    arb_stats["calls"] += 1
                    arb_stats["latency_total_s"] += latency
                    arb_stats["latency_min_s"] = latency if arb_stats["latency_min_s"] is None else min(arb_stats["latency_min_s"], latency)
                    arb_stats["latency_max_s"] = latency if arb_stats["latency_max_s"] is None else max(arb_stats["latency_max_s"], latency)
            for p, val in event.get("consensus", {}).items():
                pc = stats["principle_counts"]
                if p not in pc:
                    pc[p] = {"ones": 0, "zeros": 0}
                if val == 1:
                    pc[p]["ones"] += 1
                elif val == 0:
                    pc[p]["zeros"] += 1

        elif kind == "claim_start":
            stats["last_claim"] = event

        elif kind == "claim_failed":
            _set_live_claim_status(stats, event.get("row_index"), "failed")
            if event.get("queued"):
                stats["_queued_indices"].add(str(event.get("row_index")))
            stats["last_claim"] = event

        elif kind == "claim_skipped":
            _set_live_claim_status(stats, event.get("row_index"), "skipped")
            stats["last_claim"] = event

        elif kind == "output_written":
            stats["source_file"] = event.get("output_file") or stats.get("source_file")
            stats["output_path"] = event.get("output_path") or stats.get("output_path")

        elif kind == "complete":
            stats["active"] = False
            stats["finished"] = True
            stats["source_file"] = event.get("output_file") or stats.get("source_file")
            stats["output_path"] = event.get("output_path") or stats.get("output_path")
            if event.get("final_summary"):
                _apply_initial_summary(stats, event["final_summary"])

        elif kind == "paused":
            stats["active"] = False
            stats["finished"] = False
            stats["pause_kind"] = event.get("pause_kind", "")
            stats["pause_reason"] = event.get("pause_reason", "")

        return _public_current_stats(stats)


# ---------------------------------------------------------------------------
# Helpers de config
# ---------------------------------------------------------------------------
def read_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_config(cfg: dict):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_PATH)


# ---------------------------------------------------------------------------
# API: Modelos por proveedor
# ---------------------------------------------------------------------------

def _get_api_key(provider_type: str, override: str = "") -> str:
    if override:
        return override
    env_var = provider_api_key_env(provider_type)
    return os.environ.get(env_var, "") if env_var else ""


@app.route("/api/models", methods=["POST"])
def api_models():
    """
    Devuelve los modelos disponibles para un proveedor dado.
    Body JSON:
      - provider : gemini | vertex_gemini | anthropic | openai | ollama | openai_compat
      - api_key  : opcional, reemplazo directo de la clave en secrets/secrets.env
      - base_url : para ollama / openai_compat
      - project  : Google Cloud project para vertex_gemini
      - location : region Vertex AI para vertex_gemini
    """
    body     = request.get_json(silent=True) or {}
    provider = body.get("provider", "gemini")
    env_var  = (body.get("api_key_env") or "").strip() or provider_api_key_env(provider)
    override = (body.get("api_key") or "").strip()
    api_key  = override or (os.environ.get(env_var, "").strip() if env_var else "") or _get_api_key(provider)
    base_url = (body.get("base_url") or "").strip()
    project  = (body.get("project") or "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = (body.get("location") or "").strip() or os.environ.get("VERTEX_LOCATION", "us-central1").strip()
    force    = body.get("force") == "1"

    cache_key = f"{provider}:{env_var}:{base_url}:{project}:{location}"
    now = time.time()
    if not force and cache_key in _models_cache and (now - _models_cache[cache_key]["ts"]) < MODELS_CACHE_TTL:
        return jsonify(_models_cache[cache_key]["data"])

    try:
        if provider == "gemini":
            result = _list_gemini_models(api_key)
        elif provider == "vertex_gemini":
            result = _list_vertex_gemini_models(project, location)
        elif provider == "anthropic":
            result = _list_anthropic_models(api_key)
        elif provider == "openai":
            result = _list_openai_models(api_key)
        elif provider in ("ollama", "openai_compat"):
            result = _list_openai_compat_models(api_key, base_url)
        elif provider == "llama_cpp":
            result = _list_local_gguf_models()
        else:
            return jsonify({"error": f"Proveedor desconocido: {provider}"}), 400
    except Exception as e:
        _log.error(f"Error al listar modelos para {provider}: {e}", exc_info=True)
        msg = str(e)
        if "401" in msg or "authentication" in msg.lower() or "api_key" in msg.lower() or "invalid" in msg.lower():
            return jsonify({"error": f"Clave de API invalida o no configurada para {provider}. Verifica tu clave en los ajustes."}), 401
        return jsonify({"error": f"Error al listar modelos: {msg}"}), 502

    _models_cache[cache_key] = {"data": result, "ts": now}
    return jsonify(result)


@app.route("/api/provider-key", methods=["POST"])
def api_provider_key():
    data = request.get_json() or {}
    provider = data.get("provider", "").strip()
    api_key = data.get("api_key", "").strip()
    env_var = data.get("api_key_env", "").strip() or provider_api_key_env(provider)
    base_url = data.get("base_url", "").strip()

    if not _is_localhost():
        return jsonify({"error": "Guardar claves de API solo esta permitido desde localhost."}), 403
    if provider in {"ollama", "vertex_gemini"}:
        return jsonify({"error": "Este proveedor no usa clave de API directa en secrets/secrets.env."}), 400
    if not env_var:
        return jsonify({"error": "Proveedor sin variable de API key configurada."}), 400
    if not api_key:
        return jsonify({"error": "Pega una clave de API para verificarla."}), 400

    try:
        if provider == "gemini":
            models = _list_gemini_models(api_key)
        elif provider == "anthropic":
            models = _list_anthropic_models(api_key)
        elif provider == "openai":
            models = _list_openai_models(api_key)
        elif provider == "openai_compat":
            models = _list_openai_compat_models(api_key, base_url)
        else:
            return jsonify({"error": f"Proveedor desconocido: {provider}"}), 400
    except Exception as exc:
        logger.error(f"Error al verificar clave para {provider}: {exc}", exc_info=True)
        return jsonify({"error": "No se pudo verificar la clave."}), 400

    set_secret_value(SECRETS_ENV_PATH, env_var, api_key)
    load_dotenv(SECRETS_ENV_PATH, override=True)
    for key in list(_models_cache.keys()):
        if key.startswith(f"{provider}:"):
            _models_cache.pop(key, None)

    return jsonify({
        "ok": True,
        "provider": provider,
        "api_key_env": env_var,
        "models": models,
        "count": len(models),
    })


def _list_gemini_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere GEMINI_API_KEY en secrets/secrets.env o como parametro.")
    from google import genai as gai
    client = gai.Client(api_key=api_key)
    raw    = list(client.models.list())

    EXCLUDE = ["tts", "image", "robotics", "lyria", "computer-use",
               "deep-research", "antigravity", "nano-banana", "clip"]
    result  = []
    for m in raw:
        if "generateContent" not in (m.supported_actions or []):
            continue
        mid = m.name.replace("models/", "")
        if any(k in mid.lower() for k in EXCLUDE):
            continue
        desc = GEMINI_MODEL_DESCRIPTIONS.get(mid, _generic_desc(mid))
        result.append({"id": mid, "display_name": m.display_name or mid, "task_description": desc})

    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_vertex_gemini_models(project: str, location: str) -> list:
    if not project:
        raise ValueError("Se requiere GOOGLE_CLOUD_PROJECT o project para Vertex AI.")

    # Vertex AI no necesita API Key y se autentica con ADC / Service Account.
    # Mantenemos una lista curada de modelos generativos de texto para evitar
    # depender del endpoint de AI Studio al configurar arbitros Vertex.
    model_ids = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite-001",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ]
    result = [
        {"id": mid, "display_name": mid, "task_description": GEMINI_MODEL_DESCRIPTIONS.get(mid, _generic_desc(mid))}
        for mid in model_ids
    ]

    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_anthropic_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere ANTHROPIC_API_KEY en secrets/secrets.env o como parametro.")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    raw    = client.models.list(limit=50)
    result = []
    for m in raw.data:
        mid  = m.id
        desc = ANTHROPIC_MODEL_DESCRIPTIONS.get(mid, _generic_desc(mid))
        result.append({"id": mid, "display_name": m.display_name or mid, "task_description": desc})
    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_openai_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere OPENAI_API_KEY en secrets/secrets.env o como parametro.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    raw    = client.models.list()
    CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt")
    result = []
    for m in raw.data:
        if not any(m.id.startswith(p) for p in CHAT_PREFIXES):
            continue
        desc = OPENAI_MODEL_DESCRIPTIONS.get(m.id, _generic_desc(m.id))
        result.append({"id": m.id, "display_name": m.id, "task_description": desc})
    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _validate_base_url(base_url: str) -> None:
    """Block non-http(s) schemes and cloud metadata endpoints to prevent SSRF."""
    from urllib.parse import urlparse
    import ipaddress
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url debe usar esquema http o https.")
    host = (parsed.hostname or "").lower()
    _blocked_hosts = {"metadata.google.internal", "metadata.google"}
    if host in _blocked_hosts:
        raise ValueError(f"base_url apunta a un host no permitido: {host}")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_link_local:
            raise ValueError("base_url no puede apuntar a direcciones link-local (169.254.x.x).")
    except ValueError:
        pass  # hostname, not an IP literal  -  allowed


def _list_openai_compat_models(api_key: str, base_url: str) -> list:
    if not base_url:
        raise ValueError("Se requiere base_url para proveedor openai_compat / ollama.")
    _validate_base_url(base_url)
    from openai import OpenAI
    client = OpenAI(api_key=api_key or "local", base_url=base_url)
    raw    = client.models.list()
    result = [
        {"id": m.id, "display_name": m.id, "task_description": _generic_desc(m.id)}
        for m in raw.data
    ]
    result.sort(key=lambda m: m["id"])
    return result


def _list_local_gguf_models() -> list:
    """Lista los archivos .gguf disponibles en models/."""
    MODELS_DIR.mkdir(exist_ok=True)
    result = []
    for gguf in sorted(MODELS_DIR.glob("*.gguf")):
        size_gb = round(gguf.stat().st_size / (1024 ** 3), 2)
        result.append({
            "id": gguf.name,
            "display_name": gguf.stem,
            "task_description": f"Modelo local GGUF  -  {size_gb} GB",
            "path": str(gguf.relative_to(BASE_DIR)),
            "size_gb": size_gb,
        })
    return result


def _generic_desc(mid: str) -> str:
    if "opus" in mid or "pro" in mid:   return "Modelo de alta capacidad analitica."
    if "lite" in mid or "haiku" in mid: return "Modelo ligero. Alta velocidad, menor profundidad."
    if "flash" in mid or "sonnet" in mid or "mini" in mid: return "Balance velocidad/calidad."
    return "Modelo de proposito general."


def _tier_sort(mid: str) -> tuple:
    if "opus" in mid or ("pro" in mid and "lite" not in mid): tier = 0
    elif "lite" in mid or "haiku" in mid or "mini" in mid:    tier = 2
    else:                                                      tier = 1
    return (tier, mid)


# ---------------------------------------------------------------------------
# API: Rate limits por modelo
# ---------------------------------------------------------------------------
@app.route("/api/rate-limits", methods=["GET"])
def api_rate_limits():
    """
    Devuelve los limites de rate y el delay minimo recomendado para un modelo dado.
    Query params:
      - provider: proveedor (gemini, vertex_gemini, anthropic, openai, ...)
      - model   : id del modelo (ej: gemini-2.5-pro)
      - tier    : free | paid (default: free)
      - arbiters: numero de arbitros configurados (default: cuenta los del config actual)
    """
    provider = request.args.get("provider", "gemini").strip() or "gemini"
    model  = request.args.get("model", "").strip()
    tier   = request.args.get("tier", "free").strip()
    if tier not in ("free", "paid"):
        tier = "free"

    # Numero de arbitros activos
    try:
        n_arbiters = int(request.args.get("arbiters", 0))
        if n_arbiters <= 0:
            cfg        = read_config()
            n_arbiters = len(cfg["arbiters"])
    except (ValueError, TypeError):
        n_arbiters = 1

    if not model:
        # Devolver los limites del modelo mas restrictivo de los configurados (worst case)
        cfg    = read_config()
        providers_models = [
            (a.get("provider", {}).get("type", "gemini"), a.get("provider", {}).get("model", ""))
            for a in cfg["arbiters"]
            if a.get("provider", {}).get("model")
        ]
        if not providers_models:
            return jsonify({"error": "No hay arbitros con modelo configurado"}), 400

        # Calcular para cada modelo y devolver el mas restrictivo (menor RPM)
        results   = [compute_min_delay(p, m, tier, n_arbiters) for p, m in providers_models if m]
        worst     = min(results, key=lambda r: r["rpm"]) if results else None
        if not worst:
            return jsonify({"error": "Ningun modelo configurado tiene limites disponibles"}), 400
        worst["all_models"] = results
        return jsonify(worst)

    return jsonify(compute_min_delay(provider, model, tier, n_arbiters))


@app.route("/api/rate-limits/summary", methods=["GET"])
def api_rate_limits_summary():
    """
    Devuelve un resumen de limites para TODOS los arbitros configurados,
    con la recomendacion global de delay minimo (el mas restrictivo gana).
    Query params:
      - tier: free | paid (default: free)
    """
    tier = request.args.get("tier", "free").strip()
    if tier not in ("free", "paid"):
        tier = "free"

    cfg        = read_config()
    arbiters   = cfg["arbiters"]
    n_arbiters = len(arbiters)

    results = []
    for arb in arbiters:
        p     = arb.get("provider", {})
        model = p.get("model", "")
        ptype = p.get("type", "")
        if model:
            r = compute_min_delay(ptype, model, tier, n_arbiters)
            r["arb_id"]   = arb["id"]
            r["arb_name"] = arb["name"]
            results.append(r)
        else:
            results.append({
                "arb_id":      arb["id"],
                "arb_name":    arb["name"],
                "model":       model,
                "tier":        tier,
                "rpm":         None,
                "tpm":         None,
                "rpd":         None,
                "min_delay_s": None,
                "safe_delay_s": None,
                "alert_level": "info",
                "alert_msg":   f"Proveedor '{ptype}': sin modelo configurado.",
            })

    # Delay global recomendado = el mayor de los delays remotos. Los modelos
    # locales no tienen RPM de API; su cuello de botella es hardware/VRAM.
    provider_results = [
        r for r in results
        if r.get("min_delay_s") is not None and r.get("limit_kind") != "local"
    ]
    local_results = [r for r in results if r.get("limit_kind") == "local"]
    if provider_results:
        global_min_delay  = max(r["min_delay_s"]  for r in provider_results)
        global_safe_delay = max(r["safe_delay_s"] for r in provider_results)
        worst             = max(provider_results, key=lambda r: r["min_delay_s"])
        global_alert      = worst["alert_level"]
        global_msg        = (f"Espera minima global: {global_min_delay}s "
                             f"(limitado por {worst.get('provider')} / {worst['model']} a {worst['rpm']} RPM). "
                             f"Espera segura para produccion: {global_safe_delay}s.")
        if local_results:
            global_msg += " Los modelos locales no tienen limite RPM de API; dependen del hardware local."
    elif local_results:
        global_min_delay  = 0
        global_safe_delay = 0
        global_alert      = "info"
        names = ", ".join(f"{r.get('arb_id')} ({r.get('model')})" for r in local_results)
        global_msg        = (
            f"Procesamiento local GPU/CPU: sin limite RPM de API para {names}. "
            "La espera entre peticiones puede ser 0s; el rendimiento real depende de VRAM, capas offload, temperatura, contexto y concurrencia."
        )
    else:
        global_min_delay  = 1.5
        global_safe_delay = 3.0
        global_alert      = "info"
        global_msg        = "Sin arbitros con modelo configurado. Se usan valores por defecto."

    return jsonify({
        "tier":              tier,
        "n_arbiters":        n_arbiters,
        "global_min_delay":  global_min_delay,
        "global_safe_delay": global_safe_delay,
        "global_alert":      global_alert,
        "global_msg":        global_msg,
        "per_arbiter":       results,
    })


@app.route("/api/rate-limits/config", methods=["GET"])
def api_rate_limits_config_get():
    """Retorna la configuracion de rate limits desde config/rate_limits.json."""
    return jsonify(_rl_load_config())


@app.route("/api/rate-limits/config", methods=["PUT"])
def api_rate_limits_config_put():
    """Actualiza config/rate_limits.json con el JSON recibido."""
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"error": "JSON invalido o vacio"}), 400
    try:
        _rl_save_config(data)
    except ValueError as exc:
        logger.warning(f"Error de validacion al guardar config rate-limit: {exc}")
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error al guardar config rate-limit: {exc}", exc_info=True)
        return jsonify({"error": "Error al guardar la configuracion."}), 500
    return jsonify({"ok": True})


@app.route("/api/rate-limits/config/defaults", methods=["GET"])
def api_rate_limits_config_defaults():
    """Retorna los valores predeterminados de rate limits (sin modificar el archivo)."""
    return jsonify(_rl_get_defaults())


# ---------------------------------------------------------------------------
# API: Modelos locales GGUF
# ---------------------------------------------------------------------------

@app.route("/api/local/hardware", methods=["GET"])
def api_local_hardware():
    """Retorna hw_profile.json (leido fresco en cada llamada)."""
    return jsonify(_load_hw_profile())


@app.route("/api/local/recommended", methods=["GET"])
def api_local_recommended():
    """
    Retorna lista de modelos recomendados segun el hardware.
    Query param: ?refresh=1 para forzar nueva consulta a los LLMs.
    Siempre lee hw_profile.json fresco para reflejar cambios post-setup.
    """
    force = request.args.get("refresh") == "1"
    try:
        from labelforge_core.local_models import list_known_model_filenames, recommended_models
        hw = _load_hw_profile()
        result = recommended_models(hw if hw else None, force_refresh=force)
        downloaded_lower = {f.lower() for f in list_known_model_filenames()}
        result = [m for m in result if m.get("filename", "").lower() not in downloaded_lower]
        return jsonify(result)
    except Exception as exc:
        logger.error(f"Error al listar modelos recomendados: {exc}", exc_info=True)
        return jsonify({"error": "Error al obtener modelos recomendados."}), 500


@app.route("/api/local/cached", methods=["GET"])
def api_local_cached():
    """Lista los GGUFs descargados en models/."""
    try:
        from labelforge_core.local_models import list_cached_models
        return jsonify(list_cached_models())
    except Exception as exc:
        logger.error(f"Error al listar modelos cacheados: {exc}", exc_info=True)
        return jsonify({"error": "Error al obtener modelos cacheados."}), 500


_download_jobs: dict = {}
_download_jobs_lock = threading.Lock()


def _valid_gguf_filename(filename: str) -> bool:
    return (
        bool(filename)
        and filename.endswith(".gguf")
        and "/" not in filename
        and "\\" not in filename
        and ".." not in filename
    )


@app.route("/api/local/download", methods=["POST"])
def api_local_download():
    """
    Inicia descarga de un modelo GGUF en background.
    Body JSON: {repo_id, filename}
    Retorna: {job_id}
    """
    body = request.get_json(silent=True) or {}
    repo_id  = (body.get("repo_id") or "").strip()
    filename = (body.get("filename") or "").strip()
    description = (body.get("description") or "").strip()

    if not repo_id or not filename:
        return jsonify({"error": "repo_id y filename son obligatorios"}), 400
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400

    job_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{filename}"
    job = {
        "id": job_id,
        "repo_id": repo_id,
        "filename": filename,
        "status": "pending",
        "progress": 0,
        "error": None,
        "cancel_requested": False,
        "log_lines": [],
    }

    with _download_jobs_lock:
        for existing_id, existing in _download_jobs.items():
            if (
                existing.get("filename") == filename
                and existing.get("status") in ("pending", "downloading", "canceling")
            ):
                return jsonify({"job_id": existing_id, "already_running": True})
        _download_jobs[job_id] = job

    def _run():
        job["status"] = "downloading"
        try:
            from labelforge_core.local_models import DownloadCancelled, download_model

            def _progress(downloaded: int, total: int):
                if job.get("cancel_requested"):
                    raise DownloadCancelled("Descarga cancelada por el usuario.")
                if total:
                    job["progress"] = int(downloaded * 100 / total)

            download_model(repo_id, filename, progress_callback=_progress, description=description)
            if job.get("cancel_requested"):
                job["status"] = "canceled"
            else:
                job["status"] = "done"
                job["progress"] = 100
        except DownloadCancelled as exc:
            part = MODELS_DIR / f"{filename}.part"
            part.unlink(missing_ok=True)
            job["status"] = "canceled"
            job["error"] = str(exc)
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/api/local/download/<job_id>", methods=["GET"])
def api_local_download_status(job_id: str):
    """SSE de progreso de descarga."""
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
    if not job:
        return jsonify({"error": "job no encontrado"}), 404

    def generate():
        # Ping inicial: establece la conexion SSE antes de que llegue el primer dato
        yield ": ping\n\n"
        while True:
            with _download_jobs_lock:
                current = dict(_download_jobs.get(job_id, {}))
            data = json.dumps({
                "status": current.get("status"),
                "progress": current.get("progress", 0),
                "error": current.get("error"),
            })
            yield f"data: {data}\n\n"
            if current.get("status") in ("done", "error", "canceled"):
                break
            time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.route("/api/local/download/<job_id>/cancel", methods=["POST"])
def api_local_cancel_download(job_id: str):
    """Solicita cancelar una descarga GGUF en curso."""
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
        if not job:
            return jsonify({"error": "job no encontrado"}), 404
        if job.get("status") in ("done", "error", "canceled"):
            return jsonify({"ok": True, "status": job.get("status")})
        job["cancel_requested"] = True
        job["status"] = "canceling"
    return jsonify({"ok": True, "status": "canceling"})


@app.route("/api/local/model/<filename>", methods=["DELETE"])
def api_local_delete_model(filename: str):
    """Elimina un archivo GGUF del cache."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    target = MODELS_DIR / filename
    if not target.exists():
        return jsonify({"error": "Archivo no encontrado"}), 404
    target.unlink()
    _log.info("Modelo local eliminado: %s", filename)
    return jsonify({"ok": True, "deleted": filename})


@app.route("/api/local/model/<filename>/move", methods=["POST"])
def api_local_move_model(filename: str):
    """Mueve un GGUF activo a una carpeta absoluta elegida desde la maquina local."""
    if not _is_localhost():
        return jsonify({"error": "Mover modelos a rutas arbitrarias solo se permite desde localhost"}), 403
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400

    body = request.get_json(silent=True) or {}
    destination_dir = (body.get("destination_dir") or "").strip()
    if not destination_dir:
        return jsonify({"error": "destination_dir es obligatorio"}), 400

    try:
        from labelforge_core.local_models import move_model_to_directory
        destination = move_model_to_directory(filename, destination_dir)
        _log.info("Modelo local movido fuera del cache: %s -> %s", filename, destination)
        return jsonify({"ok": True, "moved": filename, "destination": str(destination)})
    except ValueError as exc:
        logger.warning(f"Error de validacion al mover modelo {filename}: {exc}")
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        logger.warning(f"Modelo no encontrado {filename}: {exc}")
        return jsonify({"error": "Modelo no encontrado."}), 404
    except FileExistsError as exc:
        logger.warning(f"El archivo ya existe al mover {filename}: {exc}")
        return jsonify({"error": "El archivo ya existe en el destino."}), 409
    except Exception as exc:
        logger.error(f"Error interno al mover modelo {filename}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al mover el modelo."}), 500


@app.route("/api/local/archive", methods=["GET"])
def api_local_archive():
    """Lista los GGUFs archivados en models/archive/."""
    try:
        from labelforge_core.local_models import list_archived_models
        return jsonify(list_archived_models())
    except Exception as exc:
        logger.error(f"Error al listar modelos archivados: {exc}", exc_info=True)
        return jsonify({"error": "Error al obtener modelos archivados."}), 500


@app.route("/api/local/model/<filename>/archive", methods=["POST"])
def api_local_archive_model(filename: str):
    """Mueve un GGUF descargado a models/archive/ para guardarlo fuera del cache activo."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    try:
        from labelforge_core.local_models import archive_model
        archive_model(filename)
        _log.info("Modelo local archivado: %s", filename)
        return jsonify({"ok": True, "archived": filename})
    except FileNotFoundError as exc:
        logger.warning(f"Modelo no encontrado para archivar {filename}: {exc}")
        return jsonify({"error": "Modelo no encontrado."}), 404
    except FileExistsError as exc:
        logger.warning(f"El archivo ya existe al archivar {filename}: {exc}")
        return jsonify({"error": "El archivo ya existe en el destino."}), 409
    except Exception as exc:
        logger.error(f"Error interno al archivar modelo {filename}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al archivar el modelo."}), 500


@app.route("/api/local/archive/<filename>/restore", methods=["POST"])
def api_local_restore_model(filename: str):
    """Restaura un GGUF desde models/archive/ hacia models/."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    try:
        from labelforge_core.local_models import restore_model
        restore_model(filename)
        _log.info("Modelo local restaurado: %s", filename)
        return jsonify({"ok": True, "restored": filename})
    except FileNotFoundError as exc:
        logger.warning(f"Modelo no encontrado para restaurar {filename}: {exc}")
        return jsonify({"error": "Modelo no encontrado."}), 404
    except FileExistsError as exc:
        logger.warning(f"El archivo ya existe al restaurar {filename}: {exc}")
        return jsonify({"error": "El archivo ya existe en el destino."}), 409
    except Exception as exc:
        logger.error(f"Error interno al restaurar modelo {filename}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al restaurar el modelo."}), 500


@app.route("/api/local/archive/<filename>", methods=["DELETE"])
def api_local_delete_archived_model(filename: str):
    """Elimina permanentemente un GGUF archivado."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    try:
        from labelforge_core.local_models import delete_archived_model
        delete_archived_model(filename)
        _log.info("Modelo local archivado eliminado: %s", filename)
        return jsonify({"ok": True, "deleted": filename})
    except FileNotFoundError as exc:
        logger.warning(f"Modelo archivado no encontrado {filename}: {exc}")
        return jsonify({"error": "Modelo no encontrado."}), 404
    except Exception as exc:
        logger.error(f"Error interno al eliminar modelo archivado {filename}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al eliminar el modelo."}), 500


@app.route("/api/local/external", methods=["GET"])
def api_local_external():
    """Lista los GGUFs registrados como movidos fuera del cache."""
    try:
        from labelforge_core.local_models import list_external_models
        return jsonify(list_external_models())
    except Exception as exc:
        logger.error(f"Error al listar modelos externos: {exc}", exc_info=True)
        return jsonify({"error": "Error al obtener modelos externos."}), 500


@app.route("/api/local/external/<filename>/restore", methods=["POST"])
def api_local_restore_external_model(filename: str):
    """Mueve un GGUF externo de vuelta a models/."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    try:
        from labelforge_core.local_models import restore_external_model
        restore_external_model(filename)
        _log.info("Modelo externo restaurado a cache: %s", filename)
        return jsonify({"ok": True, "restored": filename})
    except FileNotFoundError as exc:
        logger.warning(f"Modelo externo no encontrado {filename}: {exc}")
        return jsonify({"error": "Modelo no encontrado."}), 404
    except FileExistsError as exc:
        logger.warning(f"El archivo ya existe al restaurar externo {filename}: {exc}")
        return jsonify({"error": "El archivo ya existe en el destino."}), 409
    except Exception as exc:
        logger.error(f"Error interno al restaurar modelo externo {filename}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al restaurar el modelo."}), 500


@app.route("/api/local/external/<filename>", methods=["DELETE"])
def api_local_unregister_external_model(filename: str):
    """Quita un modelo externo del registro sin tocar el archivo."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    try:
        from labelforge_core.local_models import unregister_external_model
        unregister_external_model(filename)
        _log.info("Modelo externo desregistrado: %s", filename)
        return jsonify({"ok": True, "unregistered": filename})
    except FileNotFoundError as exc:
        logger.warning(f"Modelo externo no encontrado para desregistrar {filename}: {exc}")
        return jsonify({"error": "Modelo no encontrado."}), 404
    except Exception as exc:
        logger.error(f"Error interno al desregistrar modelo externo {filename}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al desregistrar el modelo."}), 500


# ---------------------------------------------------------------------------
# Rutas de paginas
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/arbiters")
def page_arbiters():
    return render_template("arbiters.html")

@app.route("/principles")
def page_principles():
    return render_template("principles.html")

@app.route("/processing")
def page_processing():
    return render_template("processing.html")

@app.route("/run")
def page_run():
    return render_template("run.html")


# ---------------------------------------------------------------------------
# API: Dataset de entrada (archivo, sheet, columna)
# ---------------------------------------------------------------------------
@app.route("/api/dataset/config", methods=["GET"])
def api_get_dataset_config():
    """Devuelve la configuracion actual del dataset de entrada."""
    cfg = read_config()
    dataset = cfg.get("dataset", {"input_file": "", "sheet": "", "text_column": ""})
    result = dict(dataset)
    input_file = result.get("input_file", "")
    if input_file:
        try:
            p = (BASE_DIR / input_file).resolve()
            st = p.stat()
            result["file_size_bytes"] = st.st_size
            result["uploaded_at"] = int(st.st_mtime)
        except OSError:
            pass
    return jsonify(result)


@app.route("/api/dataset/config", methods=["PUT"])
def api_update_dataset_config():
    """Guarda la configuracion del dataset (input_file, sheet, text_column)."""
    cfg  = read_config()
    data = request.get_json() or {}

    allowed = {"input_file": str, "sheet": str, "text_column": str}
    errors, updated = [], {}
    for key, cast in allowed.items():
        if key in data:
            try:
                updated[key] = cast(data[key])
            except Exception:
                errors.append(f"Valor invalido para '{key}'")
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    raw_input = updated.get("input_file", cfg.get("dataset", {}).get("input_file", ""))
    try:
        resolved = _resolve_allowed_dataset_path(raw_input)
    except PermissionError as exc:
        logger.warning(f"Permiso denegado para ruta de dataset {raw_input}: {exc}")
        return jsonify({"error": "Permiso denegado para acceder al archivo."}), 403
    except ValueError as exc:
        logger.warning(f"Ruta de dataset invalida {raw_input}: {exc}")
        return jsonify({"error": str(exc)}), 400
    if "input_file" in updated:
        updated["input_file"] = str(resolved.relative_to(BASE_DIR)).replace("\\", "/")

    if "dataset" not in cfg:
        cfg["dataset"] = {}
    cfg["dataset"].update(updated)
    write_config(cfg)
    return jsonify(cfg["dataset"])


@app.route("/api/dataset/inspect", methods=["POST"])
def api_dataset_inspect():
    """
    Inspecciona un archivo CSV o Excel y devuelve sus sheets y columnas.
    Body: {"file": "ruta/relativa/o/absoluta"}
    Respuesta: {"sheets": [...], "columns": [...], "is_excel": bool}
    """
    data = request.get_json() or {}
    raw_path = data.get("file", "").strip()
    if not raw_path:
        return jsonify({"error": "El campo de archivo es obligatorio"}), 400

    try:
        path = _resolve_allowed_dataset_path(raw_path)
    except PermissionError as exc:
        logger.warning(f"Permiso denegado para ruta de dataset {raw_path}: {exc}")
        return jsonify({"error": "Permiso denegado para acceder al archivo."}), 403
    except ValueError as exc:
        logger.warning(f"Ruta de dataset invalida {raw_path}: {exc}")
        return jsonify({"error": str(exc)}), 400

    if not path.exists():
        return jsonify({"error": "Archivo no encontrado."}), 404

    try:
        return jsonify(inspect_dataset(path, sheet=data.get("sheet", "")))
    except DatasetError as exc:
        logger.warning(f"Error de dataset en inspect {path}: {exc}")
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error interno al leer dataset {path}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al leer el dataset."}), 500


@app.route("/api/dataset/profile", methods=["POST"])
def api_dataset_profile():
    """
    Devuelve el perfil completo de un dataset: shape, tipos, fill rate, muestra,
    estadisticas numericas y vista previa de filas.
    Body: {"file": "ruta/relativa", "sheet": "opcional"}
    """
    data = request.get_json() or {}
    raw_path = data.get("file", "").strip()
    if not raw_path:
        return jsonify({"error": "El campo 'file' es obligatorio"}), 400
    try:
        path = _resolve_allowed_dataset_path(raw_path)
    except PermissionError as exc:
        logger.warning(f"Permiso denegado para ruta de dataset {raw_path}: {exc}")
        return jsonify({"error": "Permiso denegado para acceder al archivo."}), 403
    except ValueError as exc:
        logger.warning(f"Ruta de dataset invalida {raw_path}: {exc}")
        return jsonify({"error": str(exc)}), 400
    if not path.exists():
        return jsonify({"error": "Archivo no encontrado."}), 404
    try:
        return jsonify(profile_dataset(path, sheet=data.get("sheet", "")))
    except DatasetError as exc:
        logger.warning(f"Error de dataset en profile {path}: {exc}")
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error interno al perfilar dataset {path}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al perfilar el dataset."}), 500


@app.route("/api/dataset/upload", methods=["POST"])
def api_dataset_upload():
    """
    Recibe un archivo (CSV o Excel) via multipart/form-data,
    lo guarda en data/uploads/ y devuelve su ruta relativa + sheets + columnas.
    """
    if "file" not in request.files:
        return jsonify({"error": "No se recibio ningun archivo"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    from werkzeug.utils import secure_filename
    safe_name = secure_filename(f.filename)
    if not safe_name:
        return jsonify({"error": "Nombre de archivo no valido"}), 400

    suffix = Path(safe_name).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        return jsonify({"error": f"Formato '{suffix}' no soportado. Usa .csv, .xlsx o .xls."}), 400

    # Validar contenido real del archivo mediante magic bytes (pure Python, sin dependencias de sistema)
    f.stream.seek(0)
    file_header = f.stream.read(1024)
    f.stream.seek(0)
    if not _validate_upload_content(file_header, suffix):
        return jsonify({"error": f"El contenido del archivo no corresponde a un {suffix} valido."}), 400

    upload_dir = BASE_DIR / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    save_path = (upload_dir / safe_name).resolve()
    if not str(save_path).startswith(str(upload_dir.resolve())):
        return jsonify({"error": "Ruta de destino no permitida"}), 400
    f.save(str(save_path))

    rel_path = str(save_path.relative_to(BASE_DIR)).replace("\\", "/")
    stat = save_path.stat()

    try:
        profile = profile_dataset(save_path)
    except DatasetError as exc:
        logger.warning(f"Error de dataset en upload {save_path}: {exc}")
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error interno al leer dataset subido {save_path}: {exc}", exc_info=True)
        return jsonify({"error": "Error interno al procesar el archivo subido."}), 500
    return jsonify({
        **profile,
        "path": rel_path,
        "file_size_bytes": stat.st_size,
        "uploaded_at": int(stat.st_mtime),
    })


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(Exception)
def handle_unhandled_exception(exc):
    if isinstance(exc, HTTPException):
        return exc
    _log.error("Unhandled exception in request handler: %s", exc, exc_info=True)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Error interno del servidor."}), 500
    return "Error interno del servidor.", 500


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({"error": "El archivo es demasiado grande (máximo 300 MB)."}), 413


# ---------------------------------------------------------------------------
# API: Config completa
# ---------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(read_config())


# ---------------------------------------------------------------------------
# API: Arbitros (CRUD actualizado para estructura provider{})
# ---------------------------------------------------------------------------
@app.route("/api/arbiters", methods=["GET"])
def api_get_arbiters():
    return jsonify(read_config()["arbiters"])


def _preflight_status_by_arbiter(cfg: dict) -> dict:
    arbiters = cfg.get("arbiters", [])
    statuses = {
        a.get("id", ""): {
            "id": a.get("id", ""),
            "ok": True,
            "issues": [],
            "access_checked": False,
        }
        for a in arbiters
        if a.get("id")
    }
    global_issues = []

    for issue in validate_provider_preflight(cfg):
        matched = False
        for arb_id in statuses:
            if issue.startswith(f"{arb_id}:"):
                statuses[arb_id]["ok"] = False
                statuses[arb_id]["issues"].append(issue)
                matched = True
        vertex_match = re.search(r"Vertex AI \(([^)]+)\):", issue)
        if vertex_match:
            for arb_id in [x.strip() for x in vertex_match.group(1).split(",")]:
                if arb_id in statuses:
                    statuses[arb_id]["ok"] = False
                    statuses[arb_id]["issues"].append(issue)
                    matched = True
        if not matched:
            global_issues.append(issue)

    for arb in arbiters:
        arb_id = arb.get("id", "")
        status = statuses.get(arb_id)
        if not status or not status["ok"]:
            continue
        provider = arb.get("provider", {})
        ptype = provider.get("type", "")
        model = provider.get("model", "")
        try:
            if ptype == "gemini":
                models = _list_gemini_models(_get_api_key("gemini"))
            elif ptype == "anthropic":
                models = _list_anthropic_models(_get_api_key("anthropic"))
            elif ptype == "openai":
                models = _list_openai_models(_get_api_key("openai"))
            elif ptype in {"ollama", "openai_compat"}:
                models = _list_openai_compat_models(_get_api_key(ptype), provider.get("base_url", ""))
            elif ptype == "vertex_gemini":
                # validate_provider_preflight already checks ADC/project. Vertex model discovery
                # is curated locally to avoid extra provider calls in this screen.
                models = _list_vertex_gemini_models(
                    provider.get("vertex_project", "") or os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
                    provider.get("vertex_location", "") or os.environ.get("VERTEX_LOCATION", "us-central1"),
                )
            else:
                models = []
            status["access_checked"] = True
            ids = {m.get("id") for m in models if isinstance(m, dict)}
            if model and ids and model not in ids:
                status["ok"] = False
                status["issues"].append(
                    f"{arb_id}: el modelo seleccionado '{model}' no aparece en la lista disponible de {ptype}."
                )
        except Exception as exc:
            status["ok"] = False
            status["issues"].append(
                f"{arb_id}: no se pudo verificar acceso a {ptype}: {str(exc)[:260]}"
            )

    return {
        "ok": not global_issues and all(s["ok"] for s in statuses.values()),
        "issues": global_issues + [
            issue
            for status in statuses.values()
            for issue in status["issues"]
        ],
        "arbiters": list(statuses.values()),
    }


@app.route("/api/arbiters/preflight", methods=["GET"])
def api_arbiters_preflight():
    """Estado de configuracion/credenciales de arbitros antes de ejecutar."""
    return jsonify(_preflight_status_by_arbiter(read_config()))


@app.route("/api/arbiters", methods=["POST"])
def api_create_arbiter():
    cfg  = read_config()
    data = request.get_json()

    for key in ["id", "name", "provider", "system_prompt"]:
        if not data.get(key):
            return jsonify({"error": f"Campo requerido: {key}"}), 400

    if data["id"] in {a["id"] for a in cfg["arbiters"]}:
        return jsonify({"error": f"Ya existe un arbitro con id '{data['id']}'"}), 409

    new_arb = _build_arbiter(data)
    cfg["arbiters"].append(new_arb)
    write_config(cfg)
    return jsonify(new_arb), 201


@app.route("/api/arbiters/<arb_id>", methods=["GET"])
def api_get_arbiter(arb_id):
    for arb in read_config()["arbiters"]:
        if arb["id"] == arb_id:
            return jsonify(arb)
    return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404


@app.route("/api/arbiters/<arb_id>/hw-check", methods=["GET"])
def api_arbiter_hw_check(arb_id):
    """Verifica si el modelo del arbitro es adecuado para el hardware local."""
    arb = next((a for a in read_config()["arbiters"] if a["id"] == arb_id), None)
    if not arb:
        return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404

    prov = arb.get("provider", {})
    prov_type = prov.get("type", "gemini")

    CLOUD_TYPES = {"gemini", "anthropic", "openai", "vertex_gemini"}

    is_local_compat = False
    if prov_type == "openai_compat":
        base_url = prov.get("base_url", "")
        is_local_compat = any(h in base_url for h in ("localhost", "127.0.0.1", "::1", "0.0.0.0"))

    if prov_type in CLOUD_TYPES or (prov_type == "openai_compat" and not is_local_compat):
        return jsonify({
            "is_local": False,
            "provider_type": prov_type,
            "model": prov.get("model", ""),
        })

    hw = _load_hw_profile()
    gpu = hw.get("gpu", {})
    cpu = hw.get("cpu", {})

    result = {
        "is_local": True,
        "provider_type": prov_type,
        "model": prov.get("model", ""),
        "no_hw_profile": not hw,
        "hw": {
            "ram_gb": hw.get("ram_gb"),
            "gpu_name": gpu.get("name"),
            "vram_gb": gpu.get("vram_gb"),
            "gpu_detected": gpu.get("detected", False),
            "gpu_shared": gpu.get("shared_memory", False),
            "cpu_model": cpu.get("model"),
            "cpu_cores": cpu.get("cores_physical"),
            "best_backend": hw.get("best_backend", "cpu"),
        },
    }

    if prov_type == "llama_cpp":
        from labelforge_core.local_models import get_optimal_params
        model_path_rel = prov.get("model_path", "")
        if not model_path_rel:
            result["fit"] = "no_model_path"
            result["fit_msg"] = "El arbitro no tiene model_path configurado."
            return jsonify(result)

        mpath = Path(model_path_rel)
        if not mpath.is_absolute():
            mpath = BASE_DIR / model_path_rel

        result["model_path"] = model_path_rel
        result["model_exists"] = mpath.exists()

        if not mpath.exists():
            result["fit"] = "missing"
            result["fit_msg"] = f"El archivo del modelo no se encontro: {model_path_rel}"
        elif not hw:
            result["fit"] = "no_hw_profile"
            result["fit_msg"] = "No hay perfil de hardware (hw_profile.json). Ejecuta 'python setup_env.py'."
        else:
            from labelforge_core.local_models import get_optimal_params, list_cached_models, recommended_models
            size_gb = round(mpath.stat().st_size / (1024 ** 3), 2)
            result["model_size_gb"] = size_gb
            n_ctx = int(prov.get("n_ctx") or 4096)
            optimal = get_optimal_params(str(mpath), n_ctx=n_ctx, hw_profile=hw)
            result["optimal_params"] = optimal

            n_gpu    = optimal["n_gpu_layers"]
            vram_gb  = gpu.get("vram_gb") or 0
            ram_gb   = hw.get("ram_gb") or 0
            gpu_name = gpu.get("name") or "GPU desconocida"
            shared   = gpu.get("shared_memory", False)

            # Memoria efectiva (misma logica que get_optimal_params)
            if gpu.get("detected") and vram_gb:
                effective_mem = vram_gb * (0.75 if shared else 0.90)
            else:
                effective_mem = ram_gb * 0.6

            utilization_pct = round(size_gb / effective_mem * 100, 1) if effective_mem else None
            result["utilization_pct"] = utilization_pct
            result["effective_mem_gb"] = round(effective_mem, 1)

            if n_gpu == -1:
                result["fit"] = "full_gpu"
                result["fit_msg"] = (
                    f"El modelo cabe completamente en VRAM. "
                    f"GPU: {gpu_name} ({vram_gb} GB VRAM), modelo: {size_gb} GB."
                )
            elif n_gpu > 0:
                result["fit"] = "partial_gpu"
                result["fit_msg"] = (
                    f"Offload parcial a GPU: {n_gpu} capas en GPU, el resto en RAM. "
                    f"GPU: {gpu_name} ({vram_gb} GB VRAM), modelo: {size_gb} GB. "
                    "Un modelo mas pequeno mejoraria la velocidad."
                )
            elif not gpu.get("detected"):
                result["fit"] = "cpu_only"
                result["fit_msg"] = (
                    f"Sin GPU detectada. El modelo corre en CPU "
                    f"({cpu.get('cores_physical', '?')} nucleos fisicos). "
                    "Espera tiempos de inferencia altos."
                )
            else:
                result["fit"] = "cpu_fallback"
                result["fit_msg"] = (
                    f"El modelo ({size_gb} GB) supera la VRAM disponible ({vram_gb} GB). "
                    "Se ejecutara en CPU. Considera un modelo mas pequeno para aprovechar la GPU."
                )

            # Sugerencias: modelos del catalogo que caben en el HW y son mas capaces
            try:
                cached_filenames = {m["filename"].lower() for m in list_cached_models()}
                all_recs = recommended_models(hw_profile=hw)
                # Candidatos: caben en memoria y son al menos 1.5x mas grandes que el actual
                candidates = [
                    m for m in all_recs
                    if m.get("fits_in_memory") is True and m["size_gb"] > size_gb * 1.5
                ]
                # Ordenar por tamano desc (mas capaz primero)
                candidates.sort(key=lambda m: m["size_gb"], reverse=True)
                suggestions = []
                for m in candidates[:4]:
                    suggestions.append({
                        "filename":    m["filename"],
                        "repo_id":     m.get("repo_id", ""),
                        "size_gb":     m["size_gb"],
                        "description": m.get("description", ""),
                        "tier":        m.get("tier", ""),
                        "downloaded":  m["filename"].lower() in cached_filenames,
                    })
                result["suggestions"] = suggestions
            except Exception:
                result["suggestions"] = []

    elif prov_type == "ollama":
        if not hw:
            result["fit"] = "no_hw_profile"
            result["fit_msg"] = "No hay perfil de hardware. Ejecuta 'python setup_env.py'."
        else:
            ram_gb = hw.get("ram_gb", 0)
            vram_gb = gpu.get("vram_gb") or 0
            gpu_name = gpu.get("name") or "ninguna"
            if gpu.get("detected"):
                result["fit"] = "ollama_gpu"
                result["fit_msg"] = (
                    f"Ollama gestiona la VRAM automaticamente. "
                    f"GPU detectada: {gpu_name}"
                    + (f" ({vram_gb} GB VRAM)" if vram_gb else "")
                    + f", {ram_gb} GB RAM del sistema."
                )
            else:
                result["fit"] = "ollama_cpu"
                result["fit_msg"] = (
                    f"Sin GPU detectada. Ollama ejecutara el modelo en CPU. "
                    f"RAM del sistema: {ram_gb} GB."
                )

    else:  # openai_compat local
        base_url = prov.get("base_url", "")
        result["base_url"] = base_url
        if hw:
            ram_gb = hw.get("ram_gb", 0)
            gpu_name = gpu.get("name") or "ninguna"
            vram_gb = gpu.get("vram_gb") or 0
            result["fit"] = "compat_local"
            result["fit_msg"] = (
                f"API compatible con OpenAI en {base_url}. "
                f"Hardware local: {gpu_name}"
                + (f" ({vram_gb} GB VRAM)" if vram_gb else "")
                + f", {ram_gb} GB RAM."
            )
        else:
            result["fit"] = "no_hw_profile"
            result["fit_msg"] = "No hay perfil de hardware. Ejecuta 'python setup_env.py'."

    return jsonify(result)


@app.route("/api/arbiters/<arb_id>", methods=["PUT"])
def api_update_arbiter(arb_id):
    cfg  = read_config()
    data = request.get_json()
    for i, arb in enumerate(cfg["arbiters"]):
        if arb["id"] == arb_id:
            cfg["arbiters"][i] = _build_arbiter(data, existing=arb)
            write_config(cfg)
            return jsonify(cfg["arbiters"][i])
    return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404


@app.route("/api/arbiters/<arb_id>", methods=["DELETE"])
def api_delete_arbiter(arb_id):
    cfg = read_config()
    before = len(cfg["arbiters"])
    cfg["arbiters"] = [a for a in cfg["arbiters"] if a["id"] != arb_id]
    if len(cfg["arbiters"]) == before:
        return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404
    write_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/arbiters/export", methods=["POST"])
def api_export_arbiters():
    data = request.get_json() or {}
    try:
        password = require_export_password(data.get("password", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cfg = read_config()
    payload = build_arbiters_export_payload(cfg.get("arbiters", []))
    encrypted = encrypt_arbiters_export(payload, password)
    filename = f"labelforge-arbiters-{time.strftime('%Y%m%d-%H%M%S')}.lfarb"
    return Response(
        encrypted,
        mimetype="application/vnd.labelforge.arbiters+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/arbiters/import", methods=["POST"])
def api_import_arbiters():
    password = request.form.get("password", "")
    mode = request.form.get("mode", "merge")
    if mode not in {"merge", "replace"}:
        return jsonify({"error": "Modo de importacion invalido."}), 400
    if "file" not in request.files:
        return jsonify({"error": "Selecciona un archivo de exportacion."}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Selecciona un archivo de exportacion."}), 400

    try:
        password = require_export_password(password)
        payload = decrypt_arbiters_export(f.read(), password)
        imported = validate_imported_arbiters(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cfg = read_config()
    cfg["arbiters"], stats = merge_arbiters(cfg.get("arbiters", []), imported, mode)

    write_config(cfg)
    return jsonify({
        "ok": True,
        **stats,
    })


def _build_arbiter(data: dict, existing: dict = None) -> dict:
    """Construye un dict de arbitro limpio desde los datos del request."""
    return normalize_arbiter(data, existing=existing)


# ---------------------------------------------------------------------------
# API: Principios
# ---------------------------------------------------------------------------
@app.route("/api/principles", methods=["GET"])
def api_get_principles():
    return jsonify(read_config()["task"]["principles"])

@app.route("/api/principles", methods=["PUT"])
def api_update_principles():
    cfg  = read_config()
    data = request.get_json()
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "Se esperaba un objeto JSON"}), 400
    cfg["task"]["principles"] = data
    write_config(cfg)
    return jsonify(cfg["task"]["principles"])


# ---------------------------------------------------------------------------
# API: Prompt template
# ---------------------------------------------------------------------------
@app.route("/api/prompt-template", methods=["GET"])
def api_get_prompt_template():
    task = read_config()["task"]
    return jsonify({
        "user_prompt_template": task["user_prompt_template"],
        "include_justifications": bool(task.get("include_justifications", False)),
    })

@app.route("/api/prompt-template", methods=["PUT"])
def api_update_prompt_template():
    cfg      = read_config()
    data     = request.get_json()
    template = data.get("user_prompt_template", "").strip()
    if not template:
        return jsonify({"error": "El template no puede estar vacio"}), 400
    if "{item}" not in template:
        return jsonify({"error": "El template debe contener {item}, que representa el texto de la entrada"}), 400
    cfg["task"]["user_prompt_template"] = template
    if "include_justifications" in data:
        cfg["task"]["include_justifications"] = bool(data.get("include_justifications"))
    write_config(cfg)
    return jsonify({
        "user_prompt_template": template,
        "include_justifications": bool(cfg["task"].get("include_justifications", False)),
    })


# ---------------------------------------------------------------------------
# API: Processing params
# ---------------------------------------------------------------------------
@app.route("/api/processing", methods=["GET"])
def api_get_processing():
    return jsonify(read_config()["processing"])

@app.route("/api/processing", methods=["PUT"])
def api_update_processing():
    cfg     = read_config()
    data    = request.get_json()
    allowed = {
        "checkpoint_every": int, "claim_max_chars": int,
        "base_delay_seconds": float, "min_delay_seconds": float, "max_delay_seconds": float,
        "rate_limit_backoff_multiplier": float, "server_error_backoff_multiplier": float,
        "recovery_threshold_successes": int, "recovery_delay_multiplier": float,
    }
    errors, updated = [], {}
    for key, cast in allowed.items():
        if key in data:
            try:    updated[key] = cast(data[key])
            except: errors.append(f"Valor invalido para '{key}'")
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    if "base_delay_seconds" in updated:
        n_arbiters = len(cfg.get("arbiters", [])) or 1
        provider_delays = []
        for arb in cfg.get("arbiters", []):
            provider = arb.get("provider", {})
            if provider.get("model"):
                delay_info = compute_min_delay(
                    provider.get("type", "gemini"),
                    provider["model"],
                    "free",
                    n_arbiters,
                )
                if delay_info.get("limit_kind") != "local":
                    provider_delays.append(delay_info)
        if provider_delays:
            min_delay = max(r["min_delay_s"] for r in provider_delays)
            if updated["base_delay_seconds"] < min_delay:
                worst = max(provider_delays, key=lambda r: r["min_delay_s"])
                return jsonify({
                    "error": (f"La espera base es demasiado baja para los limites del proveedor/modelo. "
                              f"Minimo recomendado: {min_delay}s "
                              f"(limitado por {worst.get('provider')} / {worst['model']} a {worst['rpm']} RPM)."),
                    "min_delay_seconds": min_delay,
                    "safe_delay_seconds": max(r["safe_delay_s"] for r in provider_delays),
                }), 400

    cfg["processing"].update(updated)
    write_config(cfg)
    return jsonify(cfg["processing"])


# ---------------------------------------------------------------------------
# API: Lanzar proceso
# ---------------------------------------------------------------------------
@app.route("/api/run/start", methods=["POST"])
def api_run_start():
    app.logger.info("[run/start] Solicitud recibida")

    with _process_state["state_lock"]:
        if _process_state["running"]:
            app.logger.info("[run/start] Ya hay un proceso corriendo -> 409")
            return jsonify({"error": "Ya hay un proceso corriendo"}), 409
        _process_state["running"] = True  # claim slot atomically before releasing lock

    data  = request.get_json() or {}
    limit = data.get("limit")
    run_user = _session_user()
    app.logger.info("[run/start] Usuario: %s | limit: %s", run_user, limit)

    try:
        cfg_data = read_config()
    except Exception as exc:
        app.logger.error("[run/start] Error leyendo config: %s", exc)
        with _process_state["state_lock"]:
            _process_state["running"] = False
        return jsonify({"error": f"Error leyendo configuracion: {exc}"}), 500

    try:
        preflight_issues = validate_provider_preflight(cfg_data)
    except Exception as exc:
        app.logger.error("[run/start] Error en preflight: %s", exc)
        with _process_state["state_lock"]:
            _process_state["running"] = False
        return jsonify({"error": f"Error en preflight: {exc}"}), 500

    if preflight_issues:
        app.logger.info("[run/start] Preflight fallo: %s", preflight_issues)
        with _process_state["state_lock"]:
            _process_state["running"] = False
        return jsonify({
            "error": "No se puede iniciar: hay problemas de credenciales o proveedores.",
            "issues": preflight_issues,
        }), 400

    dataset_cfg = cfg_data.get("dataset", {})
    input_file  = dataset_cfg.get("input_file", "").strip()
    sheet       = dataset_cfg.get("sheet", "")
    text_column = dataset_cfg.get("text_column", "").strip()

    def _abort_run(message: str, status: int = 400):
        with _process_state["state_lock"]:
            _process_state["running"] = False
        return jsonify({"error": message}), status

    if not input_file:
        return _abort_run(
            "No hay dataset configurado. Ve a Procesamiento, sube un archivo y pulsa Guardar."
        )
    if not text_column:
        return _abort_run(
            "No hay columna de texto configurada. Ve a Procesamiento y selecciona la columna."
        )
    try:
        resolved_input = _resolve_allowed_dataset_path(input_file)
        if not resolved_input.exists():
            return _abort_run(
                f"El archivo '{Path(input_file).name}' no existe en el servidor. Vuelve a subirlo en Procesamiento."
            )
    except (PermissionError, ValueError) as exc:
        return _abort_run(str(exc))

    cmd = [sys.executable, "-u", str(BASE_DIR / "main_cli.py")]
    cmd += ["--input", input_file]
    if sheet:
        cmd += ["--sheet", sheet]
    cmd += ["--column", text_column]
    if limit:
        cmd += ["--limit", str(int(limit))]

    app.logger.info("[run/start] CMD: %s", " ".join(cmd))

    _reset_runtime_state_for_new_run()
    _push_run_log("--- Solicitud recibida: preparando ejecucion ---")
    _push_run_log(f"Usuario: {run_user.get('username') if run_user else '-'}")
    _push_run_log(f"Comando: {' '.join(cmd)}")
    def run_and_capture():
        try:
            env = os.environ.copy()
            env["DATA_LABELING_WEBAPP_METRICS"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            app.logger.info("[run/start] Lanzando subprocess...")
            _push_run_log("--- Lanzando proceso de etiquetado ---")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=str(BASE_DIR), env=env,
            )
            with _process_state["state_lock"]:
                _process_state["proc"] = proc
                _process_state["pid"]  = proc.pid
            app.logger.info("[run/start] Subprocess PID=%s", proc.pid)
            _push_run_log(f"Subprocess PID={proc.pid}")
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith(METRIC_PREFIX):
                    try:
                        event = json.loads(line[len(METRIC_PREFIX):])
                        metric_line = json.dumps(event, ensure_ascii=False, sort_keys=True)
                        _push_run_log(f"[metric] {metric_line}", expose_ui=False)
                        if event.get("event") in {"start", "claim_start", "claim_ok", "claim_failed", "claim_skipped", "pause", "complete", "output_written"}:
                            _push_run_log(f"[metric:{event.get('event')}] row={event.get('row_index', '-')} stage={event.get('stage', '-')} tokens={event.get('total_tokens', '-')} latency={event.get('total_latency_s', '-')}")
                        if event.get("event") in {"output_written", "completed"}:
                            _record_run_owner(event.get("output_file"), event.get("run_id"), run_user)
                        _apply_run_metric_event(event)
                    except json.JSONDecodeError:
                        _push_run_log(line)
                    continue
                _push_run_log(line)
            proc.stdout.close()
            proc.wait()
            exit_msg = f"--- Proceso finalizado (codigo {proc.returncode}) ---"
            app.logger.info("[run/start] %s", exit_msg)
            _push_run_log(exit_msg)
            with _process_state["state_lock"]:
                _process_state["exit_code"] = proc.returncode
        except Exception as e:
            app.logger.error("[run/start] Excepcion en subprocess: %s", e)
            err_msg = "--- Error en proceso ---"
            _push_run_log(err_msg)
            with _process_state["state_lock"]:
                _process_state["exit_code"] = "ERROR"
                # No exponer detalles internos del error al cliente
                _process_state["last_error"] = "Error interno en el proceso de etiquetado"
        finally:
            with _process_state["state_lock"]:
                _process_state["running"] = False
                _process_state["proc"]    = None

    threading.Thread(target=run_and_capture, daemon=True).start()
    app.logger.info("[run/start] Thread iniciado -> respondiendo OK")
    return jsonify({"ok": True})


@app.route("/api/run/stop", methods=["POST"])
def api_run_stop():
    with _process_state["state_lock"]:
        proc    = _process_state.get("proc")
        running = _process_state["running"]
    if proc and running:
        try:
            proc.terminate()
        except OSError:
            pass
        with _process_state["state_lock"]:
            _process_state["running"] = False
            _process_state["exit_code"] = "STOPPED"
        _push_run_log("--- Proceso detenido por el usuario ---")
        return jsonify({"ok": True})
    return jsonify({"error": "No hay proceso activo"}), 400


@app.route("/api/run/status", methods=["GET"])
def api_run_status():
    snapshot = _runtime_snapshot(0)
    return jsonify({
        "running": snapshot["running"],
        "pid": snapshot["pid"],
        "exit_code": snapshot["exit_code"],
        "last_error": snapshot["last_error"],
    })


@app.route("/api/run/snapshot", methods=["GET"])
def api_run_snapshot():
    try:
        since = int(request.args.get("since", 0) or 0)
    except (TypeError, ValueError):
        since = 0
    return jsonify(_runtime_snapshot(since))


def _describe_output_csv(path: Path) -> dict:
    import csv as _csv

    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader) if path.stat().st_size <= 15 * 1024 * 1024 else None
            return _describe_output_csv_from_rows(path, rows=rows, fieldnames=fieldnames)
    except Exception:
        return _describe_output_csv_from_rows(path)


@app.route("/api/outputs", methods=["GET"])
def api_outputs():
    files = []
    if OUTPUT_DIR.exists():
        for f in sorted(OUTPUT_DIR.glob("labeled_*.csv"), reverse=True):
            owner = _output_owner(f.name)
            if not _current_user_is_admin() and (not owner or owner.get("id") != _current_user_id()):
                continue
            files.append({
                "name": f.name,
                "owner": _public_user(owner) if owner else None,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                **_describe_output_csv(f),
            })
    return jsonify(files)


@app.route("/outputs/<name>")
def api_serve_output(name):
    import re
    if not re.match(r"^labeled_\d{8}_\d{6}\.csv$", name):
        return "Nombre de archivo invalido", 400
    if not _can_access_output(name):
        return "No autorizado", 403
    path = OUTPUT_DIR / name
    if not path.exists():
        return "Archivo no encontrado", 404
    return path.read_bytes(), 200, {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{name}"'}


@app.route("/api/outputs/<name>/stats", methods=["GET"])
def api_output_stats(name):
    """Calcula estadisticas completas para un CSV historico especifico."""
    import csv as _csv
    import re as _re
    if not _re.match(r"^labeled_\d{8}_\d{6}\.csv$", name):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    if not _can_access_output(name):
        return jsonify({"error": "No autorizado"}), 403
    path = OUTPUT_DIR / name
    if not path.exists():
        return jsonify({"error": "Archivo no encontrado"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(build_run_stats(rows, fieldnames, name, result_limit=None))


@app.route("/history")
def page_history():
    return render_template("history.html")


@app.route("/api/outputs/<name>", methods=["DELETE"])
def api_delete_output(name):
    import re
    if not re.match(r"^labeled_\d{8}_\d{6}\.csv$", name):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    if not _can_access_output(name):
        return jsonify({"error": "No autorizado"}), 403
    path = OUTPUT_DIR / name
    if not path.exists():
        return jsonify({"error": "Archivo no encontrado"}), 404
    path.unlink()
    with _auth_db() as conn:
        conn.execute("DELETE FROM run_ownership WHERE output_file = ?", (name,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/outputs/archive-all", methods=["POST"])
def api_archive_all_outputs():
    denied = _require_admin()
    if denied:
        return denied
    import re
    from datetime import datetime
    files = list(OUTPUT_DIR.glob("labeled_*.csv")) if OUTPUT_DIR.exists() else []
    files = [f for f in files if re.match(r"^labeled_\d{8}_\d{6}\.csv$", f.name)]
    if not files:
        cleared = _clear_runtime_checkpoint_files(include_run_state=True, include_claim_results=True)
        _reset_current_stats()
        return jsonify({"ok": True, "archived": 0, "archive_dir": None, "cleared": cleared})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = BASE_DIR / "archive" / f"partial_{stamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in files:
        dest = archive_dir / f.name
        f.rename(dest)
        count += 1
    # Archive is an explicit "done with this run" — clear all checkpoint state including
    # claim_results.jsonl so the next run starts completely fresh from the UI perspective.
    cleared = _clear_runtime_checkpoint_files(include_run_state=True, include_claim_results=True)
    _reset_current_stats()
    with _auth_db() as conn:
        conn.executemany("DELETE FROM run_ownership WHERE output_file = ?", [(f.name,) for f in files])
        conn.commit()
    return jsonify({
        "ok": True,
        "archived": count,
        "archive_dir": f"archive/partial_{stamp}",
        "cleared": cleared,
    })


@app.route("/api/run/principle-counts", methods=["GET"])
def api_principle_counts():
    stats      = _process_state["current_stats"]
    principles  = stats.get("principles") or []
    if not principles:
        return jsonify({"ok": False, "counts": {}, "principles": []})

    # Use live in-memory counts (updated on every claim_ok, no CSV read needed)
    live_counts = stats.get("principle_counts", {})
    if live_counts and any(
        live_counts.get(p, {}).get("ones", 0) + live_counts.get(p, {}).get("zeros", 0) > 0
        for p in principles
    ):
        counts = {p: live_counts.get(p, {"ones": 0, "zeros": 0}) for p in principles}
        return jsonify({"ok": True, "counts": counts, "principles": principles, "source": "live"})

    # Fallback: read from output CSV (written at checkpoint boundaries)
    output_path = stats.get("output_path")
    if not output_path:
        return jsonify({"ok": False, "counts": {}, "principles": principles})
    path = Path(output_path)
    if not path.exists():
        return jsonify({"ok": False, "counts": {}, "principles": principles})
    try:
        import csv as csv_mod
        with open(path, newline="", encoding="utf-8") as f:
            counts = principle_counts_from_rows(list(csv_mod.DictReader(f)), principles)
    except Exception:
        counts = {p: {"ones": 0, "zeros": 0} for p in principles}
    return jsonify({"ok": True, "counts": counts, "principles": principles, "source": "csv"})


def _resolve_project_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    resolved = path.resolve()
    if not str(resolved).startswith(str(BASE_DIR.resolve())):
        return None
    return resolved


def _load_input_rows_for_run_state(run_state: dict) -> tuple[list[dict], list[str]]:
    input_path = _resolve_project_path(run_state.get("input_file"))
    if not input_path or not input_path.exists():
        return [], []
    try:
        return load_dataset(input_path, sheet=run_state.get("sheet") or "", text_column=None)
    except DatasetError:
        return [], []


def _load_checkpoint_result_rows() -> tuple[list[dict], list[str], str]:
    run_state_path = CHECKPOINTS_DIR / "run_state.json"
    claim_results_path = CHECKPOINTS_DIR / "claim_results.jsonl"
    if not run_state_path.exists() or not claim_results_path.exists():
        return [], [], ""

    try:
        with open(run_state_path, "r", encoding="utf-8") as f:
            run_state = json.load(f)
        input_rows, input_fields = _load_input_rows_for_run_state(run_state)
        merged_by_index: dict[int, dict] = {}
        fieldnames = list(input_fields)

        with open(claim_results_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                idx = _safe_int(item.get("row_index"))
                if idx is None or idx < 0:
                    continue
                base_row = dict(input_rows[idx]) if idx < len(input_rows) else {}
                record = item.get("record") if isinstance(item.get("record"), dict) else {}
                merged_row = {**base_row, **record}
                merged_by_index[idx] = merged_row
                for key in merged_row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)

        rows = [merged_by_index[idx] for idx in sorted(merged_by_index)]
        source = f"checkpoint:{run_state.get('run_id') or claim_results_path.name}"
        return rows, fieldnames, source
    except Exception:
        return [], [], ""


@app.route("/api/run/stats", methods=["GET"])
def api_run_stats():
    """
    Lee el ultimo CSV etiquetado (o claim_results.jsonl si no hay CSV aun)
    y calcula estadisticas de tokens, latencia por arbitro y tabla de resultados.
    """
    import csv as _csv

    rows: list[dict] = []
    fieldnames: list[str] = []
    source_file: str = ""

    if OUTPUT_DIR.exists():
        csvs = sorted(OUTPUT_DIR.glob("labeled_*.csv"), reverse=True)
        if csvs:
            latest = csvs[0]
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    reader = _csv.DictReader(f)
                    fieldnames = reader.fieldnames or []
                    rows = list(reader)
                source_file = latest.name
            except Exception as e:
                app.logger.warning("[run/stats] Error leyendo CSV %s: %s", latest.name, e)

    # Use checkpoint rows (claim_results.jsonl) only while a run is underway (run_state.json
    # exists). This avoids showing stale data from a previous run after the CSV was archived
    # but the checkpoint was not yet cleared.
    run_state_path = CHECKPOINTS_DIR / "run_state.json"
    if run_state_path.exists():
        checkpoint_rows, checkpoint_fieldnames, checkpoint_source = _load_checkpoint_result_rows()
        if len(checkpoint_rows) > len(rows):
            rows = checkpoint_rows
            fieldnames = checkpoint_fieldnames
            source_file = checkpoint_source or "checkpoint"

    if not rows:
        return jsonify({"error": "No hay datos aun"}), 404

    raw_limit = str(request.args.get("limit", "250")).strip().lower()
    try:
        result_offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        result_offset = 0
    result_offset = max(0, result_offset)

    result_limit = None if raw_limit in {"all", "todo", "todos", "0"} else 250
    if result_limit is not None:
        try:
            result_limit = int(raw_limit)
        except (ValueError, TypeError):
            result_limit = 250
        result_limit = max(1, min(result_limit, 1000))

    return jsonify(build_run_stats(
        rows,
        fieldnames,
        source_file,
        result_limit=result_limit,
        result_offset=result_offset,
    ))


@app.route("/api/checkpoint/status", methods=["GET"])
def api_checkpoint_status():
    cp = CHECKPOINTS_DIR / "checkpoint.json"
    run_state_path = CHECKPOINTS_DIR / "run_state.json"
    run_state = None
    if run_state_path.exists():
        try:
            with open(run_state_path, "r", encoding="utf-8") as f:
                raw_state = json.load(f)
            summary = raw_state.get("summary", {})
            run_state = {
                "run_id": raw_state.get("run_id"),
                "status": raw_state.get("status"),
                "updated_at": raw_state.get("updated_at"),
                "output_file": raw_state.get("output_file"),
                "total_rows": raw_state.get("total_rows"),
                "pause_kind": raw_state.get("pause_kind", ""),
                "pause_reason": raw_state.get("pause_reason", ""),
                "processed_rows": summary.get("processed_rows", 0),
                "successful_rows": summary.get("successful_rows", 0),
                "failed_rows": summary.get("failed_rows", 0),
            }
            if run_state["status"] == "completed":
                # claim_results.jsonl is intentionally preserved after completion
                # so the next run with a higher limit can skip already-done entries.
                claim_count = run_state.get("processed_rows", 0) or 0
                if CLAIM_RESULTS_PATH.exists() and claim_count > 0:
                    return jsonify({
                        "exists": True,
                        "last_index": claim_count - 1,
                        "claims_done": claim_count,
                        "run_state": run_state,
                    })
                return jsonify({"exists": False, "run_state": run_state})
        except (OSError, json.JSONDecodeError):
            run_state = None
    if not cp.exists():
        return jsonify({"exists": False, "run_state": run_state})
    with open(cp) as f:
        data = json.load(f)
    return jsonify({"exists": True, "last_index": data.get("last_index", -1),
                    "claims_done": data.get("last_index", -1) + 1,
                    "run_state": run_state})


@app.route("/api/failed-queue", methods=["GET"])
def api_failed_queue():
    path = CHECKPOINTS_DIR / "failed_queue.jsonl"
    if not path.exists():
        return jsonify([])
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                row = item.get("row", {})
                items.append({
                    "index": item.get("index"),
                    "reason": item.get("reason", "sin detalle"),
                    "claim": row.get("claim", ""),
                })
            except json.JSONDecodeError:
                continue
    return jsonify(items)


@app.route("/api/checkpoint/clear", methods=["POST"])
def api_checkpoint_clear():
    cleared = _clear_runtime_checkpoint_files(include_run_state=True, include_claim_results=True)
    _reset_current_stats()
    return jsonify({"ok": True, "cleared": cleared})


# ---------------------------------------------------------------------------
# Entry point  (ejecucion directa: python webapp/app.py)
# Para produccion / uso normal usa main_webapp.py que acepta --host y --port.
# Puerto 7860: libre en macOS (5000 ocupado por AirPlay), Windows y Linux.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os as _os
    _host = _os.environ.get("LABELFORGE_HOST", "127.0.0.1")
    _port = int(_os.environ.get("LABELFORGE_PORT", "7860"))
    print(f"  LabelForge   ->   http://{_host}:{_port}")
    app.run(debug=True, host=_host, port=_port, threaded=True)
