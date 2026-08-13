"""Contrato de adapter de destino (spec/05-FORMATS-DESTINATIONS.md "Regla
general": "El adaptador debe implementar `validate`, `render`, `send`,
`acknowledge`, `healthcheck` y `close`").

`AdapterSendResult` es el shape comun de retorno de `send`. Mas alla de los
6 metodos de la spec, ambos adapters concretos exponen ademas `discard`
(quitar un valor previamente entregado cuando pasa a revoked/expirado) --
no esta en la lista original de 05, pero spec/02 "Borrados y
actualizaciones" exige que el destino reaccione a eso de alguna forma, y no
hay un metodo declarado para ello en el contrato base.
"""
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from hub.models import CanonicalIOCEvent


@dataclass
class AdapterSendResult:
    success: bool
    detail: str = ""
    status_code: Optional[int] = None


class DestinationAdapter(Protocol):
    def validate(self) -> list[str]: ...

    def render(self, event: CanonicalIOCEvent) -> Any: ...

    def send(self, rendered: Any, *, idempotency_key: str) -> AdapterSendResult: ...

    def acknowledge(self, result: AdapterSendResult) -> None: ...

    def healthcheck(self) -> bool: ...

    def close(self) -> None: ...
