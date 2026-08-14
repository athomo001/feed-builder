"""Formato de error RFC 9457.

`detail` nunca debe contener secretos, stack traces ni payload completo;
eso es responsabilidad de quien construye el ProblemDetail, no del modelo
(el modelo solo define la forma del contrato).

Autor: Athan Espinoza
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetail(BaseModel):
    # RFC 9457 / application/problem+json describe respuestas de ERROR:
    # el rango valido es el de codigos de error HTTP (4xx/5xx), no todo 100-599.
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "https://hub.example/problems/policy-rejected",
                "title": "Policy rejected the event",
                "status": 422,
                "detail": "score 10 is below MIN_SCORE 50",
                "instance": "/admin/api/v1/events/evt-1",
                "error_code": "score_below_minimum",
            }
        }
    )

    type: str
    title: str
    status: int = Field(ge=400, le=599)
    detail: str
    instance: str
    error_code: Optional[str] = None
    event_id: Optional[str] = None
    delivery_id: Optional[str] = None
    correlation_id: Optional[str] = None
