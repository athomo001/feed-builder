"""Estados de entrega.

Una entrega es la unidad event_id + destination_id + policy_version: cada
combinacion de esos tres campos se rastrea de forma independiente porque un
mismo evento puede tener resultados distintos en cada destino y bajo cada
version de politica.

Autor: Athan Espinoza
"""
from enum import Enum


class DeliveryState(str, Enum):
    # Maquina de estados de una entrega individual: separa los estados
    # terminales (DELIVERED/ACKNOWLEDGED/DEAD_LETTER/SKIPPED/REVOKED/EXPIRED)
    # de los transitorios (PENDING/SENDING/RETRYING) para que el resto del
    # sistema pueda decidir de un vistazo si una entrega todavia necesita trabajo.
    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"
    SKIPPED = "skipped"
    REVOKED = "revoked"
    EXPIRED = "expired"
