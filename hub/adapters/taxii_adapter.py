"""Adaptador servidor TAXII 2.1. A diferencia del resto de adapters
`file_feed` (una foto que se reescribe completa en cada rebuild), una
coleccion TAXII es append/update-only por convencion del protocolo -- un
cliente pagina por `added_after` esperando que la coleccion solo crezca,
nunca que un objeto desaparezca en silencio. Por eso `discard()` republica
el mismo indicator con `revoked=true` y `modified` actualizado en vez de
borrarlo: nunca se oculta una actualizacion legitima, se deja que el
cliente vea explicitamente que el indicator fue revocado.

Autor: Athan Espinoza
"""
from typing import Optional

from hub.adapters.base import AdapterSendResult
from hub.destinations_store import Destination
from hub.models import CanonicalIOCEvent
from hub.stix_bundle import render_stix_indicator
from hub.taxii_store import upsert_object


class Taxii2Adapter:
    def __init__(self, destination: Destination, *, conn=None):
        self.destination = destination
        self.conn = conn

    def validate(self) -> list[str]:
        errors = []
        if self.destination.format not in ("stix2.1",):
            errors.append("adapter 'taxii2' solo soporta destination.format == 'stix2.1'")
        if self.conn is None:
            errors.append("adapter 'taxii2' requiere una conexion a taxii_store (taxii_conn)")
        return errors

    def render(self, event: CanonicalIOCEvent) -> dict:
        return render_stix_indicator(event)

    def send(self, rendered: dict, *, idempotency_key: Optional[str] = None) -> AdapterSendResult:
        upsert_object(self.conn, self.destination.destination_id, rendered)
        return AdapterSendResult(success=True, detail=f"stix_id={rendered['id']}")

    def discard(self, event: CanonicalIOCEvent) -> AdapterSendResult:
        # Marca revoked=True en vez de eliminar la fila (ver rationale en el
        # docstring del modulo): un cliente TAXII que ya vio este objeto
        # necesita poder observar la revocacion, no un objeto que
        # simplemente desaparece de la coleccion.
        rendered = render_stix_indicator(event)
        rendered["revoked"] = True
        upsert_object(self.conn, self.destination.destination_id, rendered)
        return AdapterSendResult(success=True, detail="revoked")

    def acknowledge(self, result: AdapterSendResult) -> None:
        return None

    def healthcheck(self) -> bool:
        # Sin conexion a taxii_store no hay donde escribir: el healthcheck
        # de este adapter se reduce a "hay conexion configurada".
        return self.conn is not None

    def close(self) -> None:
        return None
