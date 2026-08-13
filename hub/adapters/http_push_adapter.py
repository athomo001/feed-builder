"""Adaptador HTTP push (spec/09-ROADMAP-ACCEPTANCE.md Entrega 2: "Adaptador
HTTP push"; primer destino `api_push` real elegido: QRadar-shaped -- JSON +
Bearer token -- pero generico a cualquier destino que reciba JSON via POST
con autenticacion Bearer, spec/05 "HTTP push/webhook").

Un solo intento por llamada a `send`; el manejo de reintentos/circuit
breaker/dead-letter vive en `hub/delivery_runner.py`, no aqui (este modulo
solo sabe hablar HTTP, no de politicas de reintento).
"""
import requests

from hub.adapters.base import AdapterSendResult
from hub.credentials import resolve_credential_ref
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent


class HttpPushAdapter:
    def __init__(self, destination: Destination, *, session=None):
        self.destination = destination
        self._session = session or requests

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
        token = resolve_credential_ref(self.destination.credential_ref)
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
            return AdapterSendResult(success=False, detail=str(e))

        if resp.status_code >= 400:
            return AdapterSendResult(success=False, detail=f"HTTP {resp.status_code}", status_code=resp.status_code)
        return AdapterSendResult(success=True, status_code=resp.status_code)

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        if not self.destination.supports_delete:
            return AdapterSendResult(success=True, detail=f"delete_strategy={self.destination.delete_strategy}")
        return self.send({**self.render(event), "deleted": True}, idempotency_key=f"delete:{event.event_id}")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        return bool(self.destination.endpoint)

    def close(self) -> None:
        return None
