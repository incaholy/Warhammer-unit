"""HTTP-facing error mapping: `code` -> HTTP status.

The `ErrorCode` vocabulary lives in `app.core.errors` (shared, below both
layers). This module owns only the API-layer concern: which HTTP status each
code maps to. Every error (service *and* auth) carries its own `code`, so this
one direction is all that's needed — and deriving the status from the code here,
rather than storing it on the error, makes it impossible for the two to disagree.
"""

from app.core.errors import ErrorCode

CODE_STATUS: dict[ErrorCode, int] = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.VALIDATION: 400,
    ErrorCode.REQUEST_VALIDATION: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.INTERNAL: 500,
}
