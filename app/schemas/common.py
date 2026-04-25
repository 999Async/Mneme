from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    ok: bool = True
    data: T | None = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    code: int
    data: None = None


def ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def error(code: int, message: str) -> dict:
    return {"ok": False, "error": message, "code": code, "data": None}
