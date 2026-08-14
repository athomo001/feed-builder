#!/usr/bin/env python3
"""Restore de un backup del Hub (ver `scripts/backup_state.py`, `docs/
RUNBOOK.md`). Rechaza sobrescribir un destino no vacio salvo `--force`, y
valida que las entradas del archivo queden dentro de los prefijos
"state/"/"feeds/" esperados (protege contra path traversal al extraer un
archivo de origen no confiable).

Uso:
    python scripts/restore_state.py hub-backup-20260814T120000Z.tar.gz --state-dir ./state --feed-dir ./feeds

Autor: Athan Espinoza
"""
import argparse
import os
import tarfile


class RestoreError(RuntimeError):
    # Excepcion propia (en vez de dejar propagar errores genericos de
    # tarfile/IO) para que el CLI pueda distinguir "el restore rechazo el
    # archivo por una validacion nuestra" de un error de I/O inesperado.
    pass


def _validate_member_names(tar: tarfile.TarFile) -> None:
    # Un archivo .tar.gz de origen no confiable podria contener entradas con
    # ".." o rutas absolutas que escriban fuera de state_dir/feed_dir (path
    # traversal / "zip slip"). Se valida todo el archivo antes de extraer
    # nada.
    for member in tar.getmembers():
        normalized = os.path.normpath(member.name)
        if normalized.startswith("..") or os.path.isabs(normalized):
            raise RestoreError(f"entrada de archivo invalida: '{member.name}'")


def _extract_prefixed(tar: tarfile.TarFile, members: list, *, prefix: str, dest: str) -> None:
    # El archivo guarda las entradas bajo "state/"/"feeds/" (ver
    # backup_state.py), pero acá se extraen directo dentro de state_dir/
    # feed_dir sin ese prefijo, para no crear una subcarpeta "state" anidada
    # dentro del destino elegido por el usuario.
    for member in members:
        relative = member.name[len(prefix):].lstrip("/")
        if not relative:
            continue
        member.name = relative
        tar.extract(member, path=dest)


def restore(archive_path: str, *, state_dir: str, feed_dir: str, force: bool = False) -> None:
    # Sin --force, un restore accidental sobre un directorio con datos vivos
    # queda bloqueado: es preferible fallar temprano a pisar sin querer el
    # estado actual del Hub.
    if not force:
        for target in (state_dir, feed_dir):
            if os.path.isdir(target) and os.listdir(target):
                raise RestoreError(f"'{target}' no esta vacio -- usa --force para sobrescribir")

    with tarfile.open(archive_path, "r:gz") as tar:
        # Orden critico: se valida el archivo completo ANTES de crear
        # directorios o extraer cualquier miembro, para no dejar a medio
        # camino una extraccion que despues resulta invalida.
        _validate_member_names(tar)
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(feed_dir, exist_ok=True)

        members = tar.getmembers()
        state_members = [m for m in members if m.name == "state" or m.name.startswith("state/")]
        feed_members = [m for m in members if m.name == "feeds" or m.name.startswith("feeds/")]

        _extract_prefixed(tar, state_members, prefix="state", dest=state_dir)
        _extract_prefixed(tar, feed_members, prefix="feeds", dest=feed_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive")
    parser.add_argument("--state-dir", default=os.environ.get("HUB_STATE_DIR", "./state"))
    parser.add_argument("--feed-dir", default=os.environ.get("TXT_FEED_DIR", "./feeds"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    restore(args.archive, state_dir=args.state_dir, feed_dir=args.feed_dir, force=args.force)
    print(f"Restaurado '{args.archive}' en '{args.state_dir}' y '{args.feed_dir}'")


if __name__ == "__main__":
    main()
