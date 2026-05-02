# gateway/base.py

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class GatewayResponse:
    ok: bool
    data: Any = None
    error: str | None = None


def success(data: Any) -> dict:
    return {"ok": True, "data": data}


def failure(error: str) -> dict:
    return {"ok": False, "error": error}
