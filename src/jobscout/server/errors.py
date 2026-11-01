"""One error shape for every non-2xx JSON response: {"error": str, "fields": {dotted_path: message} | null}."""

import logging
import types
import typing
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from jobscout.errors import ConfigError, RunInProgress
from jobscout.server.security import NO_FRAMING

log = logging.getLogger("jobscout.server")

RUN_IN_PROGRESS = "Another run is in progress. Wait for it to finish, or try again in a few minutes."
SERVER_ERROR = "Something went wrong. Check the terminal window for details."
INVALID = "Some values are not valid. Fix the highlighted fields and try again."


def error_response(status: int, message: str, fields: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": message, "fields": fields}, status_code=status)


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    """The pydantic models an annotation can hold, looking through Optional, unions and lists."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    if typing.get_origin(annotation) in (typing.Union, types.UnionType, list, typing.Annotated):
        return [m for arg in typing.get_args(annotation) for m in _models_in(arg)]
    return []


def _bounds(model: Any, loc: list[str]) -> tuple[float | None, float | None]:
    """The (ge, le) declared with `Field(...)` for the field at `loc`, found by walking the request model."""
    models = _models_in(model)
    field = None
    for part in loc:
        if part.isdigit():  # a list index
            continue
        field = next((m.model_fields[part] for m in models if part in m.model_fields), None)
        if field is None:
            return None, None
        models = _models_in(field.annotation)
    if field is None:
        return None, None
    ge = next((c.ge for c in field.metadata if hasattr(c, "ge")), None)
    le = next((c.le for c in field.metadata if hasattr(c, "le")), None)
    return ge, le


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _plain_message(err: Any, model: Any, loc: list[str]) -> str:
    kind = err["type"]
    if kind in ("less_than_equal", "greater_than_equal"):
        ge, le = _bounds(model, loc)
        ctx = err.get("ctx", {})
        ge = ctx["ge"] if ge is None and "ge" in ctx else ge
        le = ctx["le"] if le is None and "le" in ctx else le
        if ge is not None and le is not None:
            return f"Use a number from {_number(ge)} to {_number(le)}."
        if le is not None:
            return f"Use {_number(le)} or less."
        if ge is not None:
            return f"Use {_number(ge)} or more."
    if kind in ("int_parsing", "float_parsing", "int_from_float", "int_type", "float_type"):
        return "Enter a number."
    if kind == "missing":
        return "This is required."
    message = str(err["msg"]).strip()
    message = message[:1].upper() + message[1:]
    return message if message.endswith((".", "!", "?")) else message + "."


def _field_errors(exc: RequestValidationError, model: Any = None) -> dict[str, str]:
    fields: dict[str, str] = {}
    for err in exc.errors():
        loc = [str(part) for part in err["loc"] if part != "body"]
        fields.setdefault(".".join(loc) or "body", _plain_message(err, model, loc))
    return fields


def _body_model(req: Request) -> Any:
    route = req.scope.get("route")
    body_field = getattr(route, "body_field", None)
    return getattr(body_field, "field_info", None) and body_field.field_info.annotation


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConfigError)
    async def _config(_req: Request, exc: ConfigError) -> JSONResponse:
        return error_response(400, str(exc))

    @app.exception_handler(RunInProgress)
    async def _busy(_req: Request, _exc: RunInProgress) -> JSONResponse:
        return error_response(409, RUN_IN_PROGRESS)

    @app.exception_handler(RequestValidationError)
    async def _invalid(req: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, INVALID, _field_errors(exc, _body_model(req)))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unknown(_req: Request, exc: Exception) -> JSONResponse:
        log.error("Unhandled error", exc_info=exc)
        response = error_response(500, SERVER_ERROR)
        response.headers.update(NO_FRAMING)  # the server-error layer sits outside the security middleware
        return response
