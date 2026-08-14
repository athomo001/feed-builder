"""Adaptador QRadar Reference Set: empuja IOC via REST API (`bulk_load`),
JSON sobre `/api/reference_data/sets/{name}/bulk_load`, autenticado con
token de API de QRadar.

`bulk_load` es aditivo (agrega valores al set existente, nunca lo
reemplaza) -- IBM lo documenta como la via eficiente para agregar muchos
valores de una vez; llamarlo con una lista de un solo valor por evento es
seguro (nunca hay riesgo de pisar el set), aunque no es el caso de uso mas
eficiente para el que existe (ese seria un resync/backfill por lotes,
acumulando un lote antes de enviarlo -- no implementado todavia).

No se valido este adapter contra una instancia QRadar real (mismo
disclaimer honesto que ya tiene `HttpPushAdapter` para su propio destino
generico) -- el nombre del header de auth (`SEC`) y la forma del body (JSON
array de valores) siguen la documentacion publica de IBM, no un esquema
introspectado.

Autor: Athan Espinoza
"""
import requests

from hub.adapters.base import AdapterSendResult
from hub.credentials import resolve_credential_ref
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent

# Version de la QRadar REST API contra la que se probo el shape del
# request (header Version); fijada como constante para poder actualizarla
# en un solo lugar si una instalacion necesita otra version de API.
_QRADAR_API_VERSION = "12.0"


class QRadarAdapter:
    def __init__(self, destination: Destination, *, session=None, secrets_conn=None, cipher=None):
        self.destination = destination
        self._session = session or requests
        self._secrets_conn = secrets_conn
        self._cipher = cipher

    def _set_name(self) -> str:
        return (self.destination.format_options or {}).get("reference_set_name", "")

    def validate(self) -> list[str]:
        errors = []
        endpoint = self.destination.endpoint or ""
        if not endpoint:
            errors.append("adapter 'qradar_reference_set' requiere 'endpoint'")
        elif not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            errors.append("endpoint debe ser http(s)")
        if not self.destination.credential_ref:
            errors.append("adapter 'qradar_reference_set' requiere 'credential_ref'")
        if not self._set_name():
            errors.append("adapter 'qradar_reference_set' requiere format_options.reference_set_name")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return {"value": event.normalized_value, "event_id": event.event_id}

    def send(self, rendered: dict, *, idempotency_key: str) -> AdapterSendResult:
        # Token resuelto en cada llamada, nunca cacheado: recoge rotaciones
        # de credencial sin reiniciar el adapter y evita retener el secreto
        # en memoria mas alla de lo necesario para esta llamada.
        token = resolve_credential_ref(self.destination.credential_ref, secrets_conn=self._secrets_conn, cipher=self._cipher)
        url = f"{self.destination.endpoint.rstrip('/')}/api/reference_data/sets/{self._set_name()}/bulk_load"
        headers = {
            "SEC": token,
            "Version": _QRADAR_API_VERSION,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        try:
            resp = self._session.post(
                url, json=[rendered["value"]], headers=headers, timeout=self.destination.timeout_seconds
            )
        except requests.RequestException as e:
            return AdapterSendResult(success=False, detail=str(e))

        if resp.status_code >= 400:
            return AdapterSendResult(success=False, detail=f"HTTP {resp.status_code}", status_code=resp.status_code)
        return AdapterSendResult(success=True, status_code=resp.status_code)

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        # No todo reference set se puede depurar por API en todos los
        # destinos configurados: si el destino no lo soporta, se reporta
        # exito sin llamar a la red en vez de forzar un DELETE no deseado.
        if not self.destination.supports_delete:
            return AdapterSendResult(success=True, detail=f"delete_strategy={self.destination.delete_strategy}")
        token = resolve_credential_ref(self.destination.credential_ref, secrets_conn=self._secrets_conn, cipher=self._cipher)
        url = (
            f"{self.destination.endpoint.rstrip('/')}/api/reference_data/sets/"
            f"{self._set_name()}/{event.normalized_value}"
        )
        headers = {"SEC": token, "Version": _QRADAR_API_VERSION}
        try:
            resp = self._session.delete(url, headers=headers, timeout=self.destination.timeout_seconds)
        except requests.RequestException as e:
            return AdapterSendResult(success=False, detail=str(e))
        if resp.status_code >= 400:
            return AdapterSendResult(success=False, detail=f"HTTP {resp.status_code}", status_code=resp.status_code)
        return AdapterSendResult(success=True, status_code=resp.status_code)

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        # No hace una llamada de red real (evita gastar cuota/latencia en un
        # chequeo periodico): solo confirma que la configuracion minima para
        # poder intentar un send() esta presente.
        return bool(self.destination.endpoint) and bool(self._set_name())

    def close(self) -> None:
        return None
