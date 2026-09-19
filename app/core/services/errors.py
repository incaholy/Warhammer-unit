"""Typed service exceptions.

Cross-cutting failures raised by services: `NotFoundError` (a missing row) and
`ConflictError` (a duplicate). Each **inherits the builtin it maps to** —
`LookupError` / `ValueError` — so service-level tests can
`pytest.raises(LookupError / ValueError)`, and each carries its own wire `code`
(`ErrorCode`, from `app.core.errors`), a `message`, and an optional `field`.

Each also inherits `CodedError`, a marker base that carries no behaviour and
exists only so the API layer can register one handler for the whole family
instead of a per-class list that a new error can fall off.

Validation errors are per-service: each service defines its own
`*ValidationError(ValueError)` in its own module (e.g. `UnitValidationError` in
`service_unit.py`), carrying `code = ErrorCode.VALIDATION` and the offending
`field`.

The API layer (`app/main.py` handlers + `app/api/errors.py`) maps `code` -> HTTP
status, registered once against `CodedError` — never a blanket
`ValueError`/`LookupError` handler, which would swallow library exceptions.
"""

from app.core.errors import CodedError, ErrorCode


class NotFoundError(CodedError, LookupError):
    """A requested row does not exist."""

    code = ErrorCode.NOT_FOUND

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class ConflictError(CodedError, ValueError):
    """A uniqueness clash (duplicate)."""

    code = ErrorCode.CONFLICT

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field
