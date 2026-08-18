"""Reconstruccion de los feeds materializados de un destino: cantidad, peso
y vencimiento por TTL, todos aplicados en el mismo `FeedWriter.rebuild()`.

Modulo neutral (no depende de FastAPI/APIState) para que lo puedan llamar
tanto el Admin API (`hub/api/routers/feeds.py`, al publicar/revertir una
politica) como el proceso de ingestion (`hub/service.py`, en un loop
periodico de mantenimiento) sin acoplar un proceso al otro -- son servicios
separados (ver docstring de hub/service.py).

Autor: Athan Espinoza
"""
import os
from typing import Optional

from hub.adapters.factory import build_adapter
from hub.destinations_store import Destination
from hub.policy_store import get_active_version_for_destination

_BUNDLE_ADAPTER = "stix_bundle_feed"
_BUNDLE_SUBTYPE = "bundle"

# Extension de archivo por adapter de subtipo: un FeedWriterRegistry por
# destino, un archivo por subtipo. stix_bundle_feed no esta aca porque no es
# "un archivo por subtipo" -- se maneja aparte en todos lados.
SUBTYPE_ADAPTER_EXTENSIONS = {
    "txt_feed": "txt",
    "csv_feed": "csv",
    "mikrotik_rsc": "rsc",
    "wazuh_cdb": "cdb",
}
FILE_ADAPTER_TYPES = frozenset(SUBTYPE_ADAPTER_EXTENSIONS) | {_BUNDLE_ADAPTER}


def rebuild_all_feeds_for_destination(
    destination: Destination,
    *,
    txt_feed_dir: str,
    policies_conn,
    taxii_conn=None,
    secrets_conn=None,
    cipher=None,
) -> None:
    # Sin esto, un cambio de politica (TTL, cantidad, tipos permitidos)
    # quedaba guardado pero invisible en los feeds ya materializados hasta
    # que llegara un evento nuevo o alguien reconstruyera a mano -- bug real
    # reportado por el operador (2026-08-18). Llamado tanto al
    # publicar/revertir una politica (efecto inmediato) como en un loop
    # periodico (para que el TTL venza SOLO, con el tiempo, sin depender de
    # que llegue un evento nuevo para ese IOC puntual).
    if destination.adapter not in FILE_ADAPTER_TYPES:
        return

    policy = get_active_version_for_destination(policies_conn, destination.destination_id)
    adapter = build_adapter(
        destination, txt_feed_dir=txt_feed_dir, taxii_conn=taxii_conn, secrets_conn=secrets_conn, cipher=cipher, policy=policy
    )

    if destination.adapter == _BUNDLE_ADAPTER:
        adapter.writer.rebuild()
        return

    feed_dir = os.path.join(txt_feed_dir, destination.destination_id)
    ext = SUBTYPE_ADAPTER_EXTENSIONS[destination.adapter]
    suffix = f".{ext}"
    rebuilt = set()
    if os.path.isdir(feed_dir):
        for name in os.listdir(feed_dir):
            if name.endswith(suffix):
                subtype = name[: -len(suffix)]
                adapter.registry.get(subtype).rebuild()
                rebuilt.add(subtype)

    # Un subtipo recien agregado a la politica (ej. CVE) no tenia archivo
    # propio todavia -- sin esto, "Feeds materializados" lo mostraba como
    # link antes de que llegara el primer evento real, y ese link daba 404
    # hasta entonces (reportado por el operador, 2026-08-18). Se materializa
    # vacio de una vez, igual que un feed con datos: el link nunca 404ea.
    if policy is not None:
        for allowed in policy.allowed_iocs:
            for subtype in allowed.subtypes:
                if subtype not in rebuilt:
                    adapter.registry.get(subtype).rebuild()
