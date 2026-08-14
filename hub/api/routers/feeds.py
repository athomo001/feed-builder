"""Endpoints de solo gestion (listar, previsualizar, reconstruir) sobre los
destinos `txt_feed`. Un feed es un archivo por subtipo de IOC, asi que
`feed_id` se codifica como `{destination_id}::{subtype}` en vez de tener un
identificador propio, para no requerir una tabla extra solo para nombrarlos.

Autor: Athan Espinoza
"""
import os

from fastapi import APIRouter, Depends

from hub.adapters.txt_feed_adapter import TxtFeedAdapter
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.destinations_store import get_destination, list_destinations

router = APIRouter(prefix="/admin/api/v1/feeds")

_SEP = "::"


def _feed_dir(state: APIState, destination_id: str) -> str:
    return os.path.join(state.config.txt_feed_dir, destination_id)


def _parse_feed_id(feed_id: str) -> tuple[str, str]:
    # feed_id no es una columna real en ningun lado: se reconstruye desde
    # destination_id/subtype cada vez, asi que aca es donde se valida que
    # el caller lo haya armado con el separador esperado.
    if _SEP not in feed_id:
        raise APIError(400, "Bad Request", "feed_id debe tener la forma 'destination_id::subtype'", error_code="invalid_feed_id")
    destination_id, subtype = feed_id.split(_SEP, 1)
    return destination_id, subtype


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    # Los feeds no tienen tabla propia: se descubren recorriendo el
    # filesystem de cada destino txt_feed, asi que un destino sin carpeta
    # todavia (nunca escribio nada) simplemente no aporta feeds, sin error.
    feeds = []
    for destination in list_destinations(state.destinations_conn):
        if destination.adapter != "txt_feed":
            continue
        feed_dir = _feed_dir(state, destination.destination_id)
        if not os.path.isdir(feed_dir):
            continue
        for name in sorted(os.listdir(feed_dir)):
            if not name.endswith(".txt"):
                continue
            subtype = name[: -len(".txt")]
            with open(os.path.join(feed_dir, name), encoding="utf-8") as f:
                count = sum(1 for _ in f)
            feeds.append(
                {
                    "feed_id": f"{destination.destination_id}{_SEP}{subtype}",
                    "destination_id": destination.destination_id,
                    "subtype": subtype,
                    "entries": count,
                }
            )
    return feeds


@router.get("/{feed_id}/preview")
def preview(feed_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    destination_id, subtype = _parse_feed_id(feed_id)
    path = os.path.join(_feed_dir(state, destination_id), f"{subtype}.txt")
    if not os.path.exists(path):
        raise APIError(404, "Not Found", f"feed '{feed_id}' no existe", error_code="feed_not_found")
    with open(path, encoding="utf-8") as f:
        lines = [next(f, None) for _ in range(20)]
    return {"feed_id": feed_id, "preview": [line.rstrip("\n") for line in lines if line is not None]}


@router.post("/{feed_id}/rebuild")
def rebuild(feed_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("operator"))):
    # Rebuild reescribe el archivo del feed, a diferencia de list/preview que
    # solo leen: por eso exige "operator" en vez de "viewer".
    destination_id, subtype = _parse_feed_id(feed_id)
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None or destination.adapter != "txt_feed":
        raise APIError(404, "Not Found", f"feed '{feed_id}' no existe", error_code="feed_not_found")

    adapter = TxtFeedAdapter(destination, base_dir=state.config.txt_feed_dir)
    result = adapter.registry.get(subtype).rebuild()  # el writer ya se sembro desde disco al construirse
    return {"feed_id": feed_id, "written": result.written, "skipped_capacity": result.skipped_capacity}
