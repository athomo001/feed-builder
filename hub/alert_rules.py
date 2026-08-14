"""Reglas de alerta: cada funcion evalua una condicion de salud del Hub y
decide si dispara una alerta.

Solo se implementan las condiciones que hoy tienen una senal real
disponible sin construir infraestructura nueva: OpenCTI desconectado
(heartbeat), cursor sin avanzar, dead-letter no vacio, destino sin entrega
exitosa reciente y feed sin rebuild reciente. Otras condiciones deseables
(espacio en disco, TLS invalido/credencial rechazada, caida anormal de
volumen historico) se dejan sin regla a proposito -- no se inventan
valores falsos para simular una senal que no existe todavia.

Cada regla es una funcion PURA: recibe datos ya consultados por quien la
llama (sin abrir conexiones ni hacer I/O aca) y devuelve una lista de
`AlertCandidate` (una condicion puede disparar para varios recursos a la
vez -- por ejemplo dead-letter no vacio en dos destinos distintos). Ser
pura las hace triviales de testear sin mockear I/O.

Autor: Athan Espinoza
"""
from dataclasses import dataclass
from typing import Optional

from hub.ledger import LedgerEntry


@dataclass
class AlertCandidate:
    condition: str
    component: str
    resource_id: str
    severity: str
    observed_value: str


# `None` (nunca hubo heartbeat) se trata como "no disparar" en vez de como
# "maximo posible de antiguedad": evita una falsa alerta al arrancar el Hub,
# antes de que llegue el primer heartbeat real de OpenCTI.
def evaluate_opencti_disconnected(heartbeat_age_seconds: Optional[float], *, max_age_seconds: int = 120) -> list[AlertCandidate]:
    if heartbeat_age_seconds is None or heartbeat_age_seconds <= max_age_seconds:
        return []
    return [
        AlertCandidate(
            condition="opencti_disconnected",
            component="ingestion",
            resource_id="opencti",
            severity="critical",
            observed_value=f"{heartbeat_age_seconds:.0f}s sin heartbeat",
        )
    ]


def evaluate_cursor_not_advancing(unchanged_seconds: Optional[float], *, max_unchanged_seconds: int = 300) -> list[AlertCandidate]:
    if unchanged_seconds is None or unchanged_seconds <= max_unchanged_seconds:
        return []
    return [
        AlertCandidate(
            condition="cursor_not_advancing",
            component="ingestion",
            resource_id="cursor",
            severity="critical",
            observed_value=f"{unchanged_seconds:.0f}s sin avanzar",
        )
    ]


def evaluate_dead_letter_nonzero(dead_letters: list[LedgerEntry]) -> list[AlertCandidate]:
    # Se agrupa por destino (no una alerta por entrada dead-letter): un
    # destino con 50 entregas fallidas deberia generar una alerta por
    # destino con el conteo, no 50 alertas individuales que inunden al
    # operador.
    counts: dict[str, int] = {}
    for entry in dead_letters:
        counts[entry.destination_id] = counts.get(entry.destination_id, 0) + 1
    return [
        AlertCandidate(
            condition="dead_letter_nonzero",
            component="destination",
            resource_id=destination_id,
            severity="warning",
            observed_value=str(count),
        )
        for destination_id, count in counts.items()
    ]


def evaluate_destination_delivery_stale(
    last_success_seconds_ago: dict[str, Optional[float]], *, max_age_seconds: int
) -> list[AlertCandidate]:
    """Umbral unico configurable a nivel Hub (`ALERT_DESTINATION_STALE_SECONDS`),
    no un SLO por destino -- un SLO de latencia diferenciado por destino
    todavia no esta resuelto. Esta es una deteccion mas gruesa: "ningun
    destino sin entrega exitosa hace mas de X segundos", igual para todos
    los destinos."""
    return [
        AlertCandidate(
            condition="destination_delivery_stale",
            component="destination",
            resource_id=destination_id,
            severity="warning",
            observed_value=f"{age:.0f}s sin entrega exitosa",
        )
        for destination_id, age in last_success_seconds_ago.items()
        if age is not None and age > max_age_seconds
    ]


def evaluate_feed_stale(feed_age_seconds: dict[str, float], *, max_age_seconds: int) -> list[AlertCandidate]:
    return [
        AlertCandidate(
            condition="feed_stale",
            component="feed",
            resource_id=feed_id,
            severity="warning",
            observed_value=f"{age:.0f}s sin rebuild",
        )
        for feed_id, age in feed_age_seconds.items()
        if age > max_age_seconds
    ]
