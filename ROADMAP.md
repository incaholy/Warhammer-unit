# Roadmap: closing the gap to the target architecture

The delta between the code today and [`ARCHITECTURE.md`](ARCHITECTURE.md). Each item names the
principle it satisfies, the evidence that it is missing, and the concepts to read up on. No
implementations: the shape and the reasoning are here, the code is yours.

**Item IDs are stable labels, not an ordering.** The table below is ordered by value per unit of
effort; the sections that follow are in ID order so links stay put as priorities change.

**Evidence.** Every claim below was reproduced against the running code rather than read off a commit
message or a doc: `pytest` (225 passed), `npm test` (144 passed), `npm run lint`, `npm run build`,
plus throwaway probe scripts for SQL query counting, SQLite DDL inspection, and error-shape
enumeration. Where something is a latent risk rather than a live defect, it says so.

Correctness bugs are tracked in [`CODE-REVIEW.md`](CODE-REVIEW.md) and are not repeated here. Note
that its four findings have since been fixed and its test counts predate the current suite; it is
worth reading for the reasoning, not as a live bug list.

| Order | # | Item | Satisfies | Effort |
|---|---|---|---|---|
| 1 | [R1](#r1-turn-on-typescript-strict-and-add-a-python-linter) | TypeScript `strict`, ruff, lint in CI | §7, §8 | Trivial |
| 2 | [R2](#r2-unify-the-error-shape-and-add-a-code-enum) | Unify the error shape, add a code enum | §2.2 | Focused |
| 3 | [R3](#r3-move-the-transaction-boundary-into-get_session) | Move the transaction boundary | §3 | Focused, mostly deletion |
| 4 | [R10](#r10-write-a-readme) | Write a README | §6 | Trivial |
| 5 | [R11](#r11-enforce-the-layering-rule) | Enforce the layering rule | §1 | Small |
| 6 | [R4](#r4-make-pagination-a-convention) | Pagination everywhere, server-side aggregates | §2.3 | Moderate, spans both repos |
| 7 | [R5](#r5-add-the-apiv1-prefix) | `/api/v1` prefix | §2.1 | Trivial, bundle with deploy work |
| 8 | [R6](#r6-add-a-postgres-parity-tier-driven-by-migrations) | Postgres parity tier driven by migrations | §4, §5 | Moderate |
| 9 | [R7](#r7-add-a-request-correlation-id) | Request correlation ID | §2.4 | Small |
| 10 | [R8](#r8-split-the-docs-and-generate-the-frontend-types) | Split the docs, generate frontend types | §6, §7 | Moderate, incremental |
| 11 | [R9](#r9-decide-on-the-response-envelope) | Decide on the response envelope | §2.5 | Decision first |

R2, R4 and R5 each change a contract the other repo depends on. Land the backend and frontend sides
of each together, or the gap becomes a mismatch you introduced on purpose.

---

## R1. Turn on TypeScript `strict` and add a Python linter

**Satisfies:** §7 (frontend), §8 (tooling).

**What is missing.** `strict` appears nowhere in the frontend tsconfigs, so `strictNullChecks` and
`noImplicitAny` are both off (`grep -rn "strict" tsconfig*.json` finds no match).

The backend has no linter or formatter at all: no `ruff.toml`, no `pyproject.toml`, no `setup.cfg`.
Backend CI runs `pytest` and nothing else, while the frontend pipeline already runs lint, build, and
test. Neither pipeline has a `concurrency` group, so superseded runs keep consuming runner time on
every push to an open PR. The frontend also has no `.env.example`, so the only record of
`VITE_API_BASE_URL` is prose in `SPEC.md` and the declaration in `src/vite-env.d.ts`.

**Why it is first.** Turning `strict` on costs nothing today. Running
`npx tsc -p tsconfig.app.json --strict --noEmit` exits 0 with zero errors: the code is already
strict-clean. The flag just stops the next `null` dereference from compiling.

**Be clear about what this does not do.** It would *not* have caught the error-shape bug in R2. That
bug hides behind `res.json()` returning `any` and an `as ApiErrorBody` assertion, and no compiler
flag validates an assertion against what actually arrived at runtime. Turn `strict` on because it is
free and holds the line going forward, not because it fixes something you have.

**Shape of the work.** One line in each tsconfig. Add **ruff** to the backend (it replaces flake8,
isort, and black in one tool) and add a lint step to `ci.yml` so both pipelines have the same shape.
Add a `concurrency` group to both, and commit a frontend `.env.example`.

---

## R2. Unify the error shape and add a code enum

**Satisfies:** §2.2. **This is the highest-value item on the list.**

**What is missing.** The API emits two mutually incompatible error shapes. Probed against the running
app:

| Request | Status | `detail` type | Body |
|---|---|---|---|
| `GET /units/<unknown uuid>` | 404 | `str` | `{"detail": "unit ... not found"}` |
| `GET /me` (no token) | 401 | `str` | `{"detail": "Not authenticated"}` |
| `POST /auth/register` (bad email) | 422 | **`list`** | `{"detail": [{"type": "value_error", "loc": [...], "msg": "..."}]}` |
| `GET /units/not-a-uuid` | 422 | **`list`** | `{"detail": [{"type": "uuid_parsing", ...}]}` |

There is also no stable machine-readable code anywhere: clients get a free-text message and an HTTP
status.

**What it already costs.** The frontend types the body as `detail?: string`
(`src/api/client.ts:29`) and assigns it straight to the user-facing message (`client.ts:90`). When a
422 arrives, `detail` is an array, so the message becomes that array stringified: `[object Object]`
for one error, `[object Object],[object Object]` for two. Whether the user sees it in a toast or
inline depends on the call path (mutations routed through the `MutationCache` in `main.tsx:17-20`
toast it; `AuthView` catches and renders inline at `:131-133`), but the text is garbage either way.

The missing code has a second cost: `ArmyView` collapses 404, 500, network failure, and expired token
into the single sentence "Army not found" (frontend `CODE-REVIEW.md` finding 3), because there is
nothing machine-readable to branch on.

**The lesson underneath this one.** The frontend bug is not a frontend bug. It is what happens when a
backend never declares one error shape: the client has to guess, guesses the common case, and the
uncommon case renders as garbage. Note also what failed to catch it. Not the types, for the reason in
R1. Not the tests either, because they stub `fetch` and the stub returns the shape the author
expected. **Every `as` on data that crossed the network is an unverified assumption.**

**Shape of the work.** Two moves on the backend. Register a `RequestValidationError` handler so
Pydantic's 422 is reshaped into the same contract the `ServiceError` handler already produces,
instead of leaking FastAPI's default array. Then add a `code` field carrying a stable
`UPPER_SNAKE_CASE` value, with a single enum and a code-to-status lookup so the two can never
disagree. Your `ServiceError` subclasses already carry `field`, so part of this is done.

The reference has this piece and it is the one place worth copying closely:
`attention-api/app/api/errors.py` is a small `ErrorCode(StrEnum)` plus a code-to-status dict, which
is all the mechanism needs to be.

On the frontend, branch on `code` instead of on status or message text, and replace the `as`
assertion with validation at the boundary.

**Concepts:** `RequestValidationError` and `@app.exception_handler` in FastAPI; the structure of
Pydantic v2's `ValidationError.errors()` and its `loc` field; runtime schema validation on the client
(look up `zod`).

---

## R3. Move the transaction boundary into `get_session`

**Satisfies:** §3.

**What is missing.** Services call `self.session.commit()` directly, at **27 call sites**. Running
`grep -rc "session.commit()" app/core/services/*.py` gives 6 in `service_army.py`, 16 in
`service_unit.py`, 3 in `service_inventory.py`, and 2 in `service_user.py`. `get_session` rolls back
on exception but never commits (`app/core/db/connection.py:27`), and its docstring says so
deliberately.

**This is latent, not live.** I read all six routers and all five service classes: every mutating
route calls exactly one committing service method, and no service method commits twice. There is no
partial-write bug today. The invariant holds by coincidence of the current feature set, not by
construction.

It breaks on the first route that does two things. "Add a unit to an army and decrement the owner's
inventory" is two service calls: the first commit is durable the instant it returns, so if the second
raises, `get_session` rolls back a transaction that no longer contains the first write. A unit in the
army, inventory unchanged, no error, no log. No current test would catch it.

**Why now rather than later.** Doing this before that route exists is a mechanical refactor. Doing it
after is debugging silent data corruption.

**Shape of the work,** mostly deletion. Move the commit into `get_session`, delete the 27 in-service
commits, and use `session.flush()` where a service needs the database to assign a value before the
request ends (a generated key, or `add_unit` needing to know whether the row was new). Two things
that are not mechanical:

- **`refresh()` after a flush behaves differently than after a commit.** There are 20 `refresh()`
  calls, one after each commit that returns a row (the other seven commits are deletes). Work out
  what each is actually for; several become unnecessary once the commit moves.
- **The test fixture overrides `get_session` with a bare session** (`tests/conftest.py`), so commit
  behavior under test will stop matching production. The fixture has to move in the same change, or
  the suite passes while asserting the wrong thing.

**Concepts:** the unit of work pattern; `Session.flush()` versus `Session.commit()`;
session-per-request.

---

## R4. Make pagination a convention

**Satisfies:** §2.3.

**What is missing.** Exactly one endpoint paginates:

| Endpoint | Pagination |
|---|---|
| `GET /units` | `limit` / `offset`, `le=200`, total in an `X-Total-Count` header |
| `GET /me/armies` | none |
| `GET /me/inventory` | none |
| `GET /factions` | none |
| `GET /factions/taxonomy` | none |
| `GET /weapons` | none |
| `GET /abilities` | none |
| `GET /me/armies/{id}/shortfall` | none |
| `GET /me/armies/{id}/validate` (`issues`) | none |

Three distinct problems follow.

**The client invented a workaround and it broke.** Because no endpoint answers "how many units per
faction," `CatalogView` requests `limit=1000` to count client-side. The API caps at 200, so the
request 422s and every faction renders `0` (frontend `CODE-REVIEW.md` finding 1). The structural
cause is that the API exposes no aggregate, so the client tried to compute one.

**`X-Total-Count` is a side channel with a trap in it.** `app/main.py:38-43` sets `allow_origins`,
`allow_methods`, and `allow_headers`, but **not** `expose_headers`, and a response header is
invisible to cross-origin JavaScript unless it is named there. This is **not a live bug**:
`DEPLOY-GCP.md:151` puts the SPA and API on one origin via a Firebase rewrite, so CORS is not
involved. But `ALLOWED_ORIGINS` exists precisely to support the cross-origin deployment, and in that
mode `headers.get('X-Total-Count')` (`src/api/units.ts:36`) returns `null` and the catalog silently
falls back to the row count. Silently is the problem. A total in the response body is not subject to
this rule at all.

**Unbounded lists are a scaling cliff.** `/weapons` and `/abilities` grow with the same catalog that
made `/units` need a cap, which the frontend review measured at roughly 1,331 units against a seeded
database.

**The reference is a cautionary tale here, not a model.** Its `conventions.md` documents page-based
pagination as a cross-cutting rule, but only one of its five list endpoints implements it; the other
four use `offset`/`limit` and return no pagination metadata. It has the same inconsistency plus a
document asserting otherwise, which is strictly worse than no document. It has not been bitten only
because its frontend happens to call just the compliant endpoint. **A convention that lives only in
prose is not a convention.** What makes it real is applying it uniformly and having something check,
which is the argument for writing it into `docs/api/conventions.md` (R8) and generating client types
from `openapi.json` so a drifting endpoint shows up as a diff.

**Shape of the work.** Pick one convention (see §2.3 for the offset versus keyset trade-off), apply it
to every list endpoint including the small ones, move the total into the body, and add the
per-faction count as a `GROUP BY` aggregate on the server.

**Concepts:** keyset (cursor) pagination and what it fixes; CORS `expose_headers`; SQL `GROUP BY`
aggregates versus client-side derivation.

---

## R5. Add the `/api/v1` prefix

**Satisfies:** §2.1.

**What is missing.** Every route mounts at the root (`app/main.py:94-99`). There is no way to ship a
breaking change without breaking the deployed frontend simultaneously.

**Why it is nearly free right now.** `DEPLOY-GCP.md:155` already tells you to mount the routers under
a parent prefix so the Firebase rewrite can forward `/api/**` to Cloud Run. Make it `/api/v1` rather
than `/api` while you are in there. Today the cost is one `APIRouter(prefix=...)` plus the frontend
base URL. Once the API has real clients it is a migration.

Keep `/health` unversioned. That exception is the interesting part of the convention, not a footnote:
platform tooling wants a path that survives version changes.

---

## R6. Add a Postgres parity tier driven by migrations

**Satisfies:** §4 (migrations), §5 (testing).

**What is missing.** The whole suite runs on in-memory SQLite while production is Postgres, and no
test ever runs a migration.

**The parity harness looks like it exists and does not.** `make docker-test` boots a Postgres
container and sets `DATABASE_URL` to it, but `tests/conftest.py:31` hardcodes
`create_engine("sqlite://")` and never reads that variable. So the containerized run executes the
entire suite on in-memory SQLite and passes. **This is the most misleading state in the repo**,
because it reads as Postgres coverage on the tin. Fixing the conftest is the load-bearing part of
this item; adding a CI job on top of it is the easy part.

SQLite does not enforce what Postgres will. Probed directly, a column declared `max_length=128`
accepted a 500-character value and read it back at length 500. Postgres raises
`value too long for type character varying(128)` on the same insert. The generated DDL diverges too:

| Column | SQLite | PostgreSQL |
|---|---|---|
| `id` | `CHAR(32)` | `UUID` |
| `created_at` | `DATETIME` | `TIMESTAMP WITH TIME ZONE` |

So three classes of bug can pass CI and fail in production: **length-constraint violations**,
**timestamp handling** (SQLite returns naive datetimes, Postgres aware ones, so comparing or
serializing them can raise only in production), and **native type behavior** (UUID and JSON). Nothing
reads `created_at` outside the models today, so the timestamp class is latent; it activates the first
time you sort or filter by it.

Separately, `tests/conftest.py:44` builds the schema with `SQLModel.metadata.create_all()`, which
reads the models and bypasses Alembic entirely. **Nothing verifies that `alembic upgrade head`
produces a schema matching `models.py`**, and that failure surfaces at deploy time.

**Shape of the work.**

1. Make the test engine configurable so the fixture uses `DATABASE_URL` when it is set and falls back
   to SQLite otherwise. Without this, the rest of the item is decorative.
2. In the Postgres path, build the schema by running `alembic upgrade head` rather than `create_all`.
   That one change makes every migration executable-tested.
3. Add the CI job. Keep the fast SQLite suite as the default for local iteration: you want both
   tiers, not a replacement.

**Worth reading first:** the reference has a version of this at
`attention-api/tests/db/test_schema_checks.py`, which runs real inserts against Postgres to prove
CHECK constraints and a case-insensitive unique index behave. It has the same weakness described
above (it points at a database rather than a freshly migrated one), so read it for the shape of the
assertions and not for the harness.

**Concepts:** Alembic's autogenerate diff check (run autogenerate against the migrated schema and
fail on a non-empty diff, which directly asserts that models and migrations agree).

---

## R7. Add a request correlation ID

**Satisfies:** §2.4.

**What is missing.** `app/main.py:80` logs a full traceback and returns
`{"detail": "internal server error"}`. Nothing joins the user's report to that log line.

**Shape of the work.** A small HTTP middleware that generates an ID (or echoes an inbound
`X-Request-ID`), stashes it on `request.state`, returns it as a response header, and includes it in
error bodies and log records. This does not require the envelope from R9.

**Concepts:** ASGI middleware; `request.state`; structured logging with a correlation ID.

---

## R8. Split the docs and generate the frontend types

**Satisfies:** §6 (docs), §7 (frontend types).

**What is missing.** There is no `docs/` directory, and `SPEC.md` is 89KB in a single file. The
organizing principle to apply is one doc, one audience, one question. The single highest-value split
is **`docs/api/conventions.md`**, because that is the cross-repo contract: it is where R2, R4 and R5
get written down. Right now that contract exists only as the union of what the code happens to do,
which is why the frontend guessed wrong twice.

**The generated half is an asset you are not using.** `openapi.json` is checked in, `make openapi`
regenerates it, and it is currently in sync with the app, which is ahead of the reference. Two things
follow:

- Add a CI step that regenerates it and fails if the committed copy is stale. It is fresh today by
  diligence, and diligence is exactly what CI is for. A checked-in generated file that can drift is
  worse than no file, because it is trusted.
- The frontend hand-maintains `src/api/types.ts` to mirror the backend schema, and its own header
  comment says it should be generated with `openapi-typescript`. The frontend `Makefile:27-28`
  already has a `gen-api` target (marked "planned"), but it calls `npm run gen:api`, **which is not a
  script in `package.json`**, so the target fails if run. One dependency and one script deletes an
  entire class of drift.

Also: `CLAUDE.md:36` says stat names match `models.py` and lists `save`, but the field is `armor_save`
(`app/core/db/models.py:233`). `SPEC.md` uses the correct name, so this is drift in the instructions
file specifically. Small, but it is exactly what the docs-change-with-code rule exists to prevent.

---

## R9. Decide on the response envelope

**Satisfies:** §2.5. **The deliverable here is a written decision, not necessarily code.**

The reference wraps every response in `{data, errors, meta}`, with `meta.trace_id` echoed in an
`X-Request-ID` header and `meta.pagination` on its one paginated list endpoint.

**The cost is real**: every route, every response model, every frontend call site. "The reference does
it" is not a reason. What it actually buys:

1. **Pagination metadata travels in the body**, which is the R4 argument.
2. **Multiple validation errors in one response.** The current shape carries one `field`, so a form
   with three bad inputs takes three round-trips to discover, even though Pydantic found all three at
   once.
3. **A trace ID on every response**, which R7 delivers on its own.

Given R2, R4 and R7 capture most of the value, treat this as an explicit yes or no and write the
decision down either way. If yes, do it in one migration and before the API has more consumers: a
half-enveloped API is worse than either end state.

---

## R10. Write a README

**Satisfies:** §6.

`README.md` is two lines. Someone landing on the repo learns nothing about what this is or how to run
it, and it is the one doc every new reader opens. The content largely exists already; it needs a
front door linking to `ARCHITECTURE.md`, `ROADMAP.md`, `SPEC.md`, `MVP.md`, and `DEPLOY.md`. Carried
over from `CODE-REVIEW.md`, still open.

---

## R11. Enforce the layering rule

**Satisfies:** §1.

**What is missing.** The layering rule is followed almost everywhere and enforced by nothing. There is
no import-contract check, no architecture test, and CI runs only `pytest`. A service could import
from `app/api/` tomorrow and all 225 tests would stay green. §1 is marked Partial for this reason
alone.

**There is also one real violation to clean up first.** `app/core/security.py` imports FastAPI and
raises `HTTPException` (`:88`, `:111`), and `app/core/services/service_auth.py:13` imports it, so the
service layer transitively depends on an HTTP-coupled module. `tests/test_security.py:42` asserts
`HTTPException` is raised, which pins it in place.

Auth is the usual place this leaks, because token handling sits between transport and domain. The
split to make: token encode and decode and password hashing are domain concerns and belong in the
service layer or a neutral module; `get_current_user`, which turns a decode failure into a 401 with a
`WWW-Authenticate` header, is an API dependency and belongs in `app/api/`. Once split, the service
layer imports only the domain half.

**Shape of the work.** Fix the violation, then add a check so it cannot come back. Two options worth
comparing: **import-linter**, which expresses "layer A must not import layer B" as a config file and
runs in CI, or a **ruff** `flake8-tidy-imports` banned-api rule, which is lighter but less precise.
Either way the check belongs in the same CI lint step added in R1.

**Concepts:** import-linter layered contracts; why an architecture rule that only exists in a
document is indistinguishable from no rule.
