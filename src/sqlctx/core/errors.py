"""Sanitized application errors with stable public codes."""

from __future__ import annotations

from typing import Any


class SqlCtxError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}

    def public_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            **self.details,
        }

    def __str__(self) -> str:
        """Keep the stable code when an interface serializes only exception text."""
        return f"{self.code}: {self.message}"


class ApprovalRequired(SqlCtxError):
    def __init__(self, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "APPROVAL_REQUIRED",
            "This operation requires an interactive owner approval.",
            retryable=True,
            status_code=403,
            details=details,
        )


class ToolingUnavailable(SqlCtxError):
    def __init__(self, message: str, *, code: str = "TOOLING_UNAVAILABLE") -> None:
        super().__init__(code, message, retryable=False, status_code=503)
