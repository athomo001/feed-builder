"""Adaptador HTTP push: destino `api_push` generico para cualquier receptor
que acepte JSON via POST con autenticacion Bearer.

Un solo intento por llamada a `send`; el manejo de reintentos/circuit
breaker/dead-letter vive en `hub/delivery_runner.py`, no aqui (este modulo
solo sabe hablar HTTP, no de politicas de reintento).

Autor: Athan Espinoza
"""
import requests

from hub.adapters.base import AdapterSendResult
from hub.credentials import resolve_credential_ref
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent


class HttpPushAdapter:
    def __init__(self, destination: Destination, *, session=None, secrets_conn=None, cipher=None):
        self.destination = destination
        self._session = session or requests
        self._secrets_conn = secrets_conn
        self._cipher = cipher

    def validate(self) -> list[str]:
        errors = []
        endpoint = self.destination.endpoint or ""
        if not endpoint:
            errors.append("adapter 'http_push' requiere 'endpoint'")
        elif not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            errors.append("endpoint debe ser http(s)")
        if not self.destination.credential_ref:
            errors.append("adapter 'http_push' requiere 'credential_ref'")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return {
            "event_id": event.event_id,
            "family": event.family.value,
            "subtype": event.subtype,
            "value": event.normalized_value,
            "score": event.score,
            "confidence": event.confidence,
            "markings": event.markings,
            "labels": event.labels,
            "valid_until": event.valid_until.isoformat() if event.valid_until else None,
        }

    def send(self, rendered: dict, *, idempotency_key: str) -> AdapterSendResult:
        # El token se resuelve en cada llamada (nunca se cachea en self):
        # asi una rotacion de credencial se recoge en el siguiente envio sin
        # tener que reiniciar el adapter, y el secreto nunca queda retenido
        # en memoria mas tiempo del necesario para esta llamada.
        token = resolve_credential_ref(self.destination.credential_ref, secrets_conn=self._secrets_conn, cipher=self._cipher)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "X-Event-ID": rendered.get("event_id", ""),
        }
        try:
            resp = self._session.post(
                self.destination.endpoint,
                json=rendered,
                headers=headers,
                timeout=self.destination.timeout_seconds,
            )
        except requests.RequestException as e:
            # Errores de red/timeout se tratan igual que un HTTP 4xx/5xx:
            # ambos son "no se entrego", y quien llama (delivery_runner) es
            # quien decide si reintentar, no este adapter.
            return AdapterSendResult(success=False, detail=str(e))

        if resp.status_code >= 400:
            return AdapterSendResult(success=False, detail=f"HTTP {resp.status_code}", status_code=resp.status_code)
        return AdapterSendResult(success=True, status_code=resp.status_code)

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        # No todo destino HTTP push sabe borrar (algunos solo aceptan altas):
        # si el destino no lo soporta, se reporta exito sin llamar a la red
        # en vez de fallar por un borrado que el receptor no entenderia.
        if not self.destination.supports_delete:
            return AdapterSendResult(success=True, detail=f"delete_strategy={self.destination.delete_strategy}")
        return self.send({**self.render(event), "deleted": True}, idempotency_key=f"delete:{event.event_id}")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        return bool(self.destination.endpoint)

    def close(self) -> None:
        return None
