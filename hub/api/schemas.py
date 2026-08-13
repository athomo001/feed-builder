"""Modelos de request/response del Admin API (spec/08-API-SECURITY.md
"Property-level / mass assignment (API3)": "los payloads de escritura se
validan contra una allow-list explicita de campos por endpoint; campos no
declarados o de solo lectura... se rechazan, nunca se asignan por
deserializacion directa del body").

Todo modelo de REQUEST usa `extra="forbid"`: un campo no declarado aca
(por ejemplo `destination_id`, `created_at` en un update) hace que FastAPI
devuelva 422 en vez de asignarlo silenciosamente.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from hub.destinations_store import AdapterType, RetryPolicy
from hub.policy_store import AllowedIOC


class _Forbid(BaseModel):
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


class DiscardRequest(_Forbid):
    reason: str


class DestinationTestRequest(_Forbid):
    allow_private_network: bool = False
