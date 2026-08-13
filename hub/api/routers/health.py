"""spec/06-OBSERVABILITY.md "Endpoints y senales de salud"; spec/08 `GET
/status`, `GET /metrics`. Liveness/readiness quedan sin auth (un
orquestador de contenedores no manda Bearer token); `/status` y `/metrics`
requieren rol `viewer` como el resto de lectura (spec/08 roles).
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
    return {"status": "ok"}


@router.get("/healthz/readiness")
def readiness(response: Response, state: APIState = Depends(get_state)):
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
    # spec/06 "No incluir valores IOC, tokens, URLs con secretos... en labels".
    rows = state.ledger_conn.execute("SELECT state, destination_id FROM event_ledger").fetchall()
    counts = Counter((row[0], row[1]) for row in rows)

    lines = [
        "# HELP hub_deliveries_total Entregas por destino y estado.",
        "# TYPE hub_deliveries_total counter",
    ]
    for (delivery_state, destination_id), count in sorted(counts.items()):
        lines.append(f'hub_deliveries_total{{destination="{destination_id}",status="{delivery_state}"}} {count}')

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
