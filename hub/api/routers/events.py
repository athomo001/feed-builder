"""Buscador de eventos y su historial de entregas. Esta acotado a las
columnas que el event ledger ya guarda (event_id, stix_id, destino, estado,
fecha): no hay un Canonical Event Store separado, asi que no se puede
buscar todavia por family/subtype/valor del IOC.

Autor: Athan Espinoza
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.delivery import DeliveryState
from hub.ledger import list_deliveries_for_event, search_deliveries

router = APIRouter(prefix="/admin/api/v1/events")


@router.get("")
def search(
    state: APIState = Depends(get_state),
    event_id: Optional[str] = Query(default=None),
    stix_id: Optional[str] = Query(default=None),
    destination_id: Optional[str] = Query(default=None),
    delivery_state: Optional[DeliveryState] = Query(default=None, alias="state"),
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _token=Depends(require_role("viewer")),
):
    entries = search_deliveries(
        state.ledger_conn,
        event_id=event_id,
        stix_id=stix_id,
        destination_id=destination_id,
        state=delivery_state,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [e.model_dump(mode="json") for e in entries]


@router.get("/{event_id}")
def timeline(event_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    # Un mismo evento puede tener una entrega por destino/policy_version: el
    # timeline devuelve todas para poder ver de un vistazo a que destinos
    # llego, cuales fallaron y cuales quedaron pendientes.
    return [e.model_dump(mode="json") for e in list_deliveries_for_event(state.ledger_conn, event_id)]
