"""Endpoints de salud y metricas del Admin API. Liveness/readiness quedan
sin auth porque un orquestador de contenedores no manda Bearer token al
hacer el probe; `/status` y `/metrics` si requieren rol `viewer` como el
resto de endpoints de solo lectura, ya que exponen detalle operativo del
Hub que no deberia ser publico.

Autor: Athan Espinoza
"""
import sqlite3
from collections import Counter

from fastapi import APIRouter, Depends, Response

from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.service import _heartbeat_path, heartbeat_age_seconds, is_healthy

router = APIRouter()


@router.get("/healthz/liveness")
def liveness():
    # Siempre 200 mientras el proceso responda: liveness solo debe fallar si
    # el proceso esta trabado, no si una dependencia (DB, OpenCTI) esta caida
    # -- eso lo cubre readiness, para no forzar un reinicio innecesario.
    return {"status": "ok"}


@router.get("/healthz/readiness")
def readiness(response: Response, state: APIState = Depends(get_state)):
    # Un SELECT trivial contra el ledger es suficiente para detectar que la
    # conexion SQLite quedo inutilizable (disco lleno, archivo corrupto,
    # etc.); 503 le indica al orquestador que saque el pod de rotacion.
    try:
        state.ledger_conn.execute("SELECT 1")
    except sqlite3.Error:
        response.status_code = 503
        return {"status": "unhealthy", "detail": "ledger storage unavailable"}
    return {"status": "ready"}


@router.get("/admin/api/v1/status")
def status(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    heartbeat_path = _heartbeat_path(state.config)
    age = heartbeat_age_seconds(heartbeat_path)
    return {
        "ingestion": {
            "heartbeat_age_seconds": age,
            "healthy": is_healthy(heartbeat_path, max_age_seconds=600),
        },
        "source_id": state.config.source_id,
    }


@router.get("/admin/api/v1/metrics")
def metrics(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    # Las labels del contador solo llevan destination_id y estado, nunca el
    # valor del IOC, un token o una URL con secretos: estas metricas pueden
    # terminar en un backend externo (Prometheus) fuera del control del Hub.
    rows = state.ledger_conn.execute("SELECT state, destination_id FROM event_ledger").fetchall()
    counts = Counter((row[0], row[1]) for row in rows)

    lines = [
        "# HELP hub_deliveries_total Entregas por destino y estado.",
        "# TYPE hub_deliveries_total counter",
    ]
    for (delivery_state, destination_id), count in sorted(counts.items()):
        lines.append(f'hub_deliveries_total{{destination="{destination_id}",status="{delivery_state}"}} {count}')

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
