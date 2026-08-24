import base64
import json
import os
import time
from pathlib import Path

from labelforge_core.secrets_io import provider_api_key_env, secrets_env_path, set_secret_value


ARBITERS_EXPORT_MAGIC = "LFARB1"
ARBITERS_EXPORT_AAD = b"LabelForge arbiters export v1"
ARBITERS_EXPORT_KDF_ITERATIONS = 390_000
BASE_DIR = Path(__file__).resolve().parent.parent


def require_export_password(password: str) -> str:
    password = (password or "").strip()
    if len(password) < 8:
        raise ValueError("La contrasena debe tener al menos 8 caracteres.")
    return password


def normalize_arbiter(data: dict, existing: dict = None) -> dict:
    base = existing.copy() if existing else {}
    if "name" in data:
        base["name"] = data["name"].strip()
    if "persona" in data:
        base["persona"] = data.get("persona", "").strip()
    if "system_prompt" in data:
        base["system_prompt"] = data["system_prompt"].strip()
    if "id" in data and not existing:
        base["id"] = data["id"].strip()

    if "provider" in data:
        p = data["provider"]
        api_key_override = (p.get("api_key_override", "") or "").strip()
        api_key_env = p.get("api_key_env", "") or provider_api_key_env(p.get("type", "gemini"))
        if api_key_override and api_key_env:
            set_secret_value(secrets_env_path(BASE_DIR), api_key_env, api_key_override)
        base["provider"] = {
            "type": p.get("type", "gemini"),
            "model": p.get("model", ""),
            "model_path": p.get("model_path", ""),
            # llama-cpp-python 0.3.x documenta 0 como la ventana del modelo.
            "n_ctx": int(p.get("n_ctx", 0) or 0),
            "context_window": int(p.get("context_window", 0) or 0),
            "api_key_env": api_key_env,
            "api_key_override": "",
            "base_url": p.get("base_url", ""),
            "vertex_project": p.get("vertex_project", ""),
            "vertex_location": p.get("vertex_location", "us-central1"),
        }

    if "generation_config" in data:
        gc = data["generation_config"]
        base["generation_config"] = {
            "temperature": float(gc.get("temperature", 0.1)),
            "max_output_tokens": int(gc.get("max_output_tokens", 600)),
        }
    return base


def _derive_export_key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ARBITERS_EXPORT_KDF_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_arbiters_export(payload: dict, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    password = require_export_password(password)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_export_key(password, salt)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, ARBITERS_EXPORT_AAD)
    envelope = {
        "magic": ARBITERS_EXPORT_MAGIC,
        "version": 1,
        "kdf": {
            "name": "PBKDF2-HMAC-SHA256",
            "iterations": ARBITERS_EXPORT_KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        },
    }
    return json.dumps(envelope, indent=2).encode("utf-8")


def decrypt_arbiters_export(raw: bytes, password: str) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    password = require_export_password(password)
    try:
        envelope = json.loads(raw.decode("utf-8"))
        if envelope.get("magic") != ARBITERS_EXPORT_MAGIC:
            raise ValueError("El archivo no parece ser una exportacion cifrada de arbitros.")
        kdf = envelope.get("kdf", {})
        cipher = envelope.get("cipher", {})
        if kdf.get("name") != "PBKDF2-HMAC-SHA256" or cipher.get("name") != "AES-256-GCM":
            raise ValueError("Formato de cifrado no soportado.")
        iterations = int(kdf.get("iterations", 0))
        if iterations != ARBITERS_EXPORT_KDF_ITERATIONS:
            raise ValueError("Parametros KDF no soportados.")
        salt = base64.b64decode(kdf["salt"])
        nonce = base64.b64decode(cipher["nonce"])
        ciphertext = base64.b64decode(cipher["ciphertext"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("No se pudo leer el archivo de exportacion.") from exc

    try:
        key = _derive_export_key(password, salt)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, ARBITERS_EXPORT_AAD)
    except InvalidTag as exc:
        raise ValueError("Contrasena incorrecta o archivo alterado.") from exc

    try:
        return json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("El contenido descifrado no es valido.") from exc


def build_arbiters_export_payload(arbiters: list) -> dict:
    return {
        "format": "labelforge-arbiters",
        "version": 1,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arbiters": arbiters,
    }


def validate_imported_arbiters(payload: dict) -> list:
    if payload.get("format") != "labelforge-arbiters" or int(payload.get("version", 0)) != 1:
        raise ValueError("Formato de exportacion no soportado.")
    arbiters = payload.get("arbiters")
    if not isinstance(arbiters, list) or not arbiters:
        raise ValueError("La exportacion no contiene arbitros.")

    seen = set()
    cleaned = []
    for item in arbiters:
        if not isinstance(item, dict):
            raise ValueError("La exportacion contiene un arbitro invalido.")
        for key in ["id", "name", "provider", "system_prompt"]:
            if not item.get(key):
                raise ValueError(f"Arbitro importado sin campo requerido: {key}.")
        arb_id = str(item["id"]).strip()
        if arb_id in seen:
            raise ValueError(f"La exportacion repite el id de arbitro '{arb_id}'.")
        seen.add(arb_id)
        cleaned.append(normalize_arbiter(item))
    return cleaned


def export_arbiters_to_file(arbiters: list, output_path: Path, password: str) -> Path:
    payload = build_arbiters_export_payload(arbiters)
    encrypted = encrypt_arbiters_export(payload, password)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encrypted)
    return output_path


def import_arbiters_from_file(input_path: Path, password: str) -> list:
    payload = decrypt_arbiters_export(Path(input_path).read_bytes(), password)
    return validate_imported_arbiters(payload)


def merge_arbiters(current: list, imported: list, mode: str = "merge") -> tuple[list, dict]:
    if mode not in {"merge", "replace"}:
        raise ValueError("Modo de importacion invalido.")
    if mode == "replace":
        return imported, {
            "mode": mode,
            "added": len(imported),
            "updated": 0,
            "kept": 0,
            "total": len(imported),
        }

    merged = list(current)
    by_id = {a["id"]: i for i, a in enumerate(merged)}
    added = 0
    updated = 0
    for arb in imported:
        if arb["id"] in by_id:
            merged[by_id[arb["id"]]] = arb
            updated += 1
        else:
            merged.append(arb)
            added += 1
    kept = len(current) - updated
    return merged, {
        "mode": mode,
        "added": added,
        "updated": updated,
        "kept": kept,
        "total": len(merged),
    }
