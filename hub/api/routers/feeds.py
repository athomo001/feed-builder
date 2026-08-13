"""spec/08-API-SECURITY.md `/feeds`, `/feeds/{feed_id}/preview`,
`/feeds/{feed_id}/rebuild` sobre destinos `txt_feed`. `feed_id` se codifica
como `{destination_id}::{subtype}` (un feed = un archivo por subtipo,
spec/05 'Texto legacy').
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
    if _SEP not in feed_id:
        raise APIError(400, "Bad Request", "feed_id debe tener la forma 'destination_id::subtype'", error_code="invalid_feed_id")
    destination_id, subtype = feed_id.split(_SEP, 1)
    return destination_id, subtype


@router.get("")
def list_all(state: APIState = Depends(get_state), _token=Depends(require_role("viewer"))):
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
    destination_id, subtype = _parse_feed_id(feed_id)
    destination = get_destination(state.destinations_conn, destination_id)
    if destination is None or destination.adapter != "txt_feed":
        raise APIError(404, "Not Found", f"feed '{feed_id}' no existe", error_code="feed_not_found")

    adapter = TxtFeedAdapter(destination, base_dir=state.config.txt_feed_dir)
    result = adapter.registry.get(subtype).rebuild()  # el writer ya se sembro desde disco al construirse
    return {"feed_id": feed_id, "written": result.written, "skipped_capacity": result.skipped_capacity}
