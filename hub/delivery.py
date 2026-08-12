"""Estados de entrega (spec/03-ARCHITECTURE.md "Delivery ledger", Entrega 0).

Una entrega es la unidad event_id + destination_id + policy_version.
"""
from enum import Enum


class DeliveryState(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"
    SKIPPED = "skipped"
    REVOKED = "revoked"
    EXPIRED = "expired"
