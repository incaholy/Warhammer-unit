# API conventions

The cross-repo contract between the backend (`warhammer_unit`) and the frontend
(`warhammer_web`). These are the rules that hold across *every* endpoint, so a
client can be written against the conventions rather than against each route.
The machine-readable schema is [`openapi.json`](../../openapi.json) (generated
from the app by `make openapi`, verified fresh in CI); this file is the human
half — the rules a schema can't express.

## Versioning & base path

- Every resource route is served under **`/api/v1`** (e.g. `GET /api/v1/units`).
- **`GET /health`** is deliberately **unversioned** (served at the root): platform
  probes want a path that survives version bumps.
- A breaking change ships as `/api/v2` alongside `/api/v1`, so existing clients
  keep working. The version lives in one place per repo: the router prefix
  (backend `app/main.py`) and `API_PREFIX` (frontend `src/api/client.ts`).

## Authentication

- **Scheme:** JWT bearer. Send `Authorization: Bearer <token>` on authenticated
  requests. Auth state is a header, never a cookie, so CORS credentials stay off.
- **Get a token:** `POST /api/v1/auth/login` with an OAuth2 password form
  (`username` accepts a username *or* email, plus `password`) → `{access_token,
  token_type}`. `POST /api/v1/auth/register` creates a user.
- **Identity comes from the token, not the path.** User-scoped routes live under
  `/me/*` (e.g. `/me/armies`); a user can only ever address their own data, and a
  stranger's id reveals nothing (missing/Forbidden reads as `404`).
- **Admin** actions (catalog writes) require an admin user; a non-admin gets `403`.
- A missing/invalid token gets `401` with a `WWW-Authenticate: Bearer` header.

## Errors

Every error — from any layer — has **one shape**:

```json
{ "detail": "human-readable message",
  "code": "MACHINE_CODE",
  "field": "optional; the offending field",
  "request_id": "correlation id (also the X-Request-ID header)",
  "errors": [ { "code": "MACHINE_CODE", "field": "email", "detail": "…" } ] }
```

- `detail` is for humans; **branch on `code`, never on `detail` or the raw status.**
- `field` is present on validation errors that concern a specific field.
- `request_id` ties the error to its server log line (see Observability).
- **`errors` is a uniform array on every error body** — one element for most
  failures, and **all of them at once** for a multi-field request validation
  (`422`), so a form learns its three bad inputs in one round-trip, not three. The
  top-level `detail`/`code`/`field` always mirror `errors[0]` (so single-error
  clients need no change). An element's `field` is `null` for a whole-body error.

**`code` is a fixed enum**, and each code maps to exactly one HTTP status (the
backend derives status from code, so they can't disagree):

| `code` | HTTP | Meaning |
|---|---|---|
| `NOT_FOUND` | 404 | No such resource (or not visible to this user). |
| `CONFLICT` | 409 | Clashes with existing state (duplicate, already-a-member). |
| `VALIDATION` | 400 | A business rule failed on a well-formed request. |
| `REQUEST_VALIDATION` | 422 | The request itself is malformed (schema/parse). |
| `UNAUTHORIZED` | 401 | Missing or invalid credentials. |
| `FORBIDDEN` | 403 | Authenticated but not permitted (e.g. non-admin). |
| `INTERNAL` | 500 | Unexpected server fault; body is always sanitized. |

Note the two validation codes: `REQUEST_VALIDATION` (422) is "you sent something
malformed"; `VALIDATION` (400) is "your request was well-formed but broke a rule."
They are distinct on purpose so a client can tell them apart.

## Pagination

**Every list endpoint** returns the same envelope — the total is in the **body**,
never a header (a header is invisible to cross-origin JS unless CORS exposes it):

```json
{ "items": [ ... ],
  "total": 1331,      // count across the whole filter, ignoring paging
  "limit": 50,        // page size that was applied
  "offset": 0 }       // where this page started
```

- Query params: **`limit`** (1–200, default 50) and **`offset`** (≥ 0). Out-of-range
  values are `422`.
- Ordering is stable: each list sorts by a natural key **plus the primary-key `id`
  as a tiebreaker**, so paging never skips or repeats a row across page boundaries.
- Filters (where supported) travel as their own query params (`q` for name search,
  `faction_id`, etc.) and `total` respects them.
- **Server-side aggregates, not client counting.** `GET /api/v1/units/facets`
  returns `{ total, by_faction: { <faction_id>: count } }` for the current filter
  (one SQL `GROUP BY`) — so a client never downloads a collection to count it.

Not paginated (by design): computed reports about a single resource
(`/me/armies/{id}/shortfall`, `.../validate`) and the static `/api/v1/taxonomy`
map — those return complete results.

## Observability

- Every response carries an **`X-Request-ID`** header. Send your own inbound
  `X-Request-ID` and it's echoed; otherwise one is generated.
- The same id appears in every error body (`request_id`) and on every server log
  line, so a user's report, its logs, and its captured exception all join on it.

## Where these come from

Auth · errors ([ROADMAP R2]) · pagination ([R4]) · versioning ([R5]) ·
observability ([R7]). This document is the durable home the ROADMAP items pointed
to; when a convention changes, it changes here in the same PR as the code.

[ROADMAP R2]: ../../ROADMAP.md
[R4]: ../../ROADMAP.md
[R5]: ../../ROADMAP.md
[R7]: ../../ROADMAP.md
