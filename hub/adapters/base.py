"""Contrato de adapter de destino: todo adapter debe implementar `validate`,
`render`, `send`, `acknowledge`, `healthcheck` y `close`.

`AdapterSendResult` es el shape comun de retorno de `send`. Mas alla de esos
6 metodos, los adapters concretos exponen ademas `discard` (quitar un valor
previamente entregado cuando pasa a revoked/expirado): cada tipo de destino
reacciona distinto a un borrado/revocacion (reescribir un archivo, marcar
`revoked=true`, hacer un DELETE HTTP), asi que no se declaro en el contrato
base para no forzar una unica semantica de "borrado" a todos los adapters.

Autor: Athan Espinoza
"""
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from hub.models import CanonicalIOCEvent


@dataclass
class AdapterSendResult:
    success: bool
    detail: str = ""
    status_code: Optional[int] = None


# Protocol (tipado estructural) en vez de ABC: los adapters concretos no
# necesitan heredar explicitamente de esta clase, solo implementar los
# metodos con esta firma -- evita acoplar cada adapter a una jerarquia comun.
class DestinationAdapter(Protocol):
    def validate(self) -> list[str]: ...

    def render(self, event: CanonicalIOCEvent) -> Any: ...

    def send(self, rendered: Any, *, idempotency_key: str) -> AdapterSendResult: ...

    def acknowledge(self, result: AdapterSendResult) -> None: ...

    def healthcheck(self) -> bool: ...

    def close(self) -> None: ...
