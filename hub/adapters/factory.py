"""Construccion de un adapter a partir de un `Destination`: punto unico de
dispatch por `destination.adapter`, en vez de repetir el mismo if/elif en
`hub/service.py`, `hub/api/routers/destinations.py` y `hub/api/routers/
deliveries.py` (uno de esos tres, `deliveries.py`, llego a asumir
incorrectamente `HttpPushAdapter` para cualquier adapter que no fuera
`txt_feed`, precisamente por no tener un punto unico como este).

Agregar un adapter nuevo (`csv_feed`, `mikrotik_rsc`, `wazuh_cdb`,
`qradar_reference_set`, `stix_bundle_feed`, `taxii2`) solo requiere un branch
aca, no tocar los 3 call sites.

Autor: Athan Espinoza
"""
from hub.adapters.csv_feed_adapter import CsvFeedAdapter
from hub.adapters.http_push_adapter import HttpPushAdapter
from hub.adapters.mikrotik_adapter import MikrotikAdapter
from hub.adapters.qradar_adapter import QRadarAdapter
from hub.adapters.stix_bundle_adapter import StixBundleAdapter
from hub.adapters.taxii_adapter import Taxii2Adapter
from hub.adapters.txt_feed_adapter import TxtFeedAdapter
from hub.adapters.wazuh_adapter import WazuhCdbAdapter
from hub.destinations_store import Destination

# Adapters que hacen una llamada de red saliente real y por lo tanto se
# benefician de circuit breaker. Los `file_feed`/`taxii2` locales
# (txt/csv/rsc/cdb/stix bundle/taxii store) solo escriben a disco: sin
# llamada de red no hay nada que un circuit breaker proteja.
NETWORK_ADAPTER_TYPES = frozenset({"http_push", "qradar_reference_set"})


def build_adapter(destination: Destination, *, txt_feed_dir: str, taxii_conn=None, secrets_conn=None, cipher=None):
    # Dispatch centralizado (ver rationale en el docstring del modulo):
    # cada branch mapea 1:1 con un valor de destination.adapter.
    adapter = destination.adapter
    if adapter == "txt_feed":
        return TxtFeedAdapter(destination, base_dir=txt_feed_dir)
    if adapter == "csv_feed":
        return CsvFeedAdapter(destination, base_dir=txt_feed_dir)
    if adapter == "mikrotik_rsc":
        return MikrotikAdapter(destination, base_dir=txt_feed_dir)
    if adapter == "wazuh_cdb":
        return WazuhCdbAdapter(destination, base_dir=txt_feed_dir)
    if adapter == "stix_bundle_feed":
        return StixBundleAdapter(destination, base_dir=txt_feed_dir)
    if adapter == "qradar_reference_set":
        return QRadarAdapter(destination, secrets_conn=secrets_conn, cipher=cipher)
    if adapter == "taxii2":
        return Taxii2Adapter(destination, conn=taxii_conn)
    # Fallback intencional: cualquier adapter no listado arriba se trata
    # como http_push (el caso mas comun de "api_push generico"). Al vivir
    # aca, centralizado, un adapter type sin branch explicito ya no puede
    # terminar mal enrutado en un call site distinto (ver el bug descrito en
    # el docstring del modulo).
    return HttpPushAdapter(destination, secrets_conn=secrets_conn, cipher=cipher)


def uses_circuit_breaker(destination: Destination) -> bool:
    return destination.adapter in NETWORK_ADAPTER_TYPES
