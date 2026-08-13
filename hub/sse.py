"""Framer SSE puro (spec/03-ARCHITECTURE.md "Limites": tamano maximo por
evento SSE, Entrega 1 "Limites SSE, backpressure").

Puerto del `sse_size_check` de opencti_feed_builder.py, pero desacoplado de
`requests`: toma cualquier iterable de lineas crudas (bytes) y entrega
eventos completos ya ensamblados, para poder probarlo sin red y para poder
alimentarlo tanto desde un stream real como desde un fixture en tests.

Tambien conserva el campo `id:` de cada evento SSE (RFC-ish, no especifico
de OpenCTI): es el cursor de recuperacion del Live Stream (spec/02 "Cursor
durable y recuperacion"; enviarlo de vuelta como `Last-Event-ID` en la
reconexion es lo que evita reprocesar todo el stream desde el inicio).
"""
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

DEFAULT_MAX_LINE_BYTES = 256 * 1024
DEFAULT_MAX_EVENT_BYTES = 2 * 1024 * 1024


@dataclass
class SSEEvent:
    id: Optional[str]
    data: bytes


def iter_sse_events(
    lines: Iterable[Optional[bytes]],
    *,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
) -> Iterator[SSEEvent]:
    """Ensambla lineas `data:` en eventos completos.

    Una linea o evento que exceda el limite se descarta entero (nunca se
    entrega un evento parcial), igual que hacia el script legado.
    """
    event_parts: list[bytes] = []
    event_id: Optional[str] = None
    event_oversized = False

    def _reset():
        nonlocal event_parts, event_id, event_oversized
        event_parts, event_id, event_oversized = [], None, False

    for raw in lines:
        if raw is None:
            continue

        if raw == b"":
            if not event_oversized and event_parts:
                yield SSEEvent(id=event_id, data=b"\n".join(event_parts))
            _reset()
            continue

        if raw.startswith(b":"):
            continue

        if raw.startswith(b"id:"):
            event_id = raw[3:].strip().decode("utf-8", errors="replace")
            continue

        if raw.startswith(b"data:") and not event_oversized:
            line = raw[5:].lstrip()
            if len(line) > max_line_bytes:
                event_oversized = True
                event_parts = []
                continue
            projected = sum(len(p) for p in event_parts) + len(line)
            if projected > max_event_bytes:
                event_oversized = True
                event_parts = []
                continue
            event_parts.append(line)

    # El stream puede cerrar sin una linea vacia final: se entrega lo que quedo.
    if event_parts and not event_oversized:
        yield SSEEvent(id=event_id, data=b"\n".join(event_parts))
