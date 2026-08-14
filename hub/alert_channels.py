"""Canales de entrega de alertas: email y webhook. El correo se usa solo
para notificaciones (nunca como canal primario de datos sensibles) -- el
cuerpo del mensaje no lleva `credential_ref` resuelto ni ningun secreto,
solo los campos propios de la alerta. El webhook va firmado con
`hub/webhook_signing.py` (HMAC-SHA256) para que el receptor pueda verificar
que el payload no fue alterado ni forjado en transito.

Autor: Athan Espinoza
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
    # `smtp_client`/`secrets_conn`/`cipher` son inyectables para poder probar
    # el envio y la resolucion de credenciales sin abrir una conexion SMTP ni
    # una base de secretos real.
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        recipients: list[str],
        credential_ref: Optional[str] = None,
        smtp_client=None,
        secrets_conn=None,
        cipher=None,
    ):
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = recipients
        self.credential_ref = credential_ref
        self._smtp_client = smtp_client or smtplib.SMTP
        self._secrets_conn = secrets_conn
        self._cipher = cipher

    def send(self, alert: Alert) -> bool:
        # El canal es opcional: si no esta configurado (sin host o sin
        # destinatarios) simplemente no envia nada en vez de fallar.
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
            # La credencial se resuelve recien aca, dentro del bloque de
            # envio, para que el secreto nunca llegue a formar parte del
            # cuerpo del mensaje construido arriba.
            if self.credential_ref:
                secret = resolve_credential_ref(self.credential_ref, secrets_conn=self._secrets_conn, cipher=self._cipher)
                username, _, password = secret.partition(":")
                smtp.login(username, password)
            smtp.send_message(message)
        return True


class WebhookAlertChannel:
    def __init__(self, *, url: str, credential_ref: Optional[str] = None, session=None, secrets_conn=None, cipher=None):
        self.url = url
        self.credential_ref = credential_ref
        self._session = session or requests
        self._secrets_conn = secrets_conn
        self._cipher = cipher

    def send(self, alert: Alert) -> bool:
        # Igual que el canal de email: sin URL configurada, el canal esta
        # deshabilitado y no debe intentar la llamada HTTP.
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
            # Firma opcional: solo se agrega si se configuro una credencial
            # de firma para este webhook, para no romper receptores que
            # todavia no validan la firma.
            secret = resolve_credential_ref(self.credential_ref, secrets_conn=self._secrets_conn, cipher=self._cipher)
            headers.update(sign(payload, secret=secret))
        resp = self._session.post(self.url, data=payload, headers=headers, timeout=10)
        return resp.status_code < 400
