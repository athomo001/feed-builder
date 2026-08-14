#!/usr/bin/env python3
"""Backup del estado del Hub. Empaqueta `HUB_STATE_DIR` (todas las bases
SQLite: destinos, politicas, ledger, tokens, alertas, secretos cifrados,
etc.) y `TXT_FEED_DIR` (feeds materializados) en un unico archivo `.tar.gz`
con nombre timestamped -- ver `scripts/restore_state.py` para el camino
inverso, y `docs/RUNBOOK.md` para el procedimiento completo.

Uso:
    python scripts/backup_state.py --state-dir ./state --feed-dir ./feeds --out-dir ./backups

Autor: Athan Espinoza
"""
import argparse
import os
import tarfile
from datetime import datetime, timezone


def backup(*, state_dir: str, feed_dir: str, out_dir: str, now: datetime = None) -> str:
    # `now` es inyectable para que los tests puedan fijar el timestamp del
    # nombre de archivo en vez de depender del reloj real.
    now = now or datetime.now(timezone.utc)
    os.makedirs(out_dir, exist_ok=True)
    filename = f"hub-backup-{now.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    out_path = os.path.join(out_dir, filename)

    with tarfile.open(out_path, "w:gz") as tar:
        # Los nombres fijos "state"/"feeds" dentro del archivo (en vez del
        # path real de origen) son lo que le permite a restore_state.py
        # saber donde va cada cosa sin importar de que ruta se hizo el
        # backup.
        if os.path.isdir(state_dir):
            tar.add(state_dir, arcname="state")
        # feed_dir puede no existir todavia (p.ej. instancia recien
        # instalada que aun no genero feeds); no es un error, solo se
        # omite del archivo.
        if os.path.isdir(feed_dir):
            tar.add(feed_dir, arcname="feeds")

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", default=os.environ.get("HUB_STATE_DIR", "./state"))
    parser.add_argument("--feed-dir", default=os.environ.get("TXT_FEED_DIR", "./feeds"))
    parser.add_argument("--out-dir", default="./backups")
    args = parser.parse_args()

    path = backup(state_dir=args.state_dir, feed_dir=args.feed_dir, out_dir=args.out_dir)
    print(path)


if __name__ == "__main__":
    main()
