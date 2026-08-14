"""Escritura TXT compatible: un feed por subtipo, orden estable, escritura
atomica, capacidad configurable.

El adapter completo de destino (validate/render/send/acknowledge/
healthcheck/close) con configuracion por destino envuelve esta clase;
`FeedWriter` es el bloque base sobre el que se construye ese adapter.

Autor: Athan Espinoza
"""
import os
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from hub.tracing import span

OverflowStrategy = Literal["newest_first", "highest_score_first", "longest_validity_first"]

# `render_line(value, sort_key, meta) -> str`: por defecto una linea es el
# valor crudo, ignorando `meta` (compatibilidad exacta con el formato TXT
# legacy). Otros formatos de archivo (CSV de Check Point, MikroTik `.rsc`,
# Wazuh CDB) reusan esta misma clase pasando un renderer distinto que si usa
# `meta` (subtype/score/confidence/etc.), en vez de triplicar la logica de
# atomic-write/capacidad/overflow/dedup por cada formato. `meta` de un valor
# releido desde disco siempre es `{}` (no se puede reconstruir desde una
# linea ya renderizada) hasta que un evento real lo actualice -- mismo
# razonamiento que el sort_key `-inf` de abajo.
RenderLine = Callable[[str, float, dict], str]

# `parse_line(line) -> value | None`: inverso de `render_line`, para releer
# el valor original desde una linea ya renderizada (un FeedWriter es de vida
# corta, ver comentario en `__init__`; sin esto, releer un archivo en un
# formato no identidad interpretaria la linea completa como si fuera el
# valor). `None` = linea ignorada (por ejemplo un comentario/header).
ParseLine = Callable[[str], Optional[str]]


@dataclass
class FeedWriteResult:
    written: int
    skipped_capacity: int


class FeedWriter:
    """Mantiene el conjunto ordenado/deduplicado de valores de UN subtipo y
    lo materializa en disco de forma atomica (tmp + os.replace)."""

    def __init__(
        self,
        path: str,
        *,
        max_records: int = 0,
        overflow_strategy: OverflowStrategy = "newest_first",
        render_line: Optional[RenderLine] = None,
        parse_line: Optional[ParseLine] = None,
        header: str = "",
    ):
        self.path = path
        self.max_records = max_records
        self.overflow_strategy = overflow_strategy
        self.header = header
        self._render_line = render_line or (lambda value, _sort_key, _meta: value)
        self._parse_line = parse_line or (lambda line: line)
        # value -> (sort_key, meta). Se siembra desde el archivo en disco si
        # ya existe: un FeedWriter es de vida corta (se reconstruye por
        # evento/request), asi que el archivo -- no la memoria del proceso --
        # es la fuente de verdad entre llamadas. Los valores recuperados no
        # tienen sort_key/meta originales, quedan al final bajo
        # "newest_first" hasta que una nueva version los actualice.
        self._values: dict[str, tuple[float, dict]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                if not raw:
                    continue
                value = self._parse_line(raw)
                if value:
                    self._values[value] = (float("-inf"), {})

    def upsert(self, value: str, *, sort_key: float, meta: Optional[dict] = None) -> None:
        self._values[value] = (sort_key, meta or {})

    def remove(self, value: str) -> None:
        self._values.pop(value, None)

    def __len__(self) -> int:
        return len(self._values)

    def _ordered_entries(self) -> list[tuple[str, float, dict]]:
        # Orden estable: por sort_key desc (mas prioritario primero), luego por valor.
        return [
            (value, sort_key, meta)
            for value, (sort_key, meta) in sorted(self._values.items(), key=lambda kv: (-kv[1][0], kv[0]))
        ]

    def rebuild(self, *, header: Optional[str] = None) -> FeedWriteResult:
        with span("feed.rebuild", feed_path=self.path):
            header = header if header is not None else self.header
            ordered = self._ordered_entries()
            skipped_capacity = 0
            if self.max_records and len(ordered) > self.max_records:
                # Los entries ya vienen ordenados por prioridad (sort_key
                # desc); truncar la cola alcanza sin importar el
                # overflow_strategy configurado.
                skipped_capacity = len(ordered) - self.max_records
                ordered = ordered[: self.max_records]

            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp_path = self.path + ".tmp"
            # Escritura a archivo temporal + os.replace: un lector externo
            # (el destino que sirve este TXT) nunca ve un archivo a medio
            # escribir.
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                if header:
                    f.write(header.rstrip("\n") + "\n")
                for value, sort_key, meta in ordered:
                    f.write(self._render_line(value, sort_key, meta) + "\n")
            os.replace(tmp_path, self.path)

            return FeedWriteResult(written=len(ordered), skipped_capacity=skipped_capacity)


class FeedWriterRegistry:
    """Un `FeedWriter` por subtipo (`md5.txt`, `sha512.txt`, `ip.txt`...):
    nunca mezclar algoritmos/subtipos en un mismo archivo por defecto."""

    def __init__(
        self,
        base_dir: str,
        *,
        max_records: int = 0,
        overflow_strategy: OverflowStrategy = "newest_first",
        extension: str = "txt",
        render_line: Optional[RenderLine] = None,
        parse_line: Optional[ParseLine] = None,
        header: str = "",
    ):
        self.base_dir = base_dir
        self.max_records = max_records
        self.overflow_strategy = overflow_strategy
        self.extension = extension
        self._render_line = render_line
        self._parse_line = parse_line
        self._header = header
        self._writers: dict[str, FeedWriter] = {}

    def get(self, subtype: str) -> FeedWriter:
        if subtype not in self._writers:
            # Un writer por subtipo, creado on-demand y cacheado: evita abrir
            # archivos para subtipos que nunca reciben eventos.
            path = os.path.join(self.base_dir, f"{subtype}.{self.extension}")
            self._writers[subtype] = FeedWriter(
                path,
                max_records=self.max_records,
                overflow_strategy=self.overflow_strategy,
                render_line=self._render_line,
                parse_line=self._parse_line,
                header=self._header,
            )
        return self._writers[subtype]

    def rebuild_all(self) -> dict[str, FeedWriteResult]:
        return {subtype: writer.rebuild() for subtype, writer in self._writers.items()}
