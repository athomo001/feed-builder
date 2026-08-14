"""Errores como `application/problem+json` (RFC 9457), con
`X-Correlation-Id` en el body de error para poder correlacionar logs entre
cliente y servidor. Reutiliza `hub/errors.ProblemDetail`, que ya define el
contrato del problem+json; este modulo es el que lo conecta a FastAPI.

Autor: Athan Espinoza
"""
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub.errors import ProblemDetail


class APIError(Exception):
    """Excepcion generica para que cualquier router pueda levantar un error
    HTTP con forma de problem+json sin construir el JSONResponse a mano;
    `api_error_handler` la traduce al formato final."""

    def __init__(self, status_code: int, title: str, detail: str, *, error_code: str = None):
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.error_code = error_code


def correlation_id(request: Request) -> str:
    # Prioridad: si algun middleware ya calculo uno para el request (state),
    # usarlo; si no, respetar el que mando el cliente; si tampoco vino,
    # generar uno nuevo para que la respuesta de error siempre tenga uno.
    return getattr(request.state, "correlation_id", None) or request.headers.get("X-Correlation-Id") or str(uuid.uuid4())


def _problem_response(request: Request, *, status_code: int, title: str, detail: str, error_code: str = None) -> JSONResponse:
    # Punto unico donde se arma el JSONResponse problem+json: asi los tres
    # handlers de abajo (APIError, HTTPException generica, validacion) quedan
    # como wrappers finos y el formato de error nunca diverge entre ellos.
    cid = correlation_id(request)
    problem = ProblemDetail(
        type=f"https://hub.local/problems/{error_code or 'error'}",
        title=title,
        status=status_code,
        detail=detail,
        instance=str(request.url.path),
        error_code=error_code,
        correlation_id=cid,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": cid},
    )


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    # Traduce cualquier APIError levantado explicitamente por un router.
    return _problem_response(request, status_code=exc.status_code, title=exc.title, detail=exc.detail, error_code=exc.error_code)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Cubre las HTTPException que no pasan por APIError (por ejemplo las que
    # levanta el propio Starlette/FastAPI, como 404 de ruta no encontrada),
    # para que ninguna respuesta de error se escape del formato problem+json.
    return _problem_response(request, status_code=exc.status_code, title=str(exc.detail) or "HTTP error", detail=str(exc.detail))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Un campo no declarado en el schema tambien termina aca: los modelos de
    # request usan extra="forbid", asi que pydantic lo reporta como un error
    # de validacion mas (422) en vez de asignarlo silenciosamente.
    return _problem_response(
        request, status_code=422, title="Validation error", detail=str(exc.errors()), error_code="validation_error"
    )
