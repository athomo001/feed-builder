"""Firma HMAC-SHA256 de webhooks (spec/08-API-SECURITY.md "Adaptadores
webhook firman el cuerpo crudo con HMAC-SHA256 y viajan con id, timestamp y
firma en headers propios (convencion alineada a Standard Webhooks); el
receptor rechaza timestamps fuera de una ventana de tolerancia de 300s para
cortar reintentos/replays").

Extraido a un helper unico para que el webhook de alertas (spec/09 Entrega 4
"Alertas email/webhook") y cualquier futuro webhook de entrega usen la
misma firma, en vez de reimplementarla cada vez.
"""
import hashlib
import hmac
import time
import uuid
from typing import Optional

TOLERANCE_SECONDS = 300


def sign(payload: bytes, *, secret: str, message_id: Optional[str] = None, timestamp: Optional[int] = None) -> dict:
    message_id = message_id or f"msg_{uuid.uuid4()}"
    timestamp = timestamp if timestamp is not None else int(time.time())
    signed_content = f"{message_id}.{timestamp}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
    return {
        "webhook-id": message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": f"v1,{signature}",
    }


def verify(payload: bytes, headers: dict, *, secret: str, now: Optional[int] = None) -> bool:
    now = now if now is not None else int(time.time())
    try:
        message_id = headers["webhook-id"]
        timestamp = int(headers["webhook-timestamp"])
        signature_header = headers["webhook-signature"]
    except (KeyError, ValueError):
        return False
    if abs(now - timestamp) > TOLERANCE_SECONDS:
        return False
    signed_content = f"{message_id}.{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
    provided = signature_header.split(",", 1)[-1]
    return hmac.compare_digest(expected, provided)
