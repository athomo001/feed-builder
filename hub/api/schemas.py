"""Modelos de request/response del Admin API. Los payloads de escritura se
validan contra una allow-list explicita de campos por endpoint: un campo no
declarado o de solo lectura se rechaza, nunca se asigna por deserializacion
directa del body -- esto es lo que previene mass assignment (un caller
mandando `created_at` o `destination_id` en un update para pisar un campo
que no deberia poder tocar).

Todo modelo de REQUEST usa `extra="forbid"`: un campo no declarado aca
hace que FastAPI devuelva 422 en vez de asignarlo silenciosamente.

Autor: Athan Espinoza
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from hub.destinations_store import AdapterType, RetryPolicy
from hub.policy_store import AllowedIOC


class _Forbid(BaseModel):
    # Base comun para no repetir `model_config = ConfigDict(extra="forbid")`
    # en cada modelo de request; heredar de aca es lo que garantiza el
    # comportamiento anti mass-assignment descripto arriba.
    model_config = ConfigDict(extra="forbid")


class DestinationCreate(_Forbid):
    destination_id: str
    name: str
    adapter: AdapterType
    enabled: bool = True
    endpoint: Optional[str] = None
    credential_ref: Optional[str] = None
    format: str = "txt"
    allowed_ioc_types: list[str] = Field(default_factory=list)
    format_options: dict = Field(default_factory=dict)
    capacity: dict = Field(default_factory=dict)
    supports_delete: bool = False
    delete_strategy: Optional[str] = None
    timeout_seconds: int = 15
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class DestinationUpdate(_Forbid):
    # Todos los campos son Optional (a diferencia de DestinationCreate): el
    # router los aplica con `model_dump(exclude_unset=True)`, asi que un
    # campo en None por default nunca se distingue de "no lo mande" y el
    # update queda parcial, no reemplaza el destino entero.
    name: Optional[str] = None
    enabled: Optional[bool] = None
    endpoint: Optional[str] = None
    credential_ref: Optional[str] = None
    format: Optional[str] = None
    allowed_ioc_types: Optional[list[str]] = None
    format_options: Optional[dict] = None
    capacity: Optional[dict] = None
    supports_delete: Optional[bool] = None
    delete_strategy: Optional[str] = None
    timeout_seconds: Optional[int] = None
    retry: Optional[RetryPolicy] = None


class PolicyCreate(_Forbid):
    policy_id: str
    destination_id: str
    allowed_iocs: list[AllowedIOC]
    ttl_days: dict[str, int] = Field(default_factory=dict)


class SimulateRequest(_Forbid):
    sample: Optional[list[dict]] = None
    sample_size: int = 50


class VersionRequest(_Forbid):
    version: int
    reason: str


class RewindRequest(_Forbid):
    cursor_value: str
    reason: str


class DiscardRequest(_Forbid):
    reason: str


class DestinationTestRequest(_Forbid):
    allow_private_network: bool = False


class SecretCreate(_Forbid):
    name: str
    value: str


class RotateKeyRequest(_Forbid):
    new_key: str
