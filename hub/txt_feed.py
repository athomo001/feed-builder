"""Escritura TXT compatible (spec/09-ROADMAP-ACCEPTANCE.md Entrega 1
"persistencia y escritura TXT"; spec/04-IOC-MODEL-POLICIES.md "Capacidad y
throughput por destino"; spec/05-FORMATS-DESTINATIONS.md "Texto legacy").

Esto es la escritura TXT compatible de base (un feed por subtipo, orden
estable, escritura atomica, capacidad configurable). El adapter completo de
destino (validate/render/send/acknowledge/healthcheck/close, spec/05) con
configuracion por destino es Entrega 2 ("Adaptador TXT compatible");
`FeedWriter` es el bloque que ese adapter va a envolver.
"""
import os
from dataclasses import dataclass
from typing import Literal

OverflowStrategy = Literal["newest_first", "highest_score_first", "longest_validity_first"]


@dataclass
class FeedWriteResult:
    written: int
    skipped_capacity: int


class FeedWriter:
    """Mantiene el conjunto ordenado/deduplicado de valores de UN subtipo y
    lo materializa en disco de forma atomica (tmp + os.replace)."""

    def __init__(self, path: str, *, max_records: int = 0, overflow_strategy: OverflowStrategy = "newest_first"):
        self.path = path
        self.max_records = max_records
        self.overflow_strategy = overflow_strategy
        # value -> sort_key (mayor = mas prioritario segun overflow_strategy).
        # Se siembra desde el archivo en disco si ya existe: un FeedWriter es
        # de vida corta (se reconstruye por evento/request, Entrega 2), asi
        # que el archivo -- no la memoria del proceso -- es la fuente de
        # verdad entre llamadas. Los valores recuperados no tienen sort_key
        # original, quedan al final bajo "newest_first" hasta que una nueva
        # version los actualice.
        self._values: dict[str, float] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                value = line.rstrip("\n")
                if value:
                    self._values[value] = float("-inf")

    def upsert(self, value: str, *, sort_key: float) -> None:
        self._values[value] = sort_key

    def remove(self, value: str) -> None:
        self._values.pop(value, None)

    def __len__(self) -> int:
        return len(self._values)

    def _ordered_values(self) -> list[str]:
        # Orden estable: por sort_key desc (mas prioritario primero), luego por valor.
        return [v for v, _ in sorted(self._values.items(), key=lambda kv: (-kv[1], kv[0]))]

    def rebuild(self, *, header: str = "") -> FeedWriteResult:
        ordered = self._ordered_values()
        skipped_capacity = 0
        if self.max_records and len(ordered) > self.max_records:
            skipped_capacity = len(ordered) - self.max_records
            ordered = ordered[: self.max_records]

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            if header:
                f.write(header.rstrip("\n") + "\n")
            for value in ordered:
                f.write(value + "\n")
        os.replace(tmp_path, self.path)

        return FeedWriteResult(written=len(ordered), skipped_capacity=skipped_capacity)


class FeedWriterRegistry:
    """Un `FeedWriter` por subtipo (spec/05 'Texto legacy': `md5.txt`,
    `sha512.txt`, `ip.txt`... nunca mezclar algoritmos/subtipos en un mismo
    archivo por defecto)."""

    def __init__(self, base_dir: str, *, max_records: int = 0, overflow_strategy: OverflowStrategy = "newest_first"):
        self.base_dir = base_dir
        self.max_records = max_records
        self.overflow_strategy = overflow_strategy
        self._writers: dict[str, FeedWriter] = {}

    def get(self, subtype: str) -> FeedWriter:
        if subtype not in self._writers:
            path = os.path.join(self.base_dir, f"{subtype}.txt")
            self._writers[subtype] = FeedWriter(
                path, max_records=self.max_records, overflow_strategy=self.overflow_strategy
            )
        return self._writers[subtype]

    def rebuild_all(self) -> dict[str, FeedWriteResult]:
        return {subtype: writer.rebuild() for subtype, writer in self._writers.items()}
