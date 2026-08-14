"""Secretos cifrados en reposo. Mismo patrón SQLite que el resto de
`hub/*_store.py`. El valor en claro NUNCA se persiste sin cifrar ni se
devuelve por la API una vez guardado -- solo se puede usar (via
`credential_ref = "secret://<name>"`, `hub/credentials.py`) o probar
(descifrar y confirmar que abre, sin exponerlo, `POST .../test`).

Autor: Athan Espinoza
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from hub.secret_encryption import SecretCipher


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS secrets (
            name TEXT PRIMARY KEY,
            ciphertext TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def put_secret(
    conn: sqlite3.Connection, name: str, plaintext: str, *, cipher: SecretCipher, now: Optional[datetime] = None
) -> None:
    now = now or datetime.now(timezone.utc)
    ciphertext = cipher.encrypt(plaintext)
    # Se preserva el created_at original si el secreto ya existia (esto es
    # un upsert: "guardar" y "rotar el valor" son la misma operacion), en
    # vez de perder la fecha de creacion original cada vez que se actualiza.
    existing = conn.execute("SELECT created_at FROM secrets WHERE name = ?", (name,)).fetchone()
    created_at = existing[0] if existing else now.isoformat()
    conn.execute(
        """
        INSERT INTO secrets (name, ciphertext, created_at, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET ciphertext = excluded.ciphertext, updated_at = excluded.updated_at
        """,
        (name, ciphertext, created_at, now.isoformat()),
    )
    conn.commit()


def get_secret(conn: sqlite3.Connection, name: str, *, cipher: SecretCipher) -> Optional[str]:
    row = conn.execute("SELECT ciphertext FROM secrets WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return cipher.decrypt(row[0])


def list_secret_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM secrets ORDER BY name").fetchall()
    return [row[0] for row in rows]


def delete_secret(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0


def rotate_key(conn: sqlite3.Connection, *, old_cipher: SecretCipher, new_cipher: SecretCipher) -> int:
    """Re-cifra todos los secretos con una clave nueva. Descifra y re-cifra
    TODO primero, en memoria, antes de escribir ninguna fila: si algun valor
    no descifra con `old_cipher` (clave equivocada), la funcion completa
    falla sin haber tocado la base -- deja la tabla intacta con la clave
    vieja en vez de una rotacion a medio terminar que mezcle claves entre
    filas."""
    rows = conn.execute("SELECT name, ciphertext FROM secrets").fetchall()
    re_encrypted = [(name, new_cipher.encrypt(old_cipher.decrypt(ciphertext))) for name, ciphertext in rows]
    for name, ciphertext in re_encrypted:
        conn.execute("UPDATE secrets SET ciphertext = ? WHERE name = ?", (ciphertext, name))
    conn.commit()
    return len(re_encrypted)
