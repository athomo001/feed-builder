"""Errores como `application/problem+json` (spec/08-API-SECURITY.md
"Formato de errores": RFC 9457, `X-Correlation-Id` en el body de error).
Reutiliza `hub/errors.ProblemDetail` (contrato ya definido en Entrega 0);
este modulo es el que lo conecta a FastAPI.
"""
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub.errors import ProblemDetail


class APIError(Exception):
    def __init__(self, status_code: int, title: str, detail: str, *, error_code: str = None):
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.error_code = error_code


def correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", None) or request.headers.get("X-Correlation-Id") or str(uuid.uuid4())


def _problem_response(request: Request, *, status_code: int, title: str, detail: str, error_code: str = None) -> JSONResponse:
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
    return _problem_response(request, status_code=exc.status_code, title=exc.title, detail=exc.detail, error_code=exc.error_code)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _problem_response(request, status_code=exc.status_code, title=str(exc.detail) or "HTTP error", detail=str(exc.detail))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # spec/08 API3 (mass assignment): un campo no declarado en el schema
    # tambien cae aca (pydantic con extra="forbid" lo reporta como error 422).
    return _problem_response(
        request, status_code=422, title="Validation error", detail=str(exc.errors()), error_code="validation_error"
    )
