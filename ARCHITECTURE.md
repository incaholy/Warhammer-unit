# Architecture: Warhammer Unit

The target architecture for `Warhammer-unit` (FastAPI backend) and `warhammer_unit_web` (React
frontend). This document is **normative**: it states how the system is meant to be built and why,
not how it happens to be built today. [`ROADMAP.md`](ROADMAP.md) carries the delta between the two.

Companion docs: [`CODE-REVIEW.md`](CODE-REVIEW.md) for correctness bugs (note: its four findings have
since been fixed, and its test counts predate the current suite; it is kept for the reasoning),
[`DEPLOY-GCP.md`](DEPLOY-GCP.md) for production topology, `SPEC.md` for domain and feature detail.

## How to read this

Every principle below carries a status:

| Status | Meaning |
|---|---|
| **Holds** | True in the code today, and something enforces it. |
| **Partial** | True in places. The exceptions are listed, with a roadmap item. |
| **Not yet** | A decision that has been made but not implemented. Roadmap item given. |
| **Undecided** | Deliberately open. The trade-off is stated so it can be decided rather than drifted into. |

**Those markers are the most important part of this document.** A normative doc that quietly
describes aspirations as facts is worse than no doc, because readers trust it and build against it.
This project already has one instance of that failure and it cost real behavior: the frontend
requested `limit=1000` from an endpoint capped at 200, so the request 422s and every faction in the
catalog renders `0` (see [ROADMAP R4](ROADMAP.md#r4-make-pagination-a-convention)). The client was
written against an assumed contract rather than a stated one.

The reference codebase this design borrows from shows the same drift in a milder form: its
`conventions.md` documents page-based pagination as a cross-cutting rule, but only one of its five
list endpoints implements it. It has not been bitten yet, only because its frontend happens to call
just that one endpoint. That is luck, not design.

So: when a principle here stops matching the code, either fix the code or move the marker, in the
same PR that caused the drift.

"Something enforces it" in the **Holds** row means a test, a CI step, a type, or a structural
impossibility. Not discipline. A rule that depends on everyone remembering it is **Partial** at best.
That standard is applied strictly below, including where it is unflattering.

---

## 1. Layering

**API → service → DB, in one direction.**

- `app/api/` is HTTP only: routing, request and response DTOs, status codes, auth dependencies. It
  holds no business rules, and it reaches the database only by handing an injected `Session` to a
  service.
- `app/core/services/` holds business logic. Each service takes a `Session` by injection, may call
  other services, and **never imports from `app/api/`**. It never raises `HTTPException`.
- `app/core/db/` holds models, the engine and session, and migrations. It knows nothing about HTTP.

Services signal failure by raising the typed errors in `app/core/services/errors.py`. The API layer
maps them to HTTP in one place (`app/main.py`). The reason this matters more than it looks: the day
you add a second entry point (a CLI, a scheduled job, a queue consumer), business logic that raises
`HTTPException` has to be rewritten, and business logic that raises `NotFoundError` just works.

**Status: Partial.** The rule is followed nearly everywhere, but two things keep it from **Holds**:

- **Nothing enforces it.** There is no import-contract linter, no architecture test, and CI runs only
  `pytest`. A service could import from `app/api/` tomorrow and all 225 tests would stay green. By
  the definition above, a convention maintained by discipline is Partial. See
  [ROADMAP R11](ROADMAP.md#r11-enforce-the-layering-rule).
- **There is one real violation.** `app/core/security.py` imports from FastAPI and raises
  `HTTPException` (`:88`, `:111`), and `app/core/services/service_auth.py:13` imports it. So the
  service layer transitively depends on an HTTP-coupled module, and `tests/test_security.py:42`
  pins that behavior in place by asserting `HTTPException` is raised. Auth is the usual place this
  leaks, because token decoding sits awkwardly between transport and domain. The fix is to decide
  which half of `security.py` is domain logic (hashing, token encode and decode) and which half is an
  API dependency (`get_current_user` turning a failure into a 401), and split it along that line.

Two details worth keeping deliberately:

- **The typed error hierarchy subclasses the builtin it replaces**
  (`NotFoundError(CodedError, LookupError)`). That is what let the error migration happen
  incrementally instead of as one breaking change, and it is better factored than the reference's
  *service* errors, which hardcode a status code per class. The second base, `CodedError`, is a
  marker that carries no behaviour and exists so the API layer registers one handler for the family
  rather than a per-class tuple a new error can fall off. Scope that credit,
  though: the reference's *API* layer has something this codebase does not, a central `ErrorCode`
  enum plus a code-to-status lookup table so the two can never disagree
  (`attention-api/app/api/errors.py`). That is the piece §2.2 asks you to build, and it is worth
  reading first.
- **Object-level authorization returns 404, not 403**, when a resource is not the caller's
  (`get_owned_army`, `app/api/army.py:91`). That hides whether the id exists at all. The vulnerability
  class avoided is BOLA / IDOR, which sits at the top of the OWASP API Top 10. Inventory is scoped to
  `current_user.id` from the JWT rather than a path parameter, so there is no id to tamper with.

---

## 2. The API contract

The API is consumed by a separate repo. Everything in this section exists so that the frontend can be
written against a stated contract instead of against observed behavior.

### 2.1 Versioning

All endpoints live under `/api/v1/`. `GET /health` is deliberately **unversioned**, because load
balancers and uptime monitors want a path that outlives any API version.

Without a version prefix there is no way to ship a breaking change without breaking every deployed
client at the same instant. The prefix costs one router mount now and a migration later.

**Status: ✅ Done (R5).** Every resource router mounts under a `/api/v1` parent in `app/main.py`;
`GET /health` stays unversioned at the root. The frontend bakes the prefix into `API_PREFIX` in
`src/api/client.ts`, and the Firebase rewrite's `/api/**` already covers `/api/v1/**`.

### 2.2 One error shape, with stable codes

Every error response has the same shape, whatever raised it, carrying:

- a **`code`**: stable, machine-readable, `UPPER_SNAKE_CASE`
- a **`message`**: human-readable, for logs and developers, not for display copy
- an optional **`field`**: which input was wrong, so a form can position the error

The `code` is the contract; the message is not. Clients branch on `code` and never on message text.
Once a code ships its meaning never changes: adding codes is free, redefining one is a breaking
change. The HTTP status and the code always agree, which is best guaranteed by deriving one from the
other through a single lookup rather than by setting them independently at each raise site.

This applies to **all** errors, including the ones the framework raises before a request reaches your
code. Pydantic's schema validation failures must be reshaped into this contract rather than leaking
FastAPI's default array, because a client cannot be asked to parse two different shapes for the same
key.

Concepts: `RequestValidationError` and `@app.exception_handler` in FastAPI, and the structure of
Pydantic v2's `ValidationError.errors()`.

**Status: Not yet.** Two incompatible shapes ship today and the frontend renders one of them as
garbage. See [ROADMAP R2](ROADMAP.md#r2-unify-the-error-shape-and-add-a-code-enum), the
highest-value item on the list.

### 2.3 Pagination, sort, and filter are conventions, not per-endpoint features

Every list endpoint paginates, including the ones that feel small today. One convention, applied
uniformly, documented once.

The total count travels **in the response body**, not in a header. A header is a side channel: it is
invisible to cross-origin JavaScript unless the server names it in the CORS `expose_headers`
allow-list, and when that is missing the client sees no error, just a missing value.

Counts and aggregates are computed **on the server**. A client that downloads a collection to count
it will hit the page cap, and the failure mode is a plausible-looking zero rather than an error.

Choosing between offset-based and keyset (cursor) pagination is a real trade-off: `OFFSET` on a large
table gets linearly slower and can skip or repeat rows when data changes between requests, while
keyset is stable but cannot jump to an arbitrary page. For a mostly-static catalog, offset is
defensible. Decide once, write it down, apply everywhere.

**Status: Not yet.** Not Partial: no endpoint currently satisfies this section. All three rules are
unmet. One of nine list endpoints paginates at all; that one reports its total in an `X-Total-Count`
header rather than the body; and no server-side aggregate exists, which is what pushed the counting
into the client in the first place. See [ROADMAP R4](ROADMAP.md#r4-make-pagination-a-convention).

### 2.4 Failures are traceable end to end

Three pieces, useful only together:

- A generated **request ID** attached to each request, returned in an `X-Request-ID` header, and
  included in error bodies.
- **Structured logs** (machine-parseable, one event per line) carrying that ID on every line.
- **Error reporting** that captures unhandled exceptions with the same ID attached.

Without the ID, a 500 gives the user a generic message and gives you a traceback in a log aggregator
with nothing joining them. Without structured logs, the ID sits in text you cannot query. Without
error reporting, you learn about failures only when someone tells you. Any one of the three on its
own is most of the work for a fraction of the value, which is the argument for treating them as one
piece of work rather than three tickets.

Concepts: ASGI middleware, `request.state`, structured logging, log correlation.

**Status: ✅ Done (R7).** All three pieces, wired together in `app/observability.py`
(`install_observability`): a middleware generates or echoes an `X-Request-ID` and stashes it in a
context variable; a logging filter puts that ID on every JSON log line (`configure_logging`); and the
same ID goes into every error body and the response header. Sentry is initialized only when
`SENTRY_DSN` is set (a true no-op locally and in tests) and tags each event with the request ID. The
unhandled-exception handler still returns a sanitized body.

### 2.5 Resource and verb semantics

- **Collections are plural nouns.** Things that are not resources (a static enum, a computed report)
  do not live inside a resource's id namespace, because they collide with it.
- **Verbs mean what HTTP says they mean.** `GET` is safe, `PUT` and `DELETE` are idempotent, `POST`
  is not. The practical consequence: **a mutation a client might retry must not accumulate.** State a
  target quantity ("set this to 3"), do not send a delta ("add 1"), unless the endpoint takes an
  idempotency key. A network timeout is indistinguishable from a slow success, so any client that
  retries an accumulating `POST` corrupts the data, and no amount of care on the server prevents it.
- **Sibling resources of the same kind expose the same verb set.** If weapons support
  create/list/update/delete, so do abilities and subfactions. An uneven set reads as arbitrary to a
  client author and forces them to consult the code.
- **Membership resources are addressed by their natural key**, not by a surrogate id. For a row that
  exists to say "this user owns N of this unit," the identity is `(owner, unit)`. This is already done
  correctly: `ArmyUnit` and `UserUnit` each carry an `id` column, and the API deliberately never
  exposes it, addressing entries as `/me/inventory/{unit_id}` instead. Exposing the surrogate would
  give clients two ways to name one thing.

**Status: Partial.** The verb semantics, plural nouns, status codes, and the natural-key decision are
right across the board, and the link endpoints are idempotent in practice
(`UnitService.link_weapon` no-ops when the link exists). Three exceptions: the two quantity endpoints
accumulate on `POST`, `GET /factions/taxonomy` is a static enum inside the faction id namespace, and
CRUD coverage is uneven across the catalog resources. See
[ROADMAP R12](ROADMAP.md#r12-make-quantity-mutations-retry-safe-and-even-out-resource-semantics).

### 2.6 Response envelope

**Status: ✅ Decided — no full envelope (R9, option C).** The reference wraps every response in
`{data, errors, meta}` so pagination metadata, multiple errors, and the trace ID have a defined home.
But §2.2 (error shape + code), §2.3 (pagination total in the body), and §2.4 (request ID) already
deliver all three on their own, so the full envelope — the most invasive change available (every
route, every response model, every frontend call site, and now the generated types) — would buy only
structural uniformity. The one functional gap it also closed, **multiple validation errors in one
response**, was closed directly instead: every error body carries a uniform `errors[]` array (see
§2.2 / `docs/api/conventions.md`), with the top level mirroring `errors[0]` for back-compat. Success
responses stay bare. See [ROADMAP R9](ROADMAP.md#r9-decide-on-the-response-envelope).

---

## 3. The transaction boundary

**One request is one transaction, and the request owns it.**

`get_session` commits on success and rolls back on exception. Service methods never call
`session.commit()`. When a service needs the database to assign something before the request ends (a
generated key, or knowing whether a row was inserted or updated), it calls `session.flush()`, which
sends the SQL without ending the transaction.

The reason is compositional. As long as every route calls exactly one mutating service method,
committing inside services looks identical to committing at the boundary. It stops looking identical
the first time a route does two things: with commits inside the services, the first write is durable
the moment it returns, so if the second raises, the rollback covers a transaction that no longer
contains the first write. The result is silently inconsistent data, with no error and no log line.
Any feature of the form "add a unit to the army and decrement the owner's inventory" is exactly this
shape.

Committing at the boundary also removes most `session.refresh()` calls, which exist only because a
commit expires the identity map and forces a re-read of a row you just wrote.

Concepts: the unit of work pattern, `Session.flush()` versus `Session.commit()`, session-per-request.

**Status: Not yet.** Services own the commits today (27 call sites), and the test fixture is built
around that. See [ROADMAP R3](ROADMAP.md#r3-move-the-transaction-boundary-into-get_session).

---

## 4. Persistence and migrations

- Schema changes go through `app/core/db/models.py` plus an Alembic migration. Never raw DDL.
- **The engine is created lazily and cached**, so importing any module does not require a configured
  database (`get_engine`, `app/core/db/connection.py:10`). This is why the suite runs in CI with no
  database service, and it is a better call than the reference, which builds its engine at import
  time and raises if `DATABASE_URL` is missing.
- Migrations are **executable-tested**: something runs `alembic upgrade head` and asserts the result
  matches the models. Model and migration drift is the classic way this fails, and it fails at deploy
  time rather than in review.
- Stat and column names match the datasheet terms in `models.py`.

**Status: ✅ Done (R6).** The parity tier builds its schema by running `alembic upgrade head`, and
`tests/test_migrations.py` autogenerates a diff against the models and fails on any difference — so
model/migration drift is caught in CI, not at deploy time. The fast SQLite tier still uses
`create_all` for speed.

---

## 5. Testing strategy

Two tiers, both of which are needed, because they prove different things.

**Fast tier (default).** In-memory SQLite, one fresh schema per test, foreign keys enforced with
`PRAGMA foreign_keys=ON`. Optimized for iteration: the current suite runs in about four seconds and
needs no services. This is what runs on every keystroke and every push.

**Parity tier.** Real Postgres, schema built by running the migrations. This is what proves the
things SQLite cannot: length constraints, native `UUID` and `JSON` behavior, aware timestamps, and
that the migrations actually apply.

Know precisely what the fast tier does **not** prove, because a green suite is otherwise read as
proof of more than it is. SQLite does not enforce `VARCHAR` length, hands back naive datetimes where
Postgres returns aware ones, and stores UUIDs as `CHAR(32)`.

**External dependencies get a fake, and the seam is where the fake goes.** Splitting the Wahapedia
scraper into a fetch half and a parse half, then testing the parse half against a fixture
(`tests/test_scrape.py`), is the right instinct: the expensive, flaky, rate-limited part stays out of
the test path. The seam itself should eventually be covered too, by a fake fetch rather than by
hitting the live site.

**Status: ✅ Done (R6).** Both tiers exist. `tests/conftest.py` selects the backend: with
`TEST_DATABASE_URL` set it runs the whole suite against Postgres on a schema built by the real
migrations; unset, it uses the fast in-memory SQLite tier. A dedicated variable (not `DATABASE_URL`)
keeps a plain `pytest` from ever touching a developer's database. CI runs both jobs — the fast SQLite
tier and the Postgres parity tier (a `postgres:16` service) — on every push, and `make docker-test`
runs the parity tier in a container. The external-dependency seam (a fake fetch for the scraper)
remains the one open piece.

---

## 6. Docs are the contract, and the contract is generated where possible

- **`openapi.json` is generated from the app** (`make openapi`), checked in, and verified fresh by
  CI. A checked-in generated file that can drift is worse than no file, because it is trusted.
- **The frontend's API types are generated from it**, not hand-maintained. Hand-mirroring a schema
  across a repo boundary is a drift generator, and the drift shows up at runtime rather than at
  compile time.
- **`docs/api/conventions.md` is the human half of the contract**: auth, the error shape and code
  enum, pagination, versioning. The cross-repo rules from section 2 live here.
- **One doc, one audience, one question.** A single file large enough to be nobody's document gets
  skimmed rather than read, and an unread doc is the one that drifts without anyone noticing.
- **`README.md` is the front door**: what this project is, how to run it, and links onward to the
  other docs. It is the only doc a new reader is guaranteed to open.
- Docs change in the same PR as the code that changes them.

**Status: Mostly done (R8, R10).** `openapi.json` is checked in and now **CI-verified fresh**; the
frontend's types are **generated from it** (`npm run gen:api` → `src/api/schema.d.ts`, re-exported by
`types.ts`), not hand-maintained; `docs/api/conventions.md` is the cross-repo contract; and `README.md`
is a real front door (R10). One piece remains: `SPEC.md` is still an 89KB monolith — splitting it into
audience-scoped docs is the outstanding work. See
[ROADMAP R8](ROADMAP.md#r8-split-the-docs-and-generate-the-frontend-types).

---

## 7. Frontend architecture

- **Exactly one HTTP client.** One module owns the base URL, the auth token, error parsing, and
  status handling; one `fetch` call site in the app (`src/api/client.ts:73`). It notifies the auth
  layer of a 401 through a listener rather than importing React, so the data layer stays free of the
  UI framework.
- **Server state lives in TanStack Query, never in `useState`.** Query keys come from a single
  factory, one custom hook per resource, and no inline `useQuery` in components. Invalidation is
  precise, and sibling mutations share an invalidation helper so they cannot drift apart. This is a
  whole architectural layer that the reference frontend does not have, and it should not be given up.
- **Types are generated from `openapi.json`** (see section 6), not hand-written.
- **Data that crossed the network is validated, not asserted.** An `as SomeType` on a parsed response
  is a claim to the compiler, not a check, and no compiler flag validates it. Runtime schema
  validation at the boundary or generated types are what actually catch a shape mismatch.
- **Styling is CSS Modules over a single token file** (`src/styles/theme.css`), one module per
  component. Ad hoc utility strings copy-pasted between components are what this avoids.
- **TypeScript runs in `strict` mode.**

**Status: Partial.** The client, query layer, and styling are the strongest work in either repo.
`strict` is on (R1), the client **validates** the error boundary with zod rather than asserting (R2),
and types are **generated** from `openapi.json` rather than hand-maintained (R8). What remains is
enforcement: no lint rule stops a second `fetch` call site or an inline `useQuery` from appearing, so
those stay conventions rather than guarantees. See
[ROADMAP R1](ROADMAP.md#r1-turn-on-typescript-strict-and-add-a-python-linter).

---

## 8. Tooling and CI

Both repos run the same shape of pipeline on every push and pull request: **lint, type check or
build, then test.** Both repos have a linter. CI cancels superseded runs with a `concurrency` group.

The frontend is the model here: it runs `eslint`, `tsc -b && vite build`, and `vitest run` in CI. Both
reference repos have no CI at all, so nothing enforces their checks on a push.

**Status: Partial.** Both repos have CI, and the frontend pipeline is the right shape. Three
exceptions: the backend has no linter or formatter of any kind, its pipeline runs only `pytest`, and
neither repo's CI has a `concurrency` group. See
[ROADMAP R1](ROADMAP.md#r1-turn-on-typescript-strict-and-add-a-python-linter).
