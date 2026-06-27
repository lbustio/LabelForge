"""
manage.py — Herramientas de administracion de LabelForge por linea de comandos.

Uso:
    python manage.py reset-admin-password
    python manage.py list-users
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SECRETS_DIR = BASE_DIR / "secrets"
DEFAULT_DB = SECRETS_DIR / "auth.db"

ADMIN_ROLE = "admin"


def _get_db_path() -> Path:
    raw = os.environ.get("LABELFORGE_AUTH_DB", "")
    return Path(raw) if raw else DEFAULT_DB


def _open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(f"Error: base de datos no encontrada en {db_path}\n"
                 "Asegurate de haber ejecutado setup_env.py y iniciado la webapp al menos una vez.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _validate_password_strength(password: str) -> tuple[bool, str]:
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


def _hash_password(password: str) -> str:
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_reset_admin_password(args: argparse.Namespace) -> None:
    db_path = _get_db_path()
    conn = _open_db(db_path)

    admin = conn.execute(
        "SELECT * FROM users WHERE role = ? AND active = 1 ORDER BY id LIMIT 1",
        (ADMIN_ROLE,),
    ).fetchone()

    if not admin:
        sys.exit("Error: no se encontro ningun usuario admin activo en la base de datos.")

    print(f"Restableciendo contrasena para el admin: {admin['username']}")
    print("(La sesion activa del admin sera invalidada al guardar)\n")

    for attempt in range(3):
        password = getpass.getpass("Nueva contrasena: ")
        confirm = getpass.getpass("Confirmar contrasena: ")
        if password != confirm:
            print("Las contrasenas no coinciden. Intenta de nuevo.\n")
            continue
        ok, error = _validate_password_strength(password)
        if not ok:
            print(f"{error}\n")
            continue
        break
    else:
        sys.exit("Demasiados intentos fallidos.")

    password_hash = _hash_password(password)
    conn.execute(
        "UPDATE users SET password_hash = ?, session_version = lower(hex(randomblob(16))), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (password_hash, admin["id"]),
    )
    conn.commit()
    conn.close()

    print(f"\nContrasena del admin '{admin['username']}' actualizada correctamente.")
    print("Todas las sesiones activas de ese usuario han sido invalidadas.")


def cmd_list_users(args: argparse.Namespace) -> None:
    db_path = _get_db_path()
    conn = _open_db(db_path)
    rows = conn.execute(
        "SELECT id, username, email, role, active, last_login_at FROM users ORDER BY role ASC, username ASC"
    ).fetchall()
    conn.close()

    if not rows:
        print("No hay usuarios registrados.")
        return

    print(f"\n{'ID':<5} {'Usuario':<20} {'Rol':<8} {'Activo':<8} {'Email':<30} {'Ultimo login'}")
    print("-" * 85)
    for r in rows:
        estado = "si" if r["active"] else "no"
        print(f"{r['id']:<5} {r['username']:<20} {r['role']:<8} {estado:<8} {(r['email'] or '-'):<30} {r['last_login_at'] or '-'}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Herramientas de administracion de LabelForge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Comandos disponibles:
  reset-admin-password    Restablecer la contrasena del administrador
  list-users              Listar todos los usuarios registrados

Ejemplo:
  python manage.py reset-admin-password
        """,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="comando")

    subparsers.add_parser("reset-admin-password", help="Restablecer la contrasena del admin")
    subparsers.add_parser("list-users", help="Listar todos los usuarios")

    args = parser.parse_args()

    if args.command == "reset-admin-password":
        cmd_reset_admin_password(args)
    elif args.command == "list-users":
        cmd_list_users(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
