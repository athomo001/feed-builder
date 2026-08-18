"""Endpoints de gestion de destinos de distribucion. Lectura requiere
`viewer`; crear/actualizar un destino requiere `security-admin` porque ahi
se configuran credenciales, endpoint y auth; pausar/reanudar solo requiere
`operator` porque es una operacion reversible que no toca la configuracion
de seguridad del destino.

Autor: Athan Espinoza
"""
import ipaddress
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from hub.adapters.factory import build_adapter
from hub.api.audit import write_audit
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.api.idempotency import with_idempotency
from hub.api.schemas import DestinationCreate, DestinationTestRequest, DestinationUpdate, DiscardRequest
from hub.destinations_store import (
    Destination,
    delete_destination,
    get_destination,
    list_destinations,
    set_paused,
    upsert_destination,
)
from hub.policy_store import get_active_version_for_destination

router = APIRouter(prefix="/admin/api/v1/destinations")


def _is_private_endpoint(url: str) -> bool:
    """Proteccion SSRF minima: bloquea literales de loopback/red privada en
    el endpoint configurado. No resuelve DNS -- no protege contra DNS
    rebinding, solo cubre el caso obvio de un operador apuntando el Hub a
    si mismo o a la red interna."""
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
    # Centraliza el paso de las conexiones/config que cualquier adapter
    # podria necesitar (txt_feed_dir, taxii, secrets+cipher), para que el
    # router no tenga que saber cuales usa cada tipo de adapter en particular.
    # Se pasa tambien la politica activa del destino: es la unica fuente de
    # verdad de que tipos de IOC le llegan (ver hub/adapters/factory.py).
    policy = get_active_version_for_destination(state.policies_conn, destination.destination_id)
    return build_adapter(
        destination,
        txt_feed_dir=state.config.txt_feed_dir,
        taxii_conn=state.taxii_conn,
        secrets_conn=state.secrets_conn,
        cipher=state.secret_cipher,
        policy=policy,
    )


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
        # exclude_unset=True: solo los campos que el caller efectivamente
        # mando reemplazan al destino existente (PATCH-like), asi un PUT
        # parcial no pisa el resto de la configuracion con sus defaults.
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


@router.delete("/{destination_id}", status_code=204)
def delete(
    destination_id: str,
    payload: DiscardRequest,
    request: Request,
    state: APIState = Depends(get_state),
    token=Depends(require_role("security-admin")),
):
    # Borrado real, pedido explicitamente por el operador (2026-08-18): un
    # destino borrado simplemente deja de aparecer en `list_destinations`, y
    # el pipeline (hub/pipeline.py) ya resuelve esa lista en cada evento, asi
    # que una politica que le apuntaba queda inerte (no rompe nada) en vez de
    # fallar. Historial de entregas pasadas en el ledger no se toca -- sigue
    # existiendo, solo con un destination_id que ya no resuelve a un destino
    # vigente.
    existing = get_destination(state.destinations_conn, destination_id)
    if existing is None:
        raise APIError(404, "Not Found", f"destination '{destination_id}' no existe", error_code="destination_not_found")
    delete_destination(state.destinations_conn, destination_id)
    write_audit(
        request, state, actor=token, action="destination.delete",
        resource_type="destination", resource_id=destination_id,
        before=existing.model_dump(mode="json"), reason=payload.reason,
    )
    return Response(status_code=204)


@router.post("/{destination_id}/test")
def test(
    destination_id: str,
    payload: DestinationTestRequest,
    state: APIState = Depends(get_state),
    _token=Depends(require_role("security-admin")),
):
    # Este endpoint solo valida config/alcanzabilidad basica del destino;
    # nunca envia un IOC real ni ejercita adapter.send(), para que un
    # operador pueda probar un destino nuevo sin filtrar datos reales.
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
