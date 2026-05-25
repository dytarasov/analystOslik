from typing import Any


class DomainError(Exception):
    code: str = "INTERNAL"
    status_code: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    status_code = 404


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    status_code = 403


class UnauthorizedError(DomainError):
    code = "UNAUTHORIZED"
    status_code = 401


class ValidationError(DomainError):
    code = "VALIDATION"
    status_code = 422


class ConflictError(DomainError):
    code = "CONFLICT"
    status_code = 409


class UpstreamError(DomainError):
    code = "UPSTREAM"
    status_code = 502


class TimeoutError_(DomainError):
    code = "TIMEOUT"
    status_code = 504


def to_payload(exc: DomainError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "details": exc.details,
    }
