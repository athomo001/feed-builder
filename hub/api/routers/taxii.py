"""Servidor TAXII 2.1 minimo, de solo lectura (spec/09-ROADMAP-ACCEPTANCE.md
Entrega 4 "Integraciones", alto esfuerzo: "Cisco Threat Intelligence
Director via TAXII"). Decision de esta entrega: el Hub construye su propio
servidor TAXII en vez de depender solo del TAXII nativo de OpenCTI (spec/02
lo prefiere para consumo directo de OpenCTI por un tercero, pero aca Cisco
TID necesita apuntar al Hub -- es el Hub quien aplica politicas/filtrado por
destino antes de exponer el IOC, no OpenCTI directamente).

Un solo API Root (`hub`); una coleccion por destino con `adapter=taxii2`
(id de coleccion = `destination_id`). Solo lectura -- el Hub es productor,
nunca acepta STIX entrante por TAXII (`POST .../objects/` devuelve 405).

Auth: HTTP Basic contra el `credential_ref` del propio destino (spec/05
"escalera de autenticacion cuando el fabricante hace poll": Basic Auth es la
primera opcion cuando el consumidor lo soporta). El secreto resuelto via
`credential_ref` sigue la convencion `usuario:password` (mismo `env://` de
siempre, spec/08 "el secreto se referencia por credential_ref, nunca en
claro"). Discovery/api-root/listado de colecciones quedan sin autenticar
-- solo exponen metadata (titulos), nunca valores de IOC; el gate real esta
en los dos endpoints que devuelven objetos.
"""
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.credentials import CredentialResolutionError, resolve_credential_ref
from hub.destinations_store import Destination, get_destination, list_destinations
from hub.taxii_store import list_objects

router = APIRouter(prefix="/taxii2", tags=["taxii"])

_MEDIA_TYPE = "application/taxii+json;version=2.1"
_API_ROOT = "hub"

_security = HTTPBasic(auto_error=False)


def _require_api_root(api_root: str) -> None:
    if api_root != _API_ROOT:
        raise APIError(404, "Not Found", f"api_root '{api_root}' no existe", error_code="api_root_not_found")


def _require_collection_auth(
    destination_id: str, state: APIState, credentials: Optional[HTTPBasicCredentials]
) -> Destination:
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None or destination.adapter != "taxii2" or not destination.enabled:
        raise APIError(404, "Not Found", f"coleccion '{destination_id}' no existe", error_code="collection_not_found")
    if not destination.credential_ref:
        # Sin credential_ref: cerrado por defecto, igual que cualquier otro
        # destino file_feed sin auth explicita (spec/08 "nunca publicar un
        # feed... sin al menos un control de acceso").
        raise APIError(401, "Unauthorized", "coleccion sin credencial configurada", error_code="unauthorized")
    try:
        secret = resolve_credential_ref(destination.credential_ref)
    except CredentialResolutionError as e:
        raise APIError(401, "Unauthorized", f"no se pudo resolver la credencial: {e}", error_code="unauthorized")
    expected_user, _, expected_password = secret.partition(":")
    if (
        credentials is None
        or not secrets.compare_digest(credentials.username, expected_user)
        or not secrets.compare_digest(credentials.password, expected_password)
    ):
        raise APIError(401, "Unauthorized", "Basic Auth invalido para esta coleccion", error_code="unauthorized")
    return destination


@router.get("/")
def discovery():
    return JSONResponse(
        {
            "title": "OpenCTI IOC Distribution Hub",
            "default": f"/taxii2/{_API_ROOT}/",
            "api_roots": [f"/taxii2/{_API_ROOT}/"],
        },
        media_type=_MEDIA_TYPE,
    )


@router.get("/{api_root}/")
def api_root_info(api_root: str):
    _require_api_root(api_root)
    return JSONResponse(
        {"title": "hub", "versions": ["application/taxii+json;version=2.1"], "max_content_length": 104_857_600},
        media_type=_MEDIA_TYPE,
    )


@router.get("/{api_root}/collections/")
def list_collections(api_root: str, state: APIState = Depends(get_state)):
    _require_api_root(api_root)
    destinations = [d for d in list_destinations(state.destinations_conn, enabled=True) if d.adapter == "taxii2"]
    return JSONResponse(
        {
            "collections": [
                {"id": d.destination_id, "title": d.name, "can_read": True, "can_write": False}
                for d in destinations
            ]
        },
        media_type=_MEDIA_TYPE,
    )


@router.get("/{api_root}/collections/{destination_id}/")
def get_collection(
    api_root: str,
    destination_id: str,
    state: APIState = Depends(get_state),
    credentials: Optional[HTTPBasicCredentials] = Depends(_security),
):
    _require_api_root(api_root)
    destination = _require_collection_auth(destination_id, state, credentials)
    return JSONResponse(
        {"id": destination.destination_id, "title": destination.name, "can_read": True, "can_write": False},
        media_type=_MEDIA_TYPE,
    )


@router.get("/{api_root}/collections/{destination_id}/objects/")
def list_collection_objects(
    api_root: str,
    destination_id: str,
    added_after: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1),
    state: APIState = Depends(get_state),
    credentials: Optional[HTTPBasicCredentials] = Depends(_security),
):
    _require_api_root(api_root)
    _require_collection_auth(destination_id, state, credentials)
    objects, more = list_objects(state.taxii_conn, destination_id, added_after=added_after, limit=limit)
    return JSONResponse({"objects": objects, "more": more}, media_type=_MEDIA_TYPE)


@router.post("/{api_root}/collections/{destination_id}/objects/")
def reject_incoming_objects(api_root: str, destination_id: str):
    raise APIError(
        405, "Method Not Allowed",
        "el Hub es productor de esta coleccion; no acepta objetos STIX entrantes por TAXII",
        error_code="taxii_write_not_supported",
    )
