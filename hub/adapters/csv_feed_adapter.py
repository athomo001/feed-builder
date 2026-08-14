"""Adaptador CSV multi-columna (spec/09-ROADMAP-ACCEPTANCE.md Entrega 4
"Integraciones", bajo esfuerzo: Check Point Custom Intelligence Feed).

A diferencia de `TxtFeedAdapter` (una linea = un valor crudo), cada linea es
una fila CSV con columnas configurables (spec/05-FORMATS-DESTINATIONS.md
"CSV": "Columnas configurables... Separador, quoting, encoding y header
configurables... Proteccion contra CSV injection en campos textuales...
Columnas minimas recomendadas: family, subtype, value, score, confidence,
marking, created_at, valid_until").

Check Point no tiene un esquema de columnas confirmado contra un parser real
(spec/05 lo deja como "decision de implementacion" -- no hay tope de
registros ni nombre de columna documentado por el fabricante); se usan las
columnas recomendadas genericas de 05 como default, configurables por
destino via `format_options.columns`.
"""
import csv
import io
import os
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.txt_feed import FeedWriterRegistry

DEFAULT_COLUMNS = ["family", "subtype", "value", "score", "confidence", "marking", "created_at", "valid_until"]

# spec/05 "Proteccion contra CSV injection": un campo que empieza con
# =, +, - o @ puede ejecutarse como formula al abrir el CSV en Excel/Sheets.
_INJECTION_PREFIXES = ("=", "+", "-", "@")


def _sanitize_cell(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _INJECTION_PREFIXES:
        return "'" + text
    return text


class CsvFeedAdapter:
    def __init__(self, destination: Destination, *, base_dir: str):
        self.destination = destination
        self.base_dir = os.path.join(base_dir, destination.destination_id)
        opts = destination.format_options or {}
        self.columns: list[str] = opts.get("columns") or list(DEFAULT_COLUMNS)
        self.delimiter: str = opts.get("delimiter", ",")
        self.include_header: bool = opts.get("include_header", True)
        quoting_name = str(opts.get("quoting", "minimal")).upper()
        self.quoting = getattr(csv, f"QUOTE_{quoting_name}", csv.QUOTE_MINIMAL)
        header = self.delimiter.join(self.columns) if self.include_header else ""
        self.registry = FeedWriterRegistry(
            self.base_dir,
            max_records=destination.capacity.get("max_records_per_file", 0),
            overflow_strategy=destination.capacity.get("overflow_strategy", "newest_first"),
            extension="csv",
            render_line=self._render_line,
            parse_line=self._parse_line,
            header=header,
        )

    def _render_line(self, value: str, _sort_key: float, meta: dict) -> str:
        row = []
        for column in self.columns:
            if column == "value":
                row.append(_sanitize_cell(value))
            elif column == "marking":
                row.append(_sanitize_cell(",".join(meta.get("markings", []))))
            else:
                row.append(_sanitize_cell(meta.get(column)))
        buf = io.StringIO()
        csv.writer(buf, delimiter=self.delimiter, quoting=self.quoting, lineterminator="\n").writerow(row)
        return buf.getvalue().rstrip("\n")

    def _parse_line(self, line: str) -> Optional[str]:
        if self.include_header and line == self.delimiter.join(self.columns):
            return None
        if "value" not in self.columns:
            return None
        try:
            row = next(csv.reader([line], delimiter=self.delimiter, quoting=self.quoting))
        except (csv.Error, StopIteration):
            return None
        index = self.columns.index("value")
        if index >= len(row):
            return None
        value = row[index]
        return value[1:] if value.startswith("'") and value[1:2] in _INJECTION_PREFIXES else value

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("csv",):
            errors.append("adapter 'csv_feed' solo soporta destination.format == 'csv'")
        if "value" not in self.columns:
            errors.append("format_options.columns debe incluir 'value'")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return {
            "subtype": event.subtype,
            "value": event.normalized_value,
            "sort_key": event.modified_at.timestamp(),
            "meta": {
                "family": event.family.value,
                "subtype": event.subtype,
                "score": event.score,
                "confidence": event.confidence,
                "markings": event.markings,
                "created_at": event.created_at.isoformat(),
                "valid_until": event.valid_until.isoformat() if event.valid_until else "",
            },
        }

    def send(self, rendered: dict, *, idempotency_key: Optional[str] = None) -> AdapterSendResult:
        writer = self.registry.get(rendered["subtype"])
        writer.upsert(rendered["value"], sort_key=rendered["sort_key"], meta=rendered["meta"])
        results = self.registry.rebuild_all()
        written = results.get(rendered["subtype"])
        return AdapterSendResult(success=True, detail=f"written={written.written if written else 0}")

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        writer = self.registry.get(event.subtype)
        writer.remove(event.normalized_value)
        self.registry.rebuild_all()
        return AdapterSendResult(success=True, detail="removed")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            probe = os.path.join(self.base_dir, ".healthcheck")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return True
        except OSError:
            return False

    def close(self) -> None:
        return None
