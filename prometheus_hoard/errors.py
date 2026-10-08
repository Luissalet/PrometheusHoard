"""One error type for every expected failure, so the API, the agent tools and the UI report it the same way."""

from __future__ import annotations

from typing import Any

from .hoard_link.agentkit import AppError


class SparkError(AppError):
    """An expected, explainable failure with a stable ``code``, a message and an actionable ``hint``."""

    STATUS = {
        "not_found": 404,
        "confirm_required": 400,
        "invalid": 400,
        "forbidden": 403,
        "busy": 409,
        "conflict": 409,
        "unreachable": 503,
        "unavailable": 503,
        "too_large": 413,
        "remote_failed": 502,
        "timeout": 504,
    }

    def __init__(self, code: str, message: str, hint: str = "", *, status: int | None = None, **details: Any):
        super().__init__(code, message, hint=hint, status=status or self.STATUS.get(code, 400), details=details)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {**self.details, "error": self.message, "code": self.code}
        if self.hint:
            body["hint"] = self.hint
        return body
