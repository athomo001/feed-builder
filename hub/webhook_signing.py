"""Firma HMAC-SHA256 de webhooks: el emisor firma el cuerpo crudo y viaja
con id, timestamp y firma en headers propios (convencion alineada a
Standard Webhooks); el receptor rechaza timestamps fuera de una ventana de
tolerancia para cortar reintentos/replays.

Extraido a un helper unico para que el webhook de alertas y cualquier
futuro webhook de entrega usen la misma firma, en vez de reimplementarla
cada vez (una reimplementacion divergente es tipicamente donde se cuelan
bugs de seguridad como comparar firmas con `==` en vez de en tiempo
constante).

Autor: Athan Espinoza
"""
import hashlib
import hmac
import time
import uuid
from typing import Optional

# Ventana de tolerancia para el timestamp del webhook: suficiente para
# cubrir desfases de reloj y latencia de red razonables entre el emisor y
# el receptor, sin dejar una ventana tan amplia que vuelva practico un
# replay de una firma capturada.
TOLERANCE_SECONDS = 300


def sign(payload: bytes, *, secret: str, message_id: Optional[str] = None, timestamp: Optional[int] = None) -> dict:
    message_id = message_id or f"msg_{uuid.uuid4()}"
    timestamp = timestamp if timestamp is not None else int(time.time())
    # id y timestamp se incluyen DENTRO del contenido firmado (no solo en
    # headers separados): asi un atacante no puede tomar una firma valida y
    # reenviarla con un id/timestamp distinto, porque la firma dejaria de
    # coincidir.
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
        # Headers faltantes o timestamp no numerico: se trata igual que una
        # firma invalida (False), nunca se propaga la excepcion -- el
        # receptor no deberia poder distinguir "header mal formado" de
        # "firma incorrecta" por el tipo de error.
        return False
    if abs(now - timestamp) > TOLERANCE_SECONDS:
        return False
    signed_content = f"{message_id}.{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
    provided = signature_header.split(",", 1)[-1]
    # compare_digest (tiempo constante) en vez de `==`: comparar strings con
    # `==` corta apenas encuentra el primer byte distinto, lo que filtra por
    # timing cuanto de la firma coincidio -- suficiente en teoria para que
    # un atacante reconstruya la firma byte a byte.
    return hmac.compare_digest(expected, provided)
