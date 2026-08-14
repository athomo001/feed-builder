"""spec/08-API-SECURITY.md endpoints de `/destinations`; roles: lectura
`viewer`, escritura `security-admin` ("destinos, credenciales, auth y
roles"), pausar/reanudar `operator`.
"""
import ipaddress
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from hub.adapters.factory import build_adapter
from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.idempotency import with_idempotency
from hub.api.schemas import DestinationCreate, DestinationTestRequest, DestinationUpdate
from hub.destinations_store import (
    Destination,
    get_destination,
    list_destinations,
    set_paused,
    upsert_destination,
)

router = APIRouter(prefix="/admin/api/v1/destinations")


def _is_private_endpoint(url: str) -> bool:
    """SSRF minimo (spec/08 'Seguridad de destino: SSRF protection'):
    bloquea literales de loopback/red privada en el endpoint configurado.
    No resuelve DNS -- no protege contra DNS rebinding, solo el caso obvio
    de un operador apuntando el Hub a si mismo o a la red interna."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return True
    if not host:
        return True
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _build_adapter(destination: Destination, state: APIState):
    return build_adapter(destination, txt_feed_dir=state.config.txt_feed_dir, taxii_conn=state.taxii_conn)


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    return [d.model_dump(mode="json") for d in list_destinations(state.destinations_conn)]


@router.get("/{destination_id}")
def get_one(destination_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' no existe", error_code="destination_not_found")
    return destination.model_dump(mode="json")


@router.post("", status_code=201)
def create(
    payload: DestinationCreate,
    request: Request,
    state: APIState = Depends(get_state),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("security-admin")),
):
    def compute():
        if get_destination(state.destinations_conn, payload.destination_id) is not None:
            raise APIError(
                409, "Conflict", f"destination '{payload.destination_id}' ya existe", error_code="destination_exists"
            )
        now = datetime.now(timezone.utc)
        destination = Destination(**payload.model_dump(), paused=False, created_at=now, updated_at=now)
        upsert_destination(state.destinations_conn, destination)
        write_audit(
            request, state, actor=token, action="destination.create",
            resource_type="destination", resource_id=destination.destination_id,
            after=destination.model_dump(mode="json"),
        )
        return 201, destination.model_dump(mode="json")

    status_code, body = with_idempotency(
        state, key=idempotency_key, endpoint="POST /destinations", payload=payload.model_dump(mode="json"), compute=compute
    )
    return JSONResponse(status_code=status_code, content=body)


@router.put("/{destination_id}")
def update(
    destination_id: str,
    payload: DestinationUpdate,
    request: Request,
    state: APIState = Depends(get_state),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    token=Depends(require_role("security-admin")),
):
    def compute():
        existing = get_destination(state.destinations_conn, destination_id)
        if existing is None:
            raise APIError(404, "Not Found", f"destination '{destination_id}' no existe", error_code="destination_not_found")
        updates = payload.model_dump(exclude_unset=True)
        updated = existing.model_copy(update={**updates, "updated_at": datetime.now(timezone.utc)})
        upsert_destination(state.destinations_conn, updated)
        write_audit(
            request, state, actor=token, action="destination.update",
            resource_type="destination", resource_id=destination_id,
            before=existing.model_dump(mode="json"), after=updated.model_dump(mode="json"),
        )
        return 200, updated.model_dump(mode="json")

    status_code, body = with_idempotency(
        state,
        key=idempotency_key,
        endpoint=f"PUT /destinations/{destination_id}",
        payload=payload.model_dump(mode="json"),
        compute=compute,
    )
    return JSONResponse(status_code=status_code, content=body)


@router.post("/{destination_id}/test")
def test(
    destination_id: str,
    payload: DestinationTestRequest,
    state: APIState = Depends(get_state),
    _token=Depends(require_role("security-admin")),
):
    # spec/05 "El boton de prueba... payload sintetico marcado como test y
    # no publicar un IOC real": esto valida config/alcanzabilidad basica,
    # nunca envia un IOC real ni ejercita adapter.send().
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' no existe", error_code="destination_not_found")

    errors = []
    if destination.endpoint and _is_private_endpoint(destination.endpoint) and not payload.allow_private_network:
        errors.append("endpoint apunta a loopback/red privada; pasa allow_private_network=true si es intencional")

    adapter = _build_adapter(destination, state)
    errors.extend(adapter.validate())
    healthy = adapter.healthcheck() if not errors else False

    return {"destination_id": destination_id, "synthetic": True, "errors": errors, "healthy": healthy}


@router.post("/{destination_id}/pause")
def pause(destination_id: str, request: Request, state: APIState = Depends(get_state), token=Depends(require_role("operator"))):
    destination = set_paused(state.destinations_conn, destination_id, True)
    if destination is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' no existe", error_code="destination_not_found")
    write_audit(
        request, state, actor=token, action="destination.pause",
        resource_type="destination", resource_id=destination_id,
    )
    return destination.model_dump(mode="json")


@router.post("/{destination_id}/resume")
def resume(destination_id: str, request: Request, state: APIState = Depends(get_state), token=Depends(require_role("operator"))):
    destination = set_paused(state.destinations_conn, destination_id, False)
    if destination is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' no existe", error_code="destination_not_found")
    write_audit(
        request, state, actor=token, action="destination.resume",
        resource_type="destination", resource_id=destination_id,
    )
    return destination.model_dump(mode="json")
