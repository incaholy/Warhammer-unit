"""Typed service exceptions.

Each subclasses the builtin the API layer already maps (`LookupError` /
`ValueError`), so the existing `app/main.py` handlers keep mapping it correctly
even before the dedicated handler runs — the migration is incremental. See
SPEC.md "Custom service errors".

Naming: generic for cross-cutting failures (`NotFoundError`, `ConflictError`);
one resource-named `*ValidationError` per service, carrying the offending
`field`. Split a rule into its own class only when its handling diverges.
"""


class ServiceError(Exception):
    """Base for service-raised errors: an HTTP `status_code` + optional `field`."""

    status_code = 400

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(ServiceError, LookupError):
    """A requested row does not exist. → 404."""

    status_code = 404


class ConflictError(ServiceError, ValueError):
    """A uniqueness clash (duplicate). → 409."""

    status_code = 409


class ValidationError(ServiceError, ValueError):
    """Base for per-service validation errors. → 400, carries `field`.

    Constructed as `(field, message)` and rendered as ``"field: message"``.
    """

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}", field=field)


class UserValidationError(ValidationError):
    """Bad user/account input."""


class UnitValidationError(ValidationError):
    """Bad catalog input (units, factions, subfactions, weapons)."""


class ArmyValidationError(ValidationError):
    """Bad army/roster input."""


class InventoryValidationError(ValidationError):
    """Bad inventory input."""
