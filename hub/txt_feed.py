"""Escritura TXT compatible: un feed por subtipo, orden estable, escritura
atomica, capacidad configurable.

El adapter completo de destino (validate/render/send/acknowledge/
healthcheck/close) con configuracion por destino envuelve esta clase;
`FeedWriter` es el bloque base sobre el que se construye ese adapter.

Autor: Athan Espinoza
"""
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from hub.tracing import span

OverflowStrategy = Literal["newest_first", "highest_score_first", "longest_validity_first"]

# `render_line(value, sort_key, meta) -> str`: por defecto una linea es el
# valor crudo, ignorando `meta` (compatibilidad exacta con el formato TXT
# legacy). Otros formatos de archivo (CSV de Check Point, MikroTik `.rsc`,
# Wazuh CDB) reusan esta misma clase pasando un renderer distinto que si usa
# `meta` (subtype/score/confidence/etc.), en vez de triplicar la logica de
# atomic-write/capacidad/overflow/dedup por cada formato. `meta`/`sort_key`
# de un valor releido desde disco se recuperan del sidecar de metadata (ver
# `FeedWriter._load_meta`) cuando existe; solo caen a `{}`/`-inf` para un
# valor que nunca tuvo un upsert real (ej. sidecar recien migrado).
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
    expired: int = 0


class FeedWriter:
    """Mantiene el conjunto ordenado/deduplicado de valores de UN subtipo y
    lo materializa en disco de forma atomica (tmp + os.replace)."""

    def __init__(
        self,
        path: str,
        *,
        max_records: int = 0,
        max_bytes: int = 0,
        overflow_strategy: OverflowStrategy = "newest_first",
        render_line: Optional[RenderLine] = None,
        parse_line: Optional[ParseLine] = None,
        header: str = "",
    ):
        self.path = path
        # Sidecar con sort_key/_expires_at por valor, en un archivo aparte
        # del `.txt`/`.csv`/etc. (nombre oculto, mismo criterio que
        # `.healthcheck` en TxtFeedAdapter): el archivo de feed en si solo
        # puede guardar el valor renderizado (formato legado, "un IOC por
        # linea"), no metadata -- sin este sidecar, un FeedWriter nuevo (se
        # reconstruye por evento/request, es de vida corta) perdia el
        # sort_key/vencimiento real de TODO valor salvo el que se esta
        # upsert-eando en esa llamada puntual. Eso rompia, en la practica,
        # tanto "newest_first" (los ya existentes quedaban empatados y se
        # desempataban alfabeticamente, no por fecha real) como el
        # vencimiento por TTL en el rebuild periodico de mantenimiento (que
        # nunca hace upsert(), asi que sin esto jamas encontraba una fecha de
        # vencimiento real para nada) -- bug real reportado por el operador
        # (2026-08-18): "hace horas que no cambia, esos hashes ni siquiera
        # son los mas recientes".
        directory, filename = os.path.split(path)
        self._meta_path = os.path.join(directory, f".{filename}.meta.json")
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.overflow_strategy = overflow_strategy
        self.header = header
        self._render_line = render_line or (lambda value, _sort_key, _meta: value)
        self._parse_line = parse_line or (lambda line: line)
        # value -> (sort_key, meta). El archivo de feed en si sigue siendo la
        # fuente de verdad de QUE valores existen (un FeedWriter es de vida
        # corta, se reconstruye por evento/request) -- el sidecar solo aporta
        # la metadata real de los que la tienen persistida; un valor sin
        # entrada ahi (ej. escrito antes de este fix) cae al mismo default de
        # siempre (-inf, sin vencimiento) hasta que un evento real lo toque.
        self._values: dict[str, tuple[float, dict]] = {}
        self._load_existing()

    def _load_meta(self) -> dict[str, tuple[float, dict]]:
        if not os.path.exists(self._meta_path):
            return {}
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            # Sidecar corrupto/ilegible: se degrada a "sin metadata" para
            # todos los valores (mismo fallback que un valor nunca visto por
            # el sidecar) en vez de tumbar el proceso -- el `.txt` en si
            # sigue siendo la fuente de verdad de que valores existen.
            return {}
        result = {}
        for value, entry in (raw or {}).items():
            expires_at = entry.get("expires_at")
            result[value] = (entry.get("sort_key", float("-inf")), {"_expires_at": expires_at} if expires_at else {})
        return result

    def _load_existing(self) -> None:
        if not os.path.exists(self.path):
            return
        persisted = self._load_meta()
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                if not raw:
                    continue
                value = self._parse_line(raw)
                if value:
                    self._values[value] = persisted.get(value, (float("-inf"), {}))

    def _write_meta(self) -> None:
        # Solo se persisten los valores con sort_key real conocido: uno
        # todavia en -inf (nunca tuvo un upsert real, ej. sidecar recien
        # migrado) no aporta nada guardado como esta -- se recalcula solo
        # cuando un evento real lo toque, igual que hoy.
        payload = {
            value: {"sort_key": sort_key, "expires_at": (meta or {}).get("_expires_at")}
            for value, (sort_key, meta) in self._values.items()
            if math.isfinite(sort_key)
        }
        tmp_path = self._meta_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, self._meta_path)

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

    def rebuild(self, *, header: Optional[str] = None, now: Optional[datetime] = None) -> FeedWriteResult:
        with span("feed.rebuild", feed_path=self.path):
            now = now or datetime.now(timezone.utc)
            header = header if header is not None else self.header
            ordered = self._ordered_entries()

            # Vencimiento por TTL PRIMERO, antes de aplicar cantidad/peso: un
            # entry vencido no debe contar contra el cupo de uno vigente, y se
            # descarta aunque el archivo este lejos del limite (spec/04 "TTL y
            # vigencia" -- el operador esperaba que esto pasara solo, con el
            # tiempo, no solo cuando llega un evento nuevo para ese IOC puntual).
            # `meta["_expires_at"]` (prefijo "_" para no chocar con un campo
            # propio de un adapter, ej. la columna "valid_until" del CSV) lo
            # completa el adapter en upsert() a partir de
            # hub/ttl.py::effective_expiration_for_policy; un entry releido
            # desde disco (`_load_existing`) recupera esta info del sidecar de
            # metadata si la tiene persistida -- solo se asume vigente sin
            # fecha si nunca tuvo un upsert real (sidecar recien migrado).
            live = []
            expired_count = 0
            for value, sort_key, meta in ordered:
                expires_at_raw = (meta or {}).get("_expires_at")
                if expires_at_raw is not None and datetime.fromisoformat(expires_at_raw) < now:
                    expired_count += 1
                    continue
                live.append((value, sort_key, meta))

            # Un solo recorrido, en orden de prioridad (sort_key desc):
            # incluye entries hasta que se cumple max_records O max_bytes,
            # lo que pase primero (mismo criterio que el ejemplo real de
            # FortiGate en spec/04 "Capacidad y throughput por destino": 131072
            # entradas o 10MB, lo que se cumpla primero). Nunca se salta un
            # entry para "rellenar" con uno mas chico despues -- el orden de
            # prioridad importa mas que aprovechar el cupo al maximo.
            header_line = (header.rstrip("\n") + "\n") if header else ""
            included: list[tuple[str, float, dict, str]] = []
            total_bytes = len(header_line.encode("utf-8"))
            for value, sort_key, meta in live:
                if self.max_records and len(included) >= self.max_records:
                    break
                line = self._render_line(value, sort_key, meta) + "\n"
                line_bytes = len(line.encode("utf-8"))
                if self.max_bytes and total_bytes + line_bytes > self.max_bytes:
                    break
                included.append((value, sort_key, meta, line))
                total_bytes += line_bytes

            skipped_capacity = len(live) - len(included)

            # Los vencidos se sacan de la memoria del writer, no solo del
            # archivo: si no, un rebuild posterior los volveria a evaluar (sin
            # costo real, pero por prolijidad -- ya se decidio su destino).
            included_values = {value for value, _, _, _ in included}
            self._values = {v: (sk, m) for v, (sk, m) in self._values.items() if v in included_values}

            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp_path = self.path + ".tmp"
            # Escritura a archivo temporal + os.replace: un lector externo
            # (el destino que sirve este TXT) nunca ve un archivo a medio
            # escribir.
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                if header_line:
                    f.write(header_line)
                for _, _, _, line in included:
                    f.write(line)
            os.replace(tmp_path, self.path)
            # Persistir DESPUES de escribir el archivo principal: si el
            # proceso muere entre medio, el peor caso es un sidecar
            # desactualizado (se degrada a -inf para lo que falte, nunca un
            # crash), nunca un `.txt` inconsistente con su propio sidecar.
            self._write_meta()

            return FeedWriteResult(written=len(included), skipped_capacity=skipped_capacity, expired=expired_count)


class FeedWriterRegistry:
    """Un `FeedWriter` por subtipo (`md5.txt`, `sha512.txt`, `ip.txt`...):
    nunca mezclar algoritmos/subtipos en un mismo archivo por defecto."""

    def __init__(
        self,
        base_dir: str,
        *,
        max_records: int = 0,
        max_bytes: int = 0,
        overflow_strategy: OverflowStrategy = "newest_first",
        extension: str = "txt",
        render_line: Optional[RenderLine] = None,
        parse_line: Optional[ParseLine] = None,
        header: str = "",
        subtype_max_records: Optional[dict] = None,
    ):
        self.base_dir = base_dir
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.overflow_strategy = overflow_strategy
        self.extension = extension
        self._render_line = render_line
        self._parse_line = parse_line
        self._header = header
        # subtype -> cantidad, viene de la politica activa del destino (ver
        # hub/policy_store.py::PolicyVersion.max_records): un tope propio por
        # subtipo, que pisa `max_records` (el default parejo del destino)
        # solo para los subtipos que lo declaran.
        self.subtype_max_records = subtype_max_records or {}
        self._writers: dict[str, FeedWriter] = {}

    def get(self, subtype: str) -> FeedWriter:
        if subtype not in self._writers:
            # Un writer por subtipo, creado on-demand y cacheado: evita abrir
            # archivos para subtipos que nunca reciben eventos.
            path = os.path.join(self.base_dir, f"{subtype}.{self.extension}")
            self._writers[subtype] = FeedWriter(
                path,
                max_records=self.subtype_max_records.get(subtype, self.max_records),
                max_bytes=self.max_bytes,
                overflow_strategy=self.overflow_strategy,
                render_line=self._render_line,
                parse_line=self._parse_line,
                header=self._header,
            )
        return self._writers[subtype]

    def rebuild_all(self) -> dict[str, FeedWriteResult]:
        return {subtype: writer.rebuild() for subtype, writer in self._writers.items()}
