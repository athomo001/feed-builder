"""Canales de entrega de alertas (spec/09-ROADMAP-ACCEPTANCE.md Entrega 4
"Alertas email/webhook"). Email: spec/05-FORMATS-DESTINATIONS.md "Email:
solo para resumenes, aprobacion, alertas y fallos... nunca como canal
primario... ni con secretos en el mensaje" -- el cuerpo del correo no lleva
`credential_ref` resuelto ni ningun secreto, solo los campos propios de la
alerta. Webhook: firmado con `hub/webhook_signing.py` (mismo HMAC-SHA256
"Standard Webhooks" que spec/08 exige para webhooks de entrega).
"""
import json
import smtplib
from email.message import EmailMessage
from typing import Optional

import requests

from hub.alerting_store import Alert
from hub.credentials import resolve_credential_ref
from hub.webhook_signing import sign


class EmailAlertChannel:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        recipients: list[str],
        credential_ref: Optional[str] = None,
        smtp_client=None,
    ):
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = recipients
        self.credential_ref = credential_ref
        self._smtp_client = smtp_client or smtplib.SMTP

    def send(self, alert: Alert) -> bool:
        if not self.host or not self.recipients:
            return False
        message = EmailMessage()
        message["Subject"] = f"[{alert.severity.upper()}] {alert.condition} ({alert.resource_id})"
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(
            f"Componente: {alert.component}\n"
            f"Recurso: {alert.resource_id}\n"
            f"Condicion: {alert.condition}\n"
            f"Valor observado: {alert.observed_value}\n"
            f"Estado: {alert.state}\n"
            f"Primera vez: {alert.first_seen_at}\n"
            f"Ultima vez: {alert.last_seen_at}\n"
        )
        with self._smtp_client(self.host, self.port) as smtp:
            if self.credential_ref:
                secret = resolve_credential_ref(self.credential_ref)
                username, _, password = secret.partition(":")
                smtp.login(username, password)
            smtp.send_message(message)
        return True


class WebhookAlertChannel:
    def __init__(self, *, url: str, credential_ref: Optional[str] = None, session=None):
        self.url = url
        self.credential_ref = credential_ref
        self._session = session or requests

    def send(self, alert: Alert) -> bool:
        if not self.url:
            return False
        payload = json.dumps(
            {
                "alert_id": alert.alert_id,
                "condition": alert.condition,
                "severity": alert.severity,
                "state": alert.state,
                "component": alert.component,
                "resource_id": alert.resource_id,
                "observed_value": alert.observed_value,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.credential_ref:
            secret = resolve_credential_ref(self.credential_ref)
            headers.update(sign(payload, secret=secret))
        resp = self._session.post(self.url, data=payload, headers=headers, timeout=10)
        return resp.status_code < 400
