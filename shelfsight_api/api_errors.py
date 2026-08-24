from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def api_error(
    status_code: int,
    error_code: str,
    message: str,
    **details: Any,
) -> HTTPException:
    body: dict[str, Any] = {"code": error_code, "message": message}
    body.update({key: value for key, value in details.items() if value is not None})
    return HTTPException(status_code=status_code, detail=body)
