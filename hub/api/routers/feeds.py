"""Endpoints de solo gestion (listar, previsualizar, reconstruir) sobre los
destinos que materializan un feed a disco: txt_feed, csv_feed, mikrotik_rsc,
wazuh_cdb y stix_bundle_feed (ver hub/adapters/factory.py). Un feed es un
archivo por subtipo de IOC (excepto stix_bundle_feed, que junta todo en un
unico bundle.json por destino -- un bundle STIX mezcla tipos), asi que
`feed_id` se codifica como `{destination_id}::{subtype}` en vez de tener un
identificador propio, para no requerir una tabla extra solo para nombrarlos.

`public_path` es la ruta bajo la que nginx sirve ese mismo archivo (ver
nginx.conf `location ^~ /feeds/`): el link que un firewall/appliance hace
*poll* para consumir el feed (EDL de Palo Alto, pfBlockerNG, MikroTik `/tool
fetch`), o que un operador copia a mano cuando el destino no hace poll el
solo (Wazuh, ver hub/adapters/wazuh_adapter.py) -- spec/05-FORMATS-DESTINATIONS.md.

Autor: Athan Espinoza
"""
import json
import os

from fastapi import APIRouter, Depends

from hub.adapters.factory import build_adapter
from hub.api.auth import require_role
from hub.api.deps import APIState, get_state
from hub.api.errors import APIError
from hub.destinations_store import get_destination, list_destinations
from hub.feeds_maintenance import FILE_ADAPTER_TYPES, SUBTYPE_ADAPTER_EXTENSIONS, _BUNDLE_ADAPTER, _BUNDLE_SUBTYPE
from hub.feeds_maintenance import rebuild_all_feeds_for_destination as _rebuild_all_feeds_for_destination
from hub.policy_store import get_active_version_for_destination

router = APIRouter(prefix="/admin/api/v1/feeds")

_SEP = "::"
# Alias local: el resto de este modulo (list_all/preview/_feed_file_path) ya
# usaba este nombre antes de que la constante se moviera a
# hub/feeds_maintenance.py (para poder compartirla con el loop periodico de
# hub/service.py sin acoplar ese proceso al Admin API).
_SUBTYPE_ADAPTER_EXTENSIONS = SUBTYPE_ADAPTER_EXTENSIONS


def _feed_dir(state: APIState, destination_id: str) -> str:
    return os.path.join(state.config.txt_feed_dir, destination_id)


def _public_path(destination_id: str, filename: str) -> str:
    return f"/feeds/{destination_id}/{filename}"


def _parse_feed_id(feed_id: str) -> tuple[str, str]:
    # feed_id no es una columna real en ningun lado: se reconstruye desde
    # destination_id/subtype cada vez, asi que aca es donde se valida que
    # el caller lo haya armado con el separador esperado.
    if _SEP not in feed_id:
        raise APIError(400, "Bad Request", "feed_id debe tener la forma 'destination_id::subtype'", error_code="invalid_feed_id")
    destination_id, subtype = feed_id.split(_SEP, 1)
    return destination_id, subtype


def _feed_file_path(state: APIState, destination, subtype: str) -> str:
    if destination.adapter == _BUNDLE_ADAPTER:
        return os.path.join(_feed_dir(state, destination.destination_id), "bundle.json")
    ext = _SUBTYPE_ADAPTER_EXTENSIONS[destination.adapter]
    return os.path.join(_feed_dir(state, destination.destination_id), f"{subtype}.{ext}")


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    # Los feeds no tienen tabla propia: se descubren recorriendo el
    # filesystem de cada destino de archivo -- PERO ademas de lo que ya
    # existe en disco, tambien se listan los subtipos que la politica activa
    # ya permite aunque todavia no haya llegado ni un evento real (entries=0,
    # sin archivo en disco todavia). Sin esto, un operador no tenia forma de
    # copiar el link de un feed hasta que llegara data real -- reportado
    # 2026-08-18: "agregue CVE en destino y politica y no lo veo como link en
    # Feeds materializados". El link puede devolver 404 hasta que se
    # materialice (evento real o "Forzar reconstruccion"), pero ya se puede
    # copiar para configurar el consumidor de antemano.
    feeds = []
    for destination in list_destinations(state.destinations_conn):
        if destination.adapter == _BUNDLE_ADAPTER:
            feed_dir = _feed_dir(state, destination.destination_id)
            path = os.path.join(feed_dir, "bundle.json")
            entries = 0
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        entries = len(json.load(f).get("objects", []))
                except (OSError, ValueError):
                    entries = 0
            feeds.append(
                {
                    "feed_id": f"{destination.destination_id}{_SEP}{_BUNDLE_SUBTYPE}",
                    "destination_id": destination.destination_id,
                    "subtype": _BUNDLE_SUBTYPE,
                    "entries": entries,
                    "public_path": _public_path(destination.destination_id, "bundle.json"),
                }
            )
            continue

        ext = _SUBTYPE_ADAPTER_EXTENSIONS.get(destination.adapter)
        if ext is None:
            continue
        suffix = f".{ext}"
        feed_dir = _feed_dir(state, destination.destination_id)
        existing_subtypes = set()
        if os.path.isdir(feed_dir):
            for name in os.listdir(feed_dir):
                if name.endswith(suffix):
                    existing_subtypes.add(name[: -len(suffix)])

        policy = get_active_version_for_destination(state.policies_conn, destination.destination_id)
        allowed_subtypes = set()
        if policy is not None:
            for allowed in policy.allowed_iocs:
                allowed_subtypes.update(allowed.subtypes)

        for subtype in sorted(existing_subtypes | allowed_subtypes):
            name = f"{subtype}.{ext}"
            path = os.path.join(feed_dir, name)
            count = 0
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    count = sum(1 for _ in f)
            feeds.append(
                {
                    "feed_id": f"{destination.destination_id}{_SEP}{subtype}",
                    "destination_id": destination.destination_id,
                    "subtype": subtype,
                    "entries": count,
                    "public_path": _public_path(destination.destination_id, name),
                }
            )
    return feeds


@router.get("/{feed_id}/preview")
def preview(feed_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
    destination_id, subtype = _parse_feed_id(feed_id)
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None or destination.adapter not in FILE_ADAPTER_TYPES:
        raise APIError(404, "Not Found", f"feed '{feed_id}' no existe", error_code="feed_not_found")
    path = _feed_file_path(state, destination, subtype)
    if not os.path.exists(path):
        raise APIError(404, "Not Found", f"feed '{feed_id}' no existe", error_code="feed_not_found")
    with open(path, encoding="utf-8") as f:
        lines = [next(f, None) for _ in range(20)]
    return {"feed_id": feed_id, "preview": [line.rstrip("\n") for line in lines if line is not None]}


# Llamado desde hub/api/routers/policies.py cuando se publica/revierte una
# version: sin esto, un cambio de politica (TTL, cantidad, tipos permitidos)
# quedaba guardado pero invisible en los archivos ya materializados hasta
# que llegara un evento nuevo o alguien apretara "Forzar reconstruccion" a
# mano -- bug real reportado por el operador (2026-08-18): "configure
# cantidad 10 y sigo viendo 264, no esta tomando en cuenta la politica".
def rebuild_all_feeds_for_destination(state: APIState, destination_id: str) -> None:
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None:
        return
    _rebuild_all_feeds_for_destination(
        destination,
        txt_feed_dir=state.config.txt_feed_dir,
        policies_conn=state.policies_conn,
        taxii_conn=state.taxii_conn,
        secrets_conn=state.secrets_conn,
        cipher=state.secret_cipher,
    )


@router.post("/{feed_id}/rebuild")
def rebuild(feed_id: str, state: APIState = Depends(get_state), _token=Depends(require_role("operator"))):
    # Rebuild reescribe el archivo del feed, a diferencia de list/preview que
    # solo leen: por eso exige "operator" en vez de "viewer".
    destination_id, subtype = _parse_feed_id(feed_id)
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None or destination.adapter not in FILE_ADAPTER_TYPES:
        raise APIError(404, "Not Found", f"feed '{feed_id}' no existe", error_code="feed_not_found")

    policy = get_active_version_for_destination(state.policies_conn, destination_id)
    adapter = build_adapter(destination, txt_feed_dir=state.config.txt_feed_dir, policy=policy)
    if destination.adapter == _BUNDLE_ADAPTER:
        result = adapter.writer.rebuild()  # el writer ya se sembro desde disco al construirse
    else:
        result = adapter.registry.get(subtype).rebuild()
    return {"feed_id": feed_id, "written": result.written, "skipped_capacity": result.skipped_capacity}
