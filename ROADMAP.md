# Roadmap: closing the gap to the target architecture

The delta between the code today and [`ARCHITECTURE.md`](ARCHITECTURE.md). Each item names the
principle it satisfies, the evidence that it is missing, and the concepts to read up on. No
implementations: the shape and the reasoning are here, the code is yours.

**Item IDs are stable labels, not an ordering.** The table below is ordered by value per unit of
effort; the sections that follow are in ID order so links stay put as priorities change.

**Where the documents disagree.** Routes and architecture are settled in [SPEC.md](SPEC.md),
Kill Team design in [KILLTEAM.md](KILLTEAM.md), scope and current state in [MVP.md](MVP.md),
and build order here. Where any disagree about **design intent**, the one whose subject it is
wins — build order here. Where they disagree about **what the code does today**, neither wins:
check the code.

**Evidence.** Every claim below was reproduced against the running code rather than read off a commit
message or a doc. At `4dbef9d` here: `pytest` (**744 passed, 1 skipped**, 745 collected). At
`warhammer_web` `440da79`: `npm test` (**669** `it`/`test` blocks across **60** spec files) and
`npm run test:browser` (**19** blocks across **2** Playwright specs), `npm run lint` and
`npm run build`, and throwaway probe scripts for SQL query counting, SQLite DDL inspection
and error-shape enumeration. Each repository is pinned to its own commit. The two test
commands are counted separately because `vite.config.ts` excludes `browser/**` from
vitest, so any single figure attributed to `npm test` is wrong whichever number it
carries. Where something is a latent risk rather than a live defect, it says so.

**A problem statement in a done entry is the historical record, not the current state.** It is kept
because it is why the work was done; what landed is in that entry's status marker, and the marker
wins. The figures inside those statements were true when the entry was written — the suite was 225
tests then — so re-derive a count before citing one.

Correctness bugs are tracked in [`CODE-REVIEW.md`](CODE-REVIEW.md) and are not repeated here. Note
that its four findings have since been fixed and its test counts predate the current suite; it is
worth reading for the reasoning, not as a live bug list.

| Order | # | Item | Satisfies | Effort |
|---|---|---|---|---|
| 1 | [R1](#r1-turn-on-typescript-strict-and-add-a-python-linter) | TypeScript `strict`, ruff, lint in CI | §7, §8 | Trivial |
| 2 | [R2](#r2-unify-the-error-shape-and-add-a-code-enum) | Unify the error shape, add a code enum | §2.2 | Focused |
| 3 | [R3](#r3-move-the-transaction-boundary-into-get_session) | Move the transaction boundary | §3 | Focused, mostly deletion |
| 4 | [R12](#r12-make-quantity-mutations-retry-safe-and-even-out-resource-semantics) | Make quantity mutations retry-safe | §2.5 | Focused |
| 5 | [R10](#r10-write-a-readme) | Write a README | §6 | Trivial |
| 6 | [R11](#r11-enforce-the-layering-rule) | Enforce the layering rule | §1 | Small |
| 7 | [R4](#r4-make-pagination-a-convention) | Pagination everywhere, server-side aggregates | §2.3 | Moderate, spans both repos |
| 8 | [R5](#r5-add-the-apiv1-prefix) | `/api/v1` prefix | §2.1 | Trivial, bundle with deploy work |
| 9 | [R6](#r6-add-a-postgres-parity-tier-driven-by-migrations) | Postgres parity tier driven by migrations | §4, §5 | Moderate |
| 10 | [R7](#r7-land-observability-request-id-structured-logs-error-reporting) | Observability: request ID, structured logs, error reporting | §2.4 | Small |
| 11 | [R8](#r8-split-the-docs-and-generate-the-frontend-types) | Split the docs, generate frontend types | §6, §7 | Moderate, incremental |
| 12 | [R9](#r9-decide-on-the-response-envelope) | Decide on the response envelope | §2.6 | Decision first |
| 13 | [R13](#r13-unit-composition-and-points-that-follow-model-count) | Unit composition, and points that follow model count | §2.5 | Moderate; a migration with a backfill |
| 14 | [R14](#r14-two-catalog-reads-whose-contract-does-not-match-what-they-serve) | Two catalog reads whose contract does not match what they serve | §2.2, §2.5 | Small each; one is a dependency change |
| 15 | [R15](#r15-two-unpaginated-reads-return-complete-results-with-no-cap) | Two unpaginated reads return complete results with no cap | §2.3 | Small; a cap or a page on two reads |
| 16 | [R16](#r16-wrap-every-response-in-an-envelope) | Wrap every response in an envelope | §2.6 | Large; **no** schema change, both repositories together |
| 17 | [R17](#r17-publish-the-checkd-vocabularies-as-enums) | Publish the CHECK'd vocabularies as enums | §2.2 | Small; one annotation per column |
| 18 | [R18](#r18-publish-the-error-responses) | Publish the error responses | §2.2 | Small; one shared schema, declared per router |

R1–R12 are done. R13 to R18 are not started. R13 changes a shipped schema rather than a
draft one, and R16 changes a contract the other repository generates types from.

R2, R4 and R5 each change a contract the other repo depends on. Land the backend and frontend sides
of each together, or the gap becomes a mismatch you introduced on purpose.

---

## R1. Turn on TypeScript `strict` and add a Python linter

**Satisfies:** §7 (frontend), §8 (tooling).

**Status: ✅ Done (R1).** All five gaps are closed. `"strict": true` is set in
both frontend tsconfigs (`tsconfig.app.json`, `tsconfig.node.json`); the backend
has a `ruff.toml`; backend CI runs `ruff check` then `ruff format --check` ahead
of the suite, so the pipeline is lint → format check → import contracts →
openapi freshness → tests, with the Postgres parity tier as a second job; both
repos declare a `concurrency` group keyed on workflow+ref, cancelling superseded
PR runs; and the frontend has a committed `.env.example`. There is still no
`pyproject.toml` or `setup.cfg`, deliberately — `ruff.toml` is the whole config.

**What was missing.** `strict` appeared nowhere in the frontend tsconfigs, so `strictNullChecks` and
`noImplicitAny` were both off.

The backend had no linter or formatter at all. Backend CI ran `pytest` and nothing else, while the
frontend pipeline already ran lint, build, and test. Neither pipeline had a `concurrency` group, so
superseded runs kept consuming runner time on every push to an open PR. The frontend also had no
`.env.example`, so the only record of `VITE_API_BASE_URL` was prose in `SPEC.md` and the declaration
in `src/vite-env.d.ts`.

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

**Status: ✅ Done (2026-08).** Backend — every error source returns
`{detail, code, field?}` with a stable `ErrorCode` (service, auth,
request-validation, integrity, and unhandled), status derived from code via a
single `CODE_STATUS` map. Frontend — the API client validates the error body with
zod (the `as` cast is gone) and carries `code`; `ArmyView` branches on it instead
of collapsing to "not found"; a shared `messageForError` helper centralizes
user-facing messages. Out of scope (larger follow-up): validating *success*
response bodies (`as T`) with per-endpoint schemas — R2 was the error shape only.

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

**What it cost, before this entry closed it.** *Original reasoning kept; R2 fixed all of it,
so this describes the client as it was and not as it is.* ~~The frontend typed the body as
`detail?: string` and assigned it straight to the user-facing message.~~ When a 422 arrived,
`detail` was an array, so the message became that array stringified: `[object Object]` for one
error, `[object Object],[object Object]` for two. Whether the user saw it in a toast or inline
depended on the call path — mutations routed through the `MutationCache` in `main.tsx` toasted
it, `AuthView` caught and rendered inline — and both of those paths still exist. What no longer
exists is the garbage text: `ApiError` now carries a parsed `code`, and the two schemas that
parse it are named in `MVP.md`.

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

**Status: ✅ Done (R3).** The boundary is `get_session`
(`app/core/db/connection.py`), which commits once after the handler returns and
rolls back the whole unit of work on any exception; its docstring now states that
services never commit. Zero in-service commits remain —
`grep -rc "session.commit()" app/core/services/*.py` reports **0 in every
service** — and a service that needs a key or a constraint error during the
request calls `flush()` instead. The test fixture moved in the same change, which
is the half that usually gets missed.

**What was missing.** Services called `self.session.commit()` directly, at **27 call sites**:
`grep -rc "session.commit()" app/core/services/*.py` gave 6 in `service_army.py`, 16 in
`service_unit.py`, 3 in `service_inventory.py`, and 2 in `service_user.py`. `get_session` rolled back
on exception but never committed, and its docstring said so deliberately.

**It was latent, not live.** All six routers and all five service classes were read: every mutating
path called exactly one committing service method, and no service method committed twice. There was
no partial-write bug. The invariant held by coincidence of the feature set of the day, not by
construction.

It would have broken on the first route that did two things. "Add a unit to an army and decrement the owner's
inventory" is two service calls: the first commit is durable the instant it returns, so if the second
raises, `get_session` rolls back a transaction that no longer contains the first write. A unit in the
army, inventory unchanged, no error, no log. No current test would catch it.

**Why now rather than later.** Doing this before that route exists is a mechanical refactor. Doing it
after is debugging silent data corruption.

**Shape of the work was,** mostly deletion. Move the commit into `get_session`, delete the 27
in-service commits, and use `session.flush()` where a service needs the database to assign a value before the
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

**Status: ✅ Done (R4).** One convention, applied uniformly: all twelve
collection reads return `Page[T]` (`{items, total, limit, offset}`) through the
shared `PageParams` / `paginate` helpers in `app/api/pagination.py`, with the
offset-based choice written down, a `limit` capped at 200, and an `id` tiebreaker
on every ordering so a page boundary cannot repeat or skip a row. The total
travels **in the body** and no code sets `X-Total-Count` any more — it survives only as a
comment in `app/main.py` recording why it was removed —
so the CORS trap described below cannot fire. The missing aggregate was
specifically the per-faction count, and `GET /units/facets` now returns it as one
`GROUP BY`, which is what let the frontend drop its `limit=1000`
count-in-the-client hack. Five reads sit outside the convention with a recorded
reason each — see §2.3 and R15.

**What was missing.** Exactly one path paginated. The table is as this entry found it, including the
taxonomy read at a path R12 has since changed:

| Path (as of this entry) | Pagination then |
|---|---|
| `GET /units` | `limit` / `offset`, `le=200`, total in an `X-Total-Count` header |
| `GET /me/armies` | none |
| `GET /me/inventory` | none |
| `GET /factions` | none |
| `GET /factions/taxonomy` — now `GET /taxonomy` (R12) | none |
| `GET /weapons` | none |
| `GET /abilities` | none |
| `GET /me/armies/{id}/shortfall` | none |
| `GET /me/armies/{id}/validate` (`issues`) | none |

Three distinct problems followed.

**The client invented a workaround and it broke.** Because nothing answered "how many units per
faction," `CatalogView` requested `limit=1000` to count client-side. The API capped at 200, so the
request 422d and every faction rendered `0` (frontend `CODE-REVIEW.md` finding 1). The structural
cause was that the API exposed no aggregate, so the client tried to compute one.

**`X-Total-Count` was a side channel with a trap in it.** The CORS middleware set `allow_origins`,
`allow_methods`, and `allow_headers`, but **not** `expose_headers`, and a response header is
invisible to cross-origin JavaScript unless it is named there. It was **not a live bug**:
`DEPLOY-GCP.md` puts the SPA and API on one origin via a Firebase rewrite, so CORS was not involved.
But `ALLOWED_ORIGINS` exists precisely to support the cross-origin deployment, and in that mode
`headers.get('X-Total-Count')` returned `null` and the catalog silently fell back to the row count.
Silently was the problem. A total in the response body is not subject to this rule at all, which is
why moving it there fixed this structurally rather than by configuration.

**Unbounded lists were a scaling cliff.** `/weapons` and `/abilities` grow with the same catalog that
made `/units` need a cap, which the frontend review measured at roughly 1,331 units against a seeded
database.

**The reference is a cautionary tale here, not a model.** Its `conventions.md` documents page-based
pagination as a cross-cutting rule, but only one of its five collection reads implements it; the other
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

**Status: ✅ Done (R5).** Every resource router mounts under one
`APIRouter(prefix="/api/v1")` parent in `app/main.py`. Of the 49 paths in
`openapi.json`, 48 are under `/api/v1` and the only one that is not is `/health`,
unversioned on purpose. The frontend bakes the prefix into `API_PREFIX` in
`src/api/client.ts`, and the Firebase rewrite's `/api/**` already covers
`/api/v1/**`.

**What was missing.** Every path mounted at the root. There was no way to ship a breaking change
without breaking the deployed frontend simultaneously.

**Why it was nearly free at the time.** `DEPLOY-GCP.md` already told you to mount the routers under
a parent prefix so the Firebase rewrite could forward `/api/**` to Cloud Run. Make it `/api/v1`
rather than `/api` while you are in there. The cost then was one `APIRouter(prefix=...)` plus the
frontend base URL. Once the API has real clients it is a migration.

Keep `/health` unversioned. That exception is the interesting part of the convention, not a footnote:
platform tooling wants a path that survives version changes.

---

## R6. Add a Postgres parity tier driven by migrations

**Satisfies:** §4 (migrations), §5 (testing).

**Status: ✅ Done (R6).** Both tiers exist and the conftest chooses between
them. `tests/conftest.py` reads **`TEST_DATABASE_URL`**: set, the whole suite runs
against Postgres on a schema built by `alembic upgrade head`; unset, it falls
back to the fast in-memory SQLite tier with `create_all`. The dedicated variable
rather than `DATABASE_URL` is what keeps a plain `pytest` from touching a
developer's database, and the fixture refuses a URL that does not name a test
database. `tests/test_migrations.py` autogenerates a diff against the models and
fails on any difference, and CI runs a second `parity` job (a `postgres:16`
service) that does `alembic upgrade head` then `alembic check` before the suite.
`make docker-test` runs the same tier in a container, and now actually on
Postgres.

**What was missing.** The whole suite ran on in-memory SQLite while production was Postgres, and no
test ever ran a migration.

**The parity harness looked like it existed and did not.** `make docker-test` booted a Postgres
container and set `DATABASE_URL` to it, but `tests/conftest.py` hardcoded `create_engine("sqlite://")`
and never read that variable. So the containerized run executed the entire suite on in-memory SQLite
and passed. **That was the most misleading state in the repo**, because it read as Postgres coverage
on the tin. Fixing the conftest was the load-bearing part of this item; adding a CI job on top of it
was the easy part.

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

Separately, the fixture built the schema with `SQLModel.metadata.create_all()`, which reads the
models and bypasses Alembic entirely. **Nothing verified that `alembic upgrade head` produced a
schema matching `models.py`**, and that failure surfaces at deploy time. `tests/test_migrations.py`
and the parity job's `alembic check` are what closed it; `create_all` survives on the fast tier
alone, for speed.

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

## R7. Land observability: request ID, structured logs, error reporting

**Satisfies:** §2.4. This is `SPEC.md`'s **M5** item (`SPEC.md:1309`), which M5 itself now records
as done.

**Status: ✅ Done (R7).** All three pieces are wired together in
`app/observability.py` (`install_observability`): a middleware generates or echoes
an `X-Request-ID` and stashes it in a context variable; `configure_logging`
installs a `logging.config.dictConfig` whose filter puts that ID on every JSON log
line; and `init_sentry` initializes `sentry-sdk` only when `SENTRY_DSN` is set — a
true no-op locally and in tests — tagging each event with the same ID. The ID also
goes into every error body and onto the response header, including the catch-all
500, so the pin in `requirements.txt` buys signal instead of image weight.

**What was missing.** M5 scoped three pieces. Their state when this entry was written:

| Piece | State then |
|---|---|
| Sanitized catch-all `Exception` → 500 so internals never leak | **Done**, shipped as part of the `CODE-REVIEW.md` finding 2 fix |
| Wire `sentry-sdk`, no-op when `SENTRY_DSN` is unset | **Not started then.** No occurrence of `sentry` or `SENTRY_DSN` in `app/`, `scripts/`, or `.env.example` |
| Basic structured logging | **Not started then.** No `dictConfig`, `basicConfig`, or `structlog` anywhere; only `logging.getLogger("app")` in `main.py` |

`sentry-sdk==2.51.0` was already pinned in `requirements.txt`, so it was installed into the
production image and did nothing: image weight and dependency surface for zero signal. Either wire it
or drop the pin. An unused dependency that looks like a feature is worse than an absent one, because
it reads as covered.

**Why the three go together.** The catch-all handler logged a full traceback and returned
`{"detail": "internal server error"}`, with nothing joining the user's report to that log line. Adding
structured logging alone gives you queryable lines you still cannot correlate to a request. Adding
Sentry alone gives you an exception without the surrounding request context. The request ID is the
key that makes the other two worth having, so do them as one change.

**Shape of the work.** An HTTP middleware that generates an ID (or echoes an inbound
`X-Request-ID`), stashes it on `request.state`, returns it as a response header, and includes it in
error bodies. Configure logging once at startup to emit machine-parseable records with that ID on
every line. Initialize Sentry guarded on `SENTRY_DSN` being set, so local and test runs are
unaffected, and attach the same ID as a tag. None of this requires the envelope from R9.

**Concepts:** ASGI middleware; `request.state`; `logging.config.dictConfig` and structured or JSON log
formatting; log correlation; Sentry's FastAPI integration and its no-op behavior without a DSN.

---

## R8. Split the docs and generate the frontend types

**Satisfies:** §6 (docs), §7 (frontend types).

**Status: ✅ Mostly done (R8).** `docs/api/conventions.md` exists and is the
cross-repo contract — §2.3, R15 and R16 all cite it as the published record of the
pagination rule and of the reads that sit outside it. `openapi.json` is CI-verified
fresh: the workflow regenerates it and fails on a diff. The frontend's types are
generated from it rather than hand-maintained — `npm run gen:api` is a real script
in `package.json` (`node scripts/gen-api.mjs`) and writes `src/api/schema.d.ts`,
which `types.ts` re-exports. **One piece remains:** `SPEC.md` is still one
file, and splitting it into audience-scoped docs is the outstanding work. The byte count
that stood here is gone rather than pinned: the argument is that the file is too large to
navigate, and no number was carrying it.

**What was missing.** There was no `docs/` directory, and `SPEC.md` was one large file. The
organizing principle to apply is one doc, one audience, one question. The single highest-value split
was **`docs/api/conventions.md`**, because that is the cross-repo contract: it is where R2, R4 and R5
get written down. At the time that contract existed only as the union of what the code happened to
do, which is why the frontend guessed wrong twice.

**The generated half was an asset not being used.** `openapi.json` was checked in and `make openapi`
regenerated it, in sync with the app by diligence rather than by a check. Two things followed, and
both have since landed:

- Add a CI step that regenerates it and fails if the committed copy is stale. Diligence is exactly
  what CI is for, and a checked-in generated file that can drift is worse than no file, because it
  is trusted. **Landed** as the `Check openapi.json is fresh` step in `.github/workflows/ci.yml`.
- The frontend hand-maintained `src/api/types.ts` to mirror the backend schema, while its own header
  comment said it should be generated with `openapi-typescript`. Its `Makefile` carried a `gen-api`
  target marked "planned" that called `npm run gen:api`, which was not a script in `package.json`,
  so the target failed if run. One dependency and one script deleted an entire class of drift.
  **Landed**: `gen:api` is a script now and `src/api/schema.d.ts` is generated from `openapi.json`.

Also, and independent of the split: the stat-name convention in `CLAUDE.md` lists `armor_save` and
`app/core/db/models.py` agrees, so the `save` drift this entry recorded in the instructions file is
gone. The `CLAUDE.md:36` citation never belonged to it either — that line sits in the typed-error
convention, not the stat-name one, which is the kind of citation this document now re-derives rather
than copies. `SPEC.md` was correct throughout. Small, but it is exactly what the
docs-change-with-code rule exists to prevent.

---

## R9. Decide on the response envelope

**Satisfies:** §2.6. **The deliverable here is a written decision, not necessarily code.**

**Status: ✅ Done (R9) — decided, option C: no full envelope.** The decision is
written down in `ARCHITECTURE.md` §2.6, which links back here: success responses
stay bare, because §2.2 (error shape and code), §2.3 (pagination total in the
body) and §2.4 (request ID) already deliver the three things the wrapper was
wanted for. The one functional gap was closed directly instead — every error body
carries a uniform `errors[]` array, so a multi-field 422 reports all of its fields
in one response, with the top-level `detail`/`code`/`field` mirroring `errors[0]`.
**[R16](#r16-wrap-every-response-in-an-envelope) supersedes this decision**: same
facts, a changed judgement on its last clause, so read R16 before acting on this
entry.

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

**Status: ✅ Done (R10).** `README.md` is a real front door: 132 lines at `ebc0780`, carrying
what the project is and the stack it runs on, a Quick start, a Common commands
table, a Layout block, and a Documentation index linking onward to
`ARCHITECTURE.md`, `ROADMAP.md`, `SPEC.md`, `MVP.md` and the deploy docs.

**What was missing.** `README.md` was two lines. Someone landing on the repo learned nothing about
what this is or how to run it, and it is the one doc every new reader opens. The content largely
existed already; it needed a front door linking to `ARCHITECTURE.md`, `ROADMAP.md`, `SPEC.md`,
`MVP.md`, and `DEPLOY.md`. Carried over from `CODE-REVIEW.md`.

---

## R11. Enforce the layering rule

**Satisfies:** §1.

**Status: ✅ Done (R11).** `.importlinter` declares two contracts and CI runs
`lint-imports` as a step of its own beside the suite: a `layers` contract
(`app.api` > `app.core.services` > `app.core.db`, so a lower layer may not import
a higher one) and a `forbidden` contract (`app.core` may not import `fastapi` or
`starlette`, directly or transitively). The violation is split:
`app/core/security.py` kept the domain half — hashing, token encode and decode —
and `app/api/deps.py` took the transport half (`get_current_user` /
`get_current_admin`, turning a decode failure into a 401 with a
`WWW-Authenticate` header), so `app.core` raises no `HTTPException` and imports no
web framework at all. `ARCHITECTURE.md` §1 reads **✅ Holds (R11)** accordingly.

**What was missing.** The layering rule was followed almost everywhere and enforced by nothing. There
was no import-contract check, no architecture test, and CI ran only `pytest`. A service could import
from `app/api/` and the whole suite would stay green. §1 was marked Partial for this reason alone.

**There was also one real violation to clean up first.** `app/core/security.py` imported FastAPI and
raised `HTTPException`, and `app/core/services/service_auth.py` imported it, so the service layer
transitively depended on an HTTP-coupled module. A test asserting `HTTPException` was raised pinned
it in place.

Auth is the usual place this leaks, because token handling sits between transport and domain. The
split to make: token encode and decode and password hashing are domain concerns and belong in the
service layer or a neutral module; `get_current_user`, which turns a decode failure into a 401 with a
`WWW-Authenticate` header, is an API dependency and belongs in `app/api/`. Once split, the service
layer imports only the domain half.

**Shape of the work was.** Fix the violation, then add a check so it cannot come back. Two options
were worth comparing: **import-linter**, which expresses "layer A must not import layer B" as a
config file and runs in CI, or a **ruff** `flake8-tidy-imports` banned-api rule, which is lighter
but less precise. Import-linter won, and the check sits beside the CI lint steps added in R1.

**Concepts:** import-linter layered contracts; why an architecture rule that only exists in a
document is indistinguishable from no rule.

---

## R12. Make quantity mutations retry-safe, and even out resource semantics

**Satisfies:** §2.5.

**Status: ✅ Done (2026-08).** (1) `POST` add-unit is now create-only on both
inventory and army — a repeat is a `409 CONFLICT`, not a silent increment;
quantity changes go through the idempotent `PATCH`. Frontend `CatalogView`
disables the add control ("Added") for units already in the target. (2) The
taxonomy moved out of the `/factions` id namespace to `GET /taxonomy` (no future
collision with `GET /factions/{id}`; frontend follows). (3) Catalog CRUD policy
settled and `GET /subfactions` added so subfactions are enumerable — see the
table below. (4) Subfaction non-nesting left as-is, deferred to the next
catalog-routes pass by its own guidance.

The routing is in good shape overall: plural collection nouns, correct verb and status-code
semantics, nesting that reflects real hierarchy, `/me/*` scoping that keeps user ids out of paths,
and membership rows addressed by their natural key rather than an exposed surrogate. Four things
break the pattern, and the first is the only one that can corrupt data.

### The load-bearing one: `POST` that accumulates is not retry-safe

Both quantity endpoints incremented rather than set. *This is the pre-R12 state: both
`add_unit` methods are create-only now and raise `ConflictError`, and `amount += ` appears
nowhere in `app/`.*

- ~~`POST /me/inventory` → `service_inventory.py`, `entry.amount += amount`~~
- ~~`POST /me/armies/{army_id}/units` → `service_army.py`, `entry.amount += amount`~~

Both were honest about it -- each carried an "upsert: increment" comment, now gone with the code
-- and both correctly returned 201 on insert and 200 on update, which is the right HTTP for an
upsert. The problem was never the status codes, it was the accumulation.

**A network timeout is indistinguishable from a slow success.** A client that times out and retries
has no way to know whether the first request landed, so a retried "add 1" becomes "add 2". No
server-side care prevents this, because the server never learns the two requests were meant to be
one. The failure is silent: the user simply owns more models than they added.

You already have the safe shape one route away: `PATCH /me/inventory/{unit_id}` and
`PATCH /me/armies/{id}/units/{unit_id}` both set an **absolute** amount, which is idempotent and can
be retried freely. So the API offers two ways to change a quantity, one safe and one not, and the
unsafe one is the one a client reaches for first when adding.

**The decision to make:** does the incrementing endpoint need to exist at all? A client that knows the
current amount can always `PATCH` the target value, and the read it needs is one it usually already
has. If it does need to exist (concurrent edits from two devices, where "add one" is genuinely the
intent and last-write-wins would lose an update), then it needs an **idempotency key**: the client
generates a unique key per logical operation, sends it as a header, and the server records processed
keys and returns the original result for a repeat. That is the standard mechanism and it is worth
reading about even if you decide against it here.

**Concepts:** idempotency in HTTP (RFC 9110, section 9.2.2); idempotency keys as implemented by
payment APIs;
why `PUT` is idempotent and `POST` is not; lost-update versus double-apply as the two failure modes
you are choosing between.

**Resolved (the increment is gone).** Both add-unit `POST`s are create-only: a repeat answers
`409 CONFLICT` rather than silently incrementing, and a quantity change goes through the `PATCH` that
already set an absolute amount. No idempotency key was needed, because nothing accumulates any more.
R13 retires `amount` itself, so the set-versus-delta question closes with it.

### `GET /factions/taxonomy` is a non-resource in a resource's id namespace

`faction_taxonomy` (`app/api/faction.py`) serves a static map built from the
`FACTION_SUBFACTIONS` constant, not from the database. ~~It sits at `/factions/taxonomy`~~ — it
sat there when this was written, and the resolution below moved it. There is still no
`GET /factions/{faction_id}`.

When one is added, `taxonomy` and `{faction_id}` occupy the same path slot. It resolves today only
because declaration order decides, and a `UUID`-typed path parameter would reject the literal string
with a 422. That is a collision waiting on a feature, and it is invisible until then.

Static reference data is not a resource under `/factions`. Options worth weighing: a separate path
outside the id namespace, folding it into the `GET /factions` response since it describes the same
thing, or shipping it as part of a bootstrap or configuration endpoint.

**Resolved (moved out of the id namespace).** The first option was taken: the taxonomy is served at
`GET /taxonomy`, and `app/api/faction.py` records in a comment why it no longer sits under
`/factions`. The collision this section describes cannot happen now, so a `GET /factions/{faction_id}`
can be added freely. R14 reopens a different question about the same read — whether a picker needs
ids rather than names — which is about its contract, not its path.

### CRUD coverage is uneven across sibling catalog resources

The coverage as this entry found it, since changed by the policy recorded below the table:

| Resource | Create | List | Update | Delete |
|---|---|---|---|---|
| Weapons | yes | yes | yes | yes |
| Abilities | yes | yes | yes | yes |
| Subfactions | yes | **no** | **no** | yes |
| Factions | yes | yes | **no** | **no** |

Nothing is wrong route by route. The unevenness is the problem: a client author cannot predict which
verbs exist and has to read the source. Subfactions are the sharpest case, since you can create one
and then have no way to enumerate what exists.

Decide the set deliberately. "These are admin-managed reference data, so create and delete only" is a
perfectly good answer, as long as it is applied to all four and written down in
`docs/api/conventions.md` (R8).

**Resolved (deliberate policy).** Two tiers, split by whether the entity is editable content or
canonical taxonomy:

| Resource | Verbs | Rationale |
|---|---|---|
| Weapons, Abilities, Units | full CRUD | rich, correctable content |
| Subfactions | create · list · delete | thin reference rows; no in-place edit — recreate instead |
| Factions | create · list | canonical `FactionName` enum; not renamed or deleted (units reference them) |

The one gap this closed in code: added `GET /subfactions` (optional `?faction_id=` filter) so created
subfactions are enumerable — the sharpest case above. No `PATCH` on subfactions or factions, and no
`DELETE` on factions, by design. The formal write-down lands in `docs/api/conventions.md` when R8 runs;
this table is the interim record.

### Subfactions do not nest, but weapons do

`POST /subfactions` is top-level with `faction_id` in the body, while `POST /units/{unit_id}/weapons`
nests under its parent. Both styles are defensible; having both in one API is the inconsistency, and
subfaction is the one that reads oddly, since a subfaction cannot exist without its faction.

Lowest priority of the four. Worth folding into whichever pass touches the catalog routes next, not
worth its own change.

**Deferred (by its own guidance).** Left as-is; fold into the next catalog-routes pass (R8 territory)
rather than churning the route now.

## R13. Unit composition, and points that follow model count

**Status: Not started.**

Two changes that belong in one slice, because the second is only defensible once the
first has made clear what a datasheet actually prints.

### Composition is TEXT — the Kill Team answer, copied rather than re-derived

A new table on the 40k side, `UnitComposition` = {`position`, `depth`, `kind`, `text`},
mirroring `KTSelectionRule` column for column: the page's own words in the page's own
order, with the indent kept. Exposed on `Unit_Read`, so the catalog screen and the army
list both get it from the read they already make. Nothing is derived from it and nothing
is enforced.

This is not a fresh design decision. The other half of the app settled it, expensively:
decision #44 deleted three tables (`KTSelectionList`, `KTSelectionOption`,
`KTSelectionRestriction`) and retired eleven decisions, after structured composition
produced a budget summing to 43 selections for a team that fields 7, a keyword cap
matching none of the seven operatives carrying it, and four rows that read identically
while meaning four different things. #28's verdict is the one to keep in view: *"We never
had correct legality, only numbers that looked authoritative, which is worse than none."*
40k composition is the same kind of thing — prose with conditionals — so it gets the same
treatment, and #45's argument for `depth` over nesting applies unchanged.

The scraper already **locates** the block it is discarding:

```python
if el.select_one(".dsUl") or el.select_one(".PriceTag") or el.find("table"):
    return None  # composition / points, not an ability
```

`.dsUl` is the composition list. So this is parsing a block already identified, not
finding a new thing on a page.

### Points tiers are DATA, not enforcement machinery

`parse_points` builds the pair and throws half of it away:

```python
best: tuple[int, int] | None = None  # (model_count, points)
...
return best[1] if best else 0
```

So `Unit.points` is the **minimum-size** price, and `points_total` is
`SUM(army_units.amount * units.points)` — which bills every squad at its cheapest legal
size. A ten-model Intercessor squad is reported at the five-model cost. That is a wrong
number rather than a missing one, which is why this slice is worth its cost.

The objection to answer first, because it looks like reversing #44: that decision
explicitly removed *"no budget, no cost, **no models**, no repeat cap"*. The distinction
is where the number lives. #44's `models` sat on the CATALOG's composition option — how
many models an option fields, an input to deciding legality, and an approximation of
prose. This `models` sits on the PLAYER's army-list row: how many models they chose,
which is a fact about their list, priced by lookup in a table the page prints. #44's
columns failed because composition is prose and a column must approximate it; a price
table is a table, with no interpretation step. Tiers are therefore data, and
`UnitPoints` = {`models`, `points`} per unit is the shape.

### The blocking constraint

```python
__table_args__ = (UniqueConstraint("army_id", "unit_id"), ...)
amount: int
```

The class comment reads "A unit in an army list, and how many are in it", and the
quantity is of whole units -- squads -- not of models.

One row per datasheet per army, so a list holding one five-model squad and one ten-model
squad of the same unit is **unrepresentable** — not merely unpriced. Kill Team hit the
identical problem and answered it in #50: one row per operative, no count, because *"a
count could not say which of them is wounded."* The 40k version is that a count cannot
say which squad is which size, so `ArmyUnit` becomes one row per squad.

Four consequences, all of them real work:

- `PATCH` and `DELETE /me/armies/{army_id}/units/{unit_id}` rekey to `{row_id}`, since
  `unit_id` no longer identifies one row. Kill Team's roster operatives already use that
  shape, so the precedent is there to copy.
- `shortfall` must aggregate by `unit_id` BEFORE subtracting. It currently iterates
  `ArmyUnit` rows and compares each to the owner's whole inventory, which with several
  rows per unit would understate the need on every one of them.
- The points arithmetic exists **twice** — a SQL aggregate in `points_total` and a Python
  loop in `validate` — and the two must stay in agreement.
- The scraper, `seed_datasheets.py`, `Unit_Create`/`Unit_Update` and
  `UnitService._UPDATABLE` all carry `points` as a single `Stat` today.

**This finishes what R12 started.** R12 already answered its own question — the
incrementing add is gone, and both add-unit `POST`s are create-only with an idempotent
`PATCH` for the quantity. What it left standing is `amount` itself, which R13 retires:
with one row per squad the count is the row multiplicity, so `set_amount` has nothing
left to set and `UNIQUE(army_id, unit_id)` goes with it.

### What stays the players'

Report, never refuse — and #57 names this half's `points_limit` / `over_points` as the
house pattern it was following, so this is keeping a standard rather than setting one. A
seven-model squad that matches no printed tier is **saved**; the list may say the size is
not one the page prints, and must not refuse it. No composition line is enforced, and
`validate` gains no issue kind that blocks a write.

**Refusing to refuse means owing a defined report for every case allowed**, and the
seven-model squad is the case this section promises will happen. It is reported
**unpriced**, not rounded up, nearest-matched or billed at the highest tier. The page
prints no price for seven models, so the app does not know one, and inventing one is the
app asserting something the source never said — which is the mistake #28 describes as
"numbers that looked authoritative, which is worse than none".

The consequence is stated rather than hidden: `points_total` stops being a single
sufficient number. It sums what it can price, and `Army_Read` and `Validation_Read` carry
a count of what it could not, so a client shows "1,840 pts + 1 unpriced entry" instead of
a total that quietly omits a squad.

**And the incompleteness is made unreadable rather than advertised.** `points_total`
becomes `int | None`, null whenever any entry is unpriced — `0` still means an empty army.
A sibling count alone would have been ignorable by construction, and that is the trap
worth naming: the field keeps its name and its integer type, so the `openapi.json`
freshness gate sees nothing, the generated client types see nothing, and a client compiles
and renders a total short by whole squads. A semantic change under an unchanged signature
is the one kind of break every gate here is blind to.

Nullable closes it the way #54 and #31 say to — unrepresentable rather than merely
checked. The generated type becomes `number | null`, and TypeScript `strict` (R1) then
refuses to render it without handling the null, so a client that ignores the gap fails to
compile instead of failing to be correct. `points_limit` is already `int | None` in both
models, so the pattern has precedent in the same schema.

The irony is worth recording, because it was nearly shipped: "unpriced" beat "round up"
precisely because inventing a number means asserting something the page never said. A
sibling count would have left the honest answer looking identical on the wire to the
dishonest one.

The warning is **owned by `validate`**, as a fourth issue kind (`off_tier_size`) beside
`over_points`, `wrong_faction` and `wrong_subfaction`. Leaving it to the client is #57's
mistake with a different number: the point of the backend holding the figure is that a
client never has to hold a rule.

### `Unit.points` is computed, not stored

The stored column goes. `UnitPoints` is the only place a price lives, so nothing can
drift; `Unit_Read` keeps a `points` field, computed as the minimum tier, so the client's
catalog view does not break. `Unit_Create`, `Unit_Update` and `UnitService._UPDATABLE`
take tiers rather than a scalar, and the seed and scraper write them.

Keeping the column would store a value derived from the tier table beside it — the
denormalisation this slice exists to remove. Dropping the read field would break a
catalog view over an implementation detail. Computing it does neither.

### The inventory moves to models too

`shortfall` is `max(0, entry.amount - have)`. R13 retires `amount` on an army row in
favour of a model count, so without this the subtraction compares **models with whole
units** — and no compiler sees it, because both are `int`.

So `user_unit.amount` becomes a model count as well, backfilled as
`amount × minimum tier size`, the same assumption the army backfill makes, so the two
stay consistent. This is more truthful anyway: you own models, not squads. A box is ten
Intercessors, and "I own 3 Intercessor squads" was always an approximation of "I own 30
Intercessors".

**This widens the slice into the inventory**, stated here rather than discovered while
building it: `POST /me/inventory` and `PATCH /me/inventory/{unit_id}` change units, and so
does `UserUnit_Read`. That is a second user-facing surface in a slice that is already a
migration with a backfill. The alternative is leaving `shortfall` subtracting
incompatible units, which is the one option with no defence.

### The rows that already exist

An existing `army_units` row with `amount = 3` becomes three rows, and the migration has
to choose a model count for each. The only size the catalog knows is the minimum, which
is the same figure today's understated `points_total` is built on — so **the backfill
preserves the price the list already showed** rather than silently re-pricing somebody's
saved army. A note on the list says the size was assumed and can be corrected, which
turns a wrong number into a prompt instead of leaving it wrong or changing it without
asking.

Re-pricing was the alternative and it is worse in the way that matters: a player opens a
list they built last month and the total has moved, with no action of theirs to explain
it. The number was wrong before and after; only one version of being wrong is also a
surprise.

### Cost note

There are 23 cached 40k faction pages in `scripts/data/cache`, so both parsers can be
written and re-run offline against the same HTML -- **for whoever has that directory**.
It is gitignored and absent from a fresh clone, so a second person re-scrapes before
they can start. Cheap for one machine, not cheap for the repository.

## R14. Two catalog reads whose contract does not match what they serve

**Status: Not started.** Both found in the October 2026 review of the MVP document, and
both corrected in the prose there while the behaviour stayed as it was.

### `GET /units` and `GET /units/facets` declare authentication they do not require

`openapi.json` gives both the bearer scheme, so a generated client treats them as
authenticated. At runtime they serve anonymous callers. The cause is the `owned`
dependency: `get_owned_by` declares the query parameter **and** resolves the caller, so
it pulls the OAuth2 scheme into the contract even on a path where a token is optional.

This is the one small finding with a consequence in the other repository, because the
generated types are what the client is built from — a client that believes these need a
token will either send one it does not have or hide a view it could show. The fix is to
split the dependency: one that declares `?owned=`, one that resolves an optional caller,
so the scheme is attached only where it is required.

### `GET /taxonomy` serves names, and a picker needs ids

It returns `dict[str, list[str]]` from the `FACTION_SUBFACTIONS` module constant — the
subfaction names allowed under each faction, with no database read and no ids. The shape
is right for validating a value and wrong for the thing it is named for: a client
building a dropdown from it cannot then reference the row the user picked.

**The decision this needs is what the endpoint is for.** If it is a picker, it returns
ids and reads the database. If it is the enum of accepted values — which is what the
docstring says, "the values `create_subfaction` will accept, whether or not any have been
created yet" — then it is correct as it stands and the name is what misleads. Those are
different endpoints and the current one is halfway between.

---

## R15. Two unpaginated reads return complete results with no cap

**Status: Not started. Narrower than the first draft of this entry claimed** — see the
correction at the end, which is the useful part.

`docs/api/conventions.md` closes its Pagination section by naming every read that does
not paginate, with a reason for each, and the reasons form a test:

| Category | Reads |
|---|---|
| A computed report about a single resource | `/me/armies/{id}/shortfall`, `/me/armies/{id}/validate` |
| A static map | `/taxonomy` |
| One document rather than a collection | `/kill-team/teams/{id}/composition`, `/kill-team/universal` |

That is a coherent membership test, published in the document a client author reads, and
it says "those all return complete results".

**What is actually missing is a bound on two of them.** "Complete results" is a promise
about correctness, not about size. `/taxonomy` is a constant, and both Kill Team reads
are one printed page — all three are bounded by what they are. The two computed reports
are not: they return a row per army entry, and an army has no size cap. R13 makes that
longer rather than shorter, since a list carrying `amount: 10` on five entries becomes
fifty rows.

So the work here is small and specific: decide whether the two computed reports get a cap
or a page, and move the three categories into §2.3 as well, because the rule's home is
the architecture document and a maintainer reading it finds the twelve-collection claim
with no test beside it.

### The correction, which is the reusable part

The first version of this entry said four reads sat outside the envelope and three had
no recorded reason anywhere. All five are recorded, with reasons and a test, in
`docs/api/conventions.md` — and the entry missed `/validate` entirely, because an object
carrying an array buckets like any detail read.

The error was searching the files the work was happening in — ARCHITECTURE, ROADMAP,
KILLTEAM — rather than the file whose job the question was. `conventions.md` exists to
answer "what shape does this API return" and was the one not consulted. It is the same
mistake as concluding SPEC.md claimed something it did not: an absence asserted from an
incomplete search reads exactly like an absence.

"Nothing documents this" is a claim about every file, and it is only ever as good as the
list of files checked.

---

## R16. Wrap every response in an envelope

**Satisfies:** §2.6, which this supersedes. **Status: Not started.**

Every response becomes `{data, errors, meta}` — `meta` carrying the trace id, and
pagination, sort and filter where they apply. The shape is `attention-api`'s
`ResponseEnvelope[T]`, which is where R9 first met it.

### Three parts of the shape are open, and they decide how large this is

Read from the reference rather than assumed, because the first draft of this entry
assumed and got it wrong: it described `data` as holding the current `Page` object with
`meta.pagination` beside it, which double-counts paging and is not what the code does.

**E1 — does `data` hold the bare array, or keep `Page`?** The reference returns
`ResponseEnvelope[List[ChallengeRead]]`: `data` is the array and paging belongs in
`meta.pagination`, so **`Page[T]` stops existing rather than nesting**. Twelve generated
schemas collapse instead of gaining a level, which is cheaper than nesting — but every
read's envelope then carries paging in a different place from where the client reads it
today. Keeping `Page` inside `data` is the hybrid, and nobody is running it.

**E2 — offset or page-number?** The reference's `Pagination` is
`{size, number, total_elements, total_pages}`. Warhammer's is `{limit, offset}`, chosen
deliberately in R4 with §2.3's reasoning for a mostly-static catalog, with a 200 cap and
an `id` tiebreaker so a page boundary cannot repeat a row. Adopting the envelope does not
require adopting the pagination MODEL, and conflating the two makes this change look
larger than it is: the wrapper is a shape, and page-number paging is a different
decision with its own consequences — including `paging.ts` in the client, which walks
`total` to fetch user-scoped collections whole and has no `total_elements` to walk.

**E3 — are unset `meta` fields absent or null?** The reference sets
`response_model_exclude_unset=True` on every route, so `pagination`, `sort` and `filter`
are omitted when they do not apply rather than serialised as null. Cleaner bodies; the
cost is that a client cannot tell "not applicable" from "omitted", and the generated
types will encode whichever is chosen.

One observation worth carrying: the reference's list route accepts `offset` and `limit`
and **never populates `meta.pagination`**. So "it gives pagination a defined home" is
true of the model and not yet of the code — the home exists and is empty. Adopting the
shape means deciding to fill it, not inheriting a filled one.

### Why, and it is a change of judgement rather than a correction

R9 refused this and **every fact in its reasoning still holds.** The three things the
wrapper buys were already delivered: pagination metadata in the body (R4), multiple
validation errors in a uniform `errors[]` (R2), a trace id in a header and every error
body (R7). §2.6 then said the remainder "would buy only **structural uniformity**" and
judged that not worth the cost.

That clause is what changes. Uniformity across every response is wanted as an end: no read
is an exception, and no future read has to argue for one. Alongside it, `meta` gives
`sort` and `filter` a home before they are needed — filtering exists today with no place
to echo what was applied — and a trace id reaches a client on success rather than only on
failure, which matters for a screen that worked and looked wrong.

### What this is NOT justified by

An earlier draft of R15 claimed five unpaginated reads had no recorded reason, which would
have made non-uniformity a demonstrated cost rather than a preference. **That was wrong.**
`docs/api/conventions.md` records all five with a three-category test: a computed report
about a single resource, a static map, or one document rather than a collection. The
current shape has not failed, and nobody should later cite a failure that did not happen.

### What it breaks

- **Every route and every response model.** Twelve `Page[T]` reads become
  `ResponseEnvelope[Page[T]]`, and every detail read nests one level.
- **The client's generated contract**: `schema.d.ts` (4,830 lines) and 53 zod schemas in
  `schemas.gen.ts`, both at `warhammer_web` `440da79` and both CI-gated against
  `openapi.json`. Regenerated wholesale. The file exports 54 names; the 54th is
  `PAGE_LIMIT_MAX`, a constant rather than a schema.
- **A hard cutover, with no gradual path.** The client's `parsed()` is non-strict, so a
  backend that ADDS a field never breaks a deployed client — but a wrapper changes the
  shape rather than adding to it, so every read throws `ResponseShapeError` until the
  client ships. The two repositories land together or the app is broken in between. R9's
  own condition applies and is narrowing: "do it in one migration and before the API has
  more consumers". There is one consumer, pre-release, on an unmerged branch.
- **Three things already decided.** KILLTEAM #91 settled the event read as an ordinary
  paged collection. The five unpaginated reads stop being exceptions, because there is
  nothing left to be an exception to — which closes most of R15. And `paging.ts` in the
  client walks `total` to fetch user-scoped collections whole, so the walk gains an unwrap
  in the module whose whole purpose is that a partial collection is silently wrong.
- **Two shapes that need deciding, not inheriting.** A `204` either stays bodiless — an
  exception inside the thing adopted for uniformity — or becomes a `200` with
  `{data: null}`, changing the **thirteen** operations that answer 204 and the client's
  `apiDelete`, which expects no body at all. And `errors[]` in a success envelope is dead
  weight beside the `ApiError` the client already throws from a separate schema on
  `!res.ok`; either both error paths stay, or views that branch on `ApiError.code` are
  rewritten.

### The rows that already exist

None. This changes no table and no stored data, which is the one respect in which it is
cheaper than it looks.

### Where it lands in the documents

`ARCHITECTURE.md` §2.6 is marked superseded by this entry and keeps its record. The
published shape lives in `docs/api/conventions.md`, which is the document a client author
reads and therefore the one that makes the change visible to its audience. `SPEC.md` has
no response-shape section and needs one. `MVP.md` names both shapes in its conventions
paragraph and in every row of its API reference.

---

## A slice states what it breaks, and what happens to rows that already exist

Added because K8 did neither and the gap was found in review rather than in building.
K8 named two of the six constraints its migration touches, and one of the two it missed
was another decision (#60) reached through a column the slice was dropping. R13 had the
same hole from the other end: its DDL was right and it never said what happens to an
existing `army_units` row.

So every slice from here states both. **What this breaks**: every constraint,
relationship, read model and service read that names a column or table the slice moves or
drops — found by searching for the name, not by reasoning about the data, because a
column participates in constraints that sit nowhere near its declaration. **The rows that
already exist**: what the backfill writes, and where it has to choose, that choice named
as a decision rather than left to whoever writes the migration.

A schema change is not specified when the DDL is right.

---

# Kill Team

The design lives in [KILLTEAM.md](KILLTEAM.md); this is the build order. Built as a
thin slice: K1–K5 for one or two kill teams first, then K6 widens to the rest. K1–K7
are done; K8 and K9 are not started.
Frontend views (warhammer_web) follow each step.

Kill Team is kept separate from the 40k army list builder: `/kill-team` vs
`/army-list` (see KILLTEAM.md → "Separation from the 40k army list builder").

Models come first: the design has already been checked against a real team
(Raveners), so the tables are known well enough to build, and the migration stays
changeable until `fire-team` merges, since nothing is deployed against it.
*(`fire-team` merged as PR #5 on 2026-10-08. The tables are on `main` and seeded, so
a column now costs a migration with a backfill -- see K8.)*

## K1. Catalog models

**Status: ✅ Done.**

The nine `kt_*` catalog tables from KILLTEAM.md and their migration, with test
factories and model tests. Fifteen `kt_*` tables exist now: those nine, K4's two roster
tables and K5's four game tables. Still treated as a draft: the migration has been
regenerated repeatedly -- most recently for decision #54's owner column, K5's game
tables, #64's roster-name unique and K7's `withdrawn` flag. The count is deliberately not
stated: a regeneration REPLACES the file, so git cannot be asked for it and two audits
disagreed (ten versus twenty-four). `alembic heads` is the answer. It stays regenerable
until `fire-team` merged, which it did as PR #5.

## K2. Data pipeline

**Status: ✅ Done.**

Kill Team scraper and seed, same two-stage shape as the 40k pipeline, filling the K1
tables — `make scrape-kt` (or `scrape-kt-fresh`) then `make seed-kt`. all 48 of the site's
teams parse and seed. Composition is stored as the page's own words — 897
`KTSelectionRule` rows, nothing derived (KILLTEAM.md decision #44) — which is why there
is no longer a warning channel for it: the checks all read a structure that is gone.
Tested against **synthetic** fixtures, so no scraped content is committed.

## K3. Catalog routes

**Status: ✅ Done.** Read-only routes under `/api/v1/kill-team`, over
`KillTeamService`: public read and **no write route at all**, unlike the 40k catalog's
admin write — an admin edit would be silently undone by the next `make seed-kt`
(KILLTEAM.md decision #21). One assertion over the published OpenAPI document proves it
(#49), so a route added later cannot slip a write past it.

Six paths, one `GET` each:

| Path | Serves |
|---|---|
| `GET /kill-team/factions` | the 22 factions, paged |
| `GET /kill-team/teams` | the 48 teams, paged, `?faction_id=` |
| `GET /kill-team/teams/{id}` | one team whole — 16–46 KB, median 24, in nine queries flat |
| `GET /kill-team/teams/{id}/composition` | its printed rules alone, unpaginated |
| `GET /kill-team/operatives` | operatives across teams, paged, `?kill_team_id=` and `?keyword=` |
| `GET /kill-team/universal` | the ploy and equipment list no team owns, unpaginated (#48) |

A read names a parent by id and never copies its name (#46). The detail carries
everything nested (#47), because operatives are reachable only that way — and no schema
carries `created_at`/`updated_at` or a nested row's parent FK. `openapi.json` is checked
in and CI fails if it drifts, so a route is not finished until `make openapi` has run.

The last two answer what the nested form cannot. The keyword filter is
`keywords @> '["LEADER"]'` against a GIN index on Postgres and a `json_each` scan on
SQLite, so the default test tier exercises it too — the index is what decision #26 chose
JSONB for.

**Still deferred:** no reader for a single selection rule. A composition line has no
identity a client would hold onto — it is one bullet of a page, and its id changes
whenever the composition is replaced.

## K4. Roster

**Status: ✅ Done** — except that the seed's withdrawn-row question was deferred here
and is not answered by it; it is K7 now, with a condition instead of a slice name. Kill
team rosters under `/api/v1/me/kill-team/rosters`, mirroring `Army`.

Audit finding 12 is fixed here: the five write operations take `get_owned_roster_shallow`,
which answers "does the caller own this?" without loading the bundle, and only
`GET /{id}` keeps the full read. Measured on a six-operative roster with a furnished
team: a 204 DELETE went 13 → 6 queries, a one-row PATCH 14 → 8, an append 18 → 9, and
the rename 30 → 17 (it had been loading the detail three times). `GET` is unchanged at
13, which is the point. The shape came from K5's games router, which was built with it
from the start. Eight operations: CRUD on a roster, then append, move and remove on the operatives
in it. The catalog half of the API takes no writes at all (#21) and this is the half a
player writes — one assertion over the published document holds that line now that both
are mounted. A roster listing is 287 bytes a row where its detail is 24-42 KB, median 31, because
the listing carries a name, a kill team and a faction and the detail carries #52's
whole bundle.
A row's composite foreign key names its roster through `owner_user_id` as well as
`kill_team_id` (#54), so neither a stranger's roster nor another team's operative is a
state the table can hold — the ownership rule is the schema's, not just the router's.
Equipment is **not** part of a roster — KILLTEAM.md decision #17 puts it on the game,
since the rules pick it per battle. A roster offers **every operative of its kill team**
and nothing narrows that: composition is the page's words shown beside the roster, never
a rule the catalog applies (decisions #28, #44), which is what lets a custom game be
built. So there is no `validate` and nothing to report — if legality checking is ever
wanted it is a feature of its own, decided then, not a column inherited from the
catalog.

## K5. Game tracker

**Status: ✅ Done.** Four tables, a service and fifteen operations under
`/api/v1/me/kill-team/games`. The migration was regenerated again (one of
several times since; `alembic heads` names the current one). Four decisions were settled while building it: **#60** deleting a
roster a game was played from is refused (409), **#61** activation is stored as the
turning point rather than a flag so `advance` clears nothing, **#62** `initiative` is
nullable because a turning point before its roll-off holds nobody, and **#63**
activating and transforming are their own routes rather than fields on a PATCH.

The ownership dependency loads the game **shallow** rather than eager-loading decision
#52's bundle to answer a yes/no. That was audit finding 12, which K5 did not inherit and
which the rosters router has since been fixed to match — see K4.

A latent bug shipped in K4 turned up here: `get_roster` returned a stale collection
after a write in the same session. It never bit through the roster API, which does not
read a detail back after touching a collection — the game router's shape does, on six
routes. Both services now pass `populate_existing=True`.

Games created from a roster: turning points, CP, VP, and each
operative's wounds, order and activation, with undo. Each game copies the whole
datacard it plays with (decision #22), so the screen can show what a unit can do beside
what is true of it now, and a re-scrape cannot change a battle in progress. Also this
battle's equipment, the actions each operative has used, and the operatives that
equipment or a team rule adds or transforms mid-battle (KILLTEAM.md decisions #17–#24).
The game screen doubles as the reference sheet: any operative opens to its full datacard,
and the team's rules and ploys travel with the game, so a battle needs no catalog call.
**Tac Ops do NOT land here** — decision #59 withdraws that. Neither they nor the
archetypes that gate them are scraped (`archetype` appears 0 times in the payload, and
the scraper fetches only the nav and the per-team pages, so the Tac Ops are on a page it
has never visited). A Tac Op a player takes is a per-game pick in `KTGame.choices`,
beside the Accursed Gift, which is #1's line: the tracker records that things exist and
the players apply what they do.

Three things the design left open are settled before the models:

- **#57** — the equipment allowance is REPORTED, not enforced. A game read carries
  `equipment_limit` (4) the way `Army_Read` carries `points_limit`, and the fifth piece
  is accepted. Narrows #17. The per-team override the old deferral waited on does not
  exist in the pages at all.
- **#58** — an event's payload names the fields touched with their `before` and `after`,
  and `undo` writes the `before` back, appends a compensating event and marks the
  original `undone_by`. Field-scoped rather than whole-row: 40 bytes against 1,129 for a
  datacard copy, on the most frequent event in a game.
- **#59** — Tac Ops and archetypes are out of v1, as above.

Two mechanisms here are new to this codebase, which is why K5 is larger than K4: the
append-only event log with `undo` (#8), and `version` for optimistic concurrency with a
stale write answering 409 (#9).

One sizing note. A game's snapshot is the roster detail turned into stored JSON, and a
roster detail measures 24–42 KB (#52), so each game carries roughly that much at rest,
forever. So `GET /games` is lean the way `GET /rosters` is — 287 bytes a row — and the
detail is the only route that carries the bundle.

## K6. Widen to every kill team

**Status: ✅ Done in substance.** All 48 teams parse and seed. The three pages that used
to need a human decision needed it because the catalog was trying to structure their
composition; storing the printed words instead (decision #44) removed the question rather
than answering it — an entry the parser could not name is not a problem when no entry is
named. Gellerpox Infected's equipment-conditional datacards still carry
`availability = in_battle` (#20), which is the one thing in a composition that is still
read structurally.

Gellerpox's second block is read now — every top-level block under the Operatives
heading is, so its condition and the three vermin it grants are stored as printed text
with the condition between the two blocks.

The word-splitting in `weapon_rules` is fixed: text is taken with the SOURCE's own
spacing rather than joining inline elements with a space, which is what split
`Conceal</span></span>ed Position` into "Conceal ed Position". 671 stored strings
changed, none in any way but whitespace, and the distinct rule count fell from 103 to
101 as the artefacts merged with their correct spellings.

Two pairs are still spelled two ways, and neither is ours: `Torrent 0"` / `Torrent 0"*`,
where the asterisk is a footnote marker 46 of the 101 rules carry, and
`Heavy (Dash Only)` / `Heavy (Dash only)`, where the source itself disagrees about the
capital. **Settled by decision #56: both are kept verbatim.** `#30` strips markers from
composition bullets only because the note BODY is stored there as a row, which makes the
marker redundant; a weapon rule's note body is not scraped at all, so the marker is the
only surviving record that a footnote applies. Grouping two chips that differ by a marker
is the frontend's call. Capturing the note bodies is a K7 item below, and keeping the
marker is what leaves it possible.

## K7. Withdrawn catalog rows

**Status: ✅ Done.** The seed is an append-and-rewrite superset: outside
the composition, a row the source REMOVED or RENAMED stays behind and nothing marks it
stale, so a withdrawn ploy keeps being served and a renamed team leaves its old subtree
beside the new one. The seed said so from the start and deferred the answer to K4; K4
shipped without it, which is what this entry exists to stop happening twice.

Decision #55: a `withdrawn` flag on every `kt_*` table the seed upserts, set when the
source drops a row and cleared when it reappears. Catalog reads filter it out; a roster
or game read still resolves it, so a datacard built last month still renders. Uniform
across the tables rather than only the ones a roster points at today, because #25 made
the same call for `position`; `KTSelectionRule` carries over as its exception, since a
composition is replaced as a whole and so already loses what the source dropped.

The sweep is scoped to the PARENTS the payload contains, so a partial scrape cannot
mass-flag a catalog it simply did not mention.

Built ahead of its condition (*before a client consumes the catalog as a picker*), which
had not been reached when K7 ran. It has been reached since: the client's `kill-team-ui`
branch, 86 commits ahead of its own `main` at `warhammer_web` `440da79`, carries
`src/api/killTeam.ts`, `killTeamQueries.ts` and `killTeamRosters.ts` plus six Kill Team
views, so the picker this flag was built for now exists — on an unmerged branch rather
than on `main`.

Three decisions were settled while building it. **Where the filter lives:** in the catalog
service's own queries, not on the relationships, because a roster read reaches the same
rules, ploys and equipment through them and must not filter — so `get_kill_team` assembles
the detail from one query per collection instead of `selectinload`. Still nine queries,
which is what it promised before. **What a roster read shows:** everything it references,
withdrawn or not; the catalog read is the picker and the roster read is "what I have".
**What a write does:** a NEW pick of a withdrawn row is refused with a 404, while rows
already on a roster or in a game are untouched — and `transform_operative` is deliberately
not refused, since a model already on the table changing into something the catalog has
since dropped is the case the flag exists to survive.

Also here, and independent: capturing the footnote bodies a weapon rule's marker points
at (#56), which is a scraper job across 48 pages rather than a schema one.

## K8. Two sides in one game

**Status: Not started.** Decisions #67, #68, #69, #70, #73, #74, #76, #77, #92, #93, #94.

A game today holds one kill team, one owner and one set of numbers. The opponent exists
as `opponent_name` and a single `opponent_victory_points` int typed in by hand (#5), which
was never a second side so much as a placeholder where one would go.

This slice makes the second side real and stops there. **No second user, no invite, no
sync** — one person still operates the app, and that is the point: `side` must not be
derived from a user id, because the common case is an opponent with no account whose side
is filled in at the table. Build that case first and the shared-session slice becomes an
authorization change on a model that already fits. Build it second and the obvious
shortcut — key the operatives off `user_id` — makes the common case unrepresentable.

The migration is the slice. `kt_game_sides` arrives carrying what moves off `kt_games`:
`kill_team_id`, `command_points`, `victory_points`, `rules`, `ploys`, `ploys_used`,
`choices` and a display name. `kt_game_operatives` and `kt_game_equipment` gain `side`,
and the ownership leg widens from three columns to four so it names the SIDE rather than
the game (#67) — **on `kt_game_operatives`, which is the only one of the two that has such
a leg.** `kt_game_equipment` reaches its game through a single `game_id` key and never had
a composite one, because the universal equipment list belongs to no kill team and there is
no `(kill_team_id, equipment_id)` pair to aim at. That argument is about the EQUIPMENT
reference though, and says nothing against a leg to the SIDE, which involves no kill team
— so equipment GAINS `(game_id, side)` pointing at `kt_game_sides`, the same shape #94
gives the event log. Found by writing the models. The widening is also why this cannot be a widened where-clause: `kill_team_id` sits
inside the current triple and `kt_games` holds exactly one, so a second team's operative
fails the insert rather than slipping past a missing check.

`kt_game_events` gains `side` with it (#94), and that is part of the move rather than an
extra. Shifting `command_points`, `victory_points`, `ploys_used` and `choices` onto the side
BREAKS `undo` for all four at the moment the columns move. `_write` adds `target_id` to the
payload only when a row is named (`_write` in `service_killteam_game.py`), so a game-level
write omits it and `_event_target` returns the game — correct while those columns are on
`kt_games` and wrong the instant they are not. With no `target_id`, `_undo_fields` calls
`setattr` for a column the game no longer has and raises a bare `ValueError`, which is not a
`CodedError` and reaches the player as a 500. With the side row's id, `_event_target` probes
two models, finds neither, and answers 404 for a row that exists. Deferring the column to K9
would leave that window open against a shipped schema.

**Two siblings share `_event_target`'s assumption.** `_undo_created` and
`_undo_deleted` both iterate the same `(KTGameOperative, KTGameEquipment)` pair.
Neither breaks under #94, because no operation creates or deletes a side row yet — but
#83's vacate is the candidate that would, and K9 MUST check them rather than inherit the
assumption.

Three smaller things land here because both halves of the slice need them.
`kt_game_events` gains `actor_user_id` AND `actor_display_name` (#70), both nullable, since
an event log that cannot say
who wrote a row makes undo a button with an invisible target — needed whichever undo
semantics K9 settles on, so it is not deferred with them. `KTGameEvent_Read` carries the
actor's display name beside it, because no user route exists to resolve a UUID — and it is
STORED rather than joined, so a later rename leaves old events alone (#70).

And `kt_game_sides` carries `occupant_user_id` (#74), nullable, **written here and
deliberately unread until K9**. A nullable column on a table this migration creates is
free; adding it in K9 is a migration with a backfill against a schema that is by then
shipped and seeded, which is the cost this slice's last paragraph warns about. The side
row is created once that side's kill team is known rather than at game creation, so every
row that has children carries a `kill_team_id` — a composite leg under MATCH SIMPLE skips
its whole check when any column is NULL, which is the hazard `fk_kt_game_roster`'s comment
already records. The COLUMN is still nullable (#77), and that is not a contradiction: the
only rows allowed to hold NULL are #77's backfilled opponents, which have no operatives
and no equipment, so no composite leg exists on them to skip. The invariant is "a row with
children has a team", not "the column is NOT NULL". And `use_ploy` becomes side-scoped
(`POST .../sides/{side}/ploys`), because #65's whole argument is that spending a ploy is
one operation against one CP pool, and there are now two.

### What this breaks

The earlier version of this entry named two constraints. There are **seven**, and one of
them is another decision. **This count has now been wrong three times** — two when
written, four after `UNIQUE(game_id, side)` was added at `f5c8d11` without it moving, six
until writing the model found the parent unique below. Count the bullets rather than
trusting the number; it has grown every time someone looked at the migration properly:

- `uq_kt_game_owner_team_id` is the provably-unique parent every composite leg into
  `kt_games` targets. The child leg moves to the side, so the target moves with it — a
  new unique on `kt_game_sides(owner_user_id, game_id, side, kill_team_id)`.
- **`kt_games` needs a second unique, `(owner_user_id, id)`, and this entry did not say so.**
  The side's OWN leg into the game is `(owner_user_id, game_id)`, and Postgres requires a
  declared unique covering exactly those target columns — a subset of
  `uq_kt_game_owner_team_id` will not serve. Nor could the side reuse that unique: a side
  carries its own `kill_team_id`, which for the opponent is a different team from the
  game's and is NULL for one of #77's backfilled rows, so a leg through that column would
  constrain the wrong relationship. It is the same redundant-looking unique `kt_rosters`
  and `kt_games` already carry, for the reason `MVP.md` states — to give a composite key a
  provably unique parent to point at. Found by writing the model, which is where a missing
  FK target surfaces rather than in review.
- **`UNIQUE(game_id, side)` as well, and it is not implied by the one above.** #77 makes
  `kill_team_id` nullable so historical games can be backfilled, and `KILLTEAM.md` already
  records what that costs: Postgres treats two NULLs as distinct, so a four-column unique
  containing a nullable column refuses nothing when that column is NULL. Two `opponent`
  rows with no team would satisfy it, and `KTGame_Read.opponent` would become whichever
  row the query returned first — a query with an ordering rule, which is the shape #74
  rejected a participants table to avoid. The risk is not theoretical now that a side has
  two creation paths: #92's `PUT` and #86's join.
- `UNIQUE(game_id, equipment_id)` becomes `UNIQUE(game_id, side, equipment_id)`, or the
  two sides cannot take the same piece.
- **`ck_kt_game_opponent_vp` goes with its column.** `opponent_victory_points` is dropped,
  so its `>= 0` CHECK is dropped with it and is **not replaced** — #77 backfills the number
  as a value in the opponent side's `victory_points` document, where
  `_validate_victory_points` guards it in the service instead. That loses no guarantee,
  because the player's `victory_points` has never had a DDL check and a CHECK cannot express
  the rule over keys nobody declared. It is still a move from the DDL to the service, which
  #77 records rather than leaving to be noticed when the CHECK fails to reappear.
- `ck_kt_game_operative_current_wounds` still couples `current_wounds` to the snapshot's
  `wounds`, so a transform to a lower-wound card stays the service's problem.
- **`fk_kt_game_roster` is decision #60.** Its comment says so: no `ondelete`,
  deliberately, because "NO ACTION means deleting a roster a game was played from is
  REFUSED". That is the 409 protecting a played-from roster, and it is tested. It is
  built on `kt_games.kill_team_id`, which is why #76 keeps that column rather than
  moving it: dropping it would not fail loudly, it would start letting roster deletions
  succeed and strand finished battle records.

Two more things read `kt_games.kill_team_id` and would break with it: `KTGame.kill_team`,
which feeds `kill_team_name` and `faction_name` in both read models, and `_new_operative`.

`add_operative` is a third, and it is the one with a behaviour change rather than a
reference to repoint. It refuses `source == "roster"` and refuses an operative whose
`kill_team_id` differs from the game's (both guards in `add_operative`, `service_killteam_game.py`). Both
guards are right while a game holds one team and wrong the moment it holds two: a host
filling in the opponent's models by hand hits each of them. #93 adds `'opponent'` to the
`source` vocabulary and moves the team comparison onto the SIDE. The operative MUST still
resolve to a catalog row, so an opponent's faction MUST be scraped before it can be
played against.

### What the reads become

Measured against `openapi.json`, not transcribed. The earlier draft of this entry said
nothing about the read models, which is where a client meets the change.

**`KTGame_Read`** — 24 fields today, 15 of them unchanged: the table below moves six, removes two and relocates one.

| | |
|---|---|
| Moves to its side (6) | `command_points`, `victory_points`, `rules`, `ploys`, `ploys_used`, `choices` |
| Removed (2) | `opponent_name`, `opponent_victory_points` — backfilled onto the opponent side (#77) |
| Added (1) | `player` and `opponent` objects, named rather than a `sides` array, because #68 already fixed that vocabulary and an array makes every client write a lookup the schema could name |
| Relocated | `equipment_limit` moves inside each side. It is the constant `EQUIPMENT_LIMIT = 4`, so both carry 4 — but #57's argument is that the number lives in the backend precisely so a client does not hold a rule, and a client reading one number and applying it twice is one step from hard-coding it |
| Unchanged by #76 | `kill_team_id`, `kill_team_name`, `faction_name`, `roster_id` — these would all have gone if the column had moved |

**`KTGame_ListRead`** — loses `opponent_name`, `opponent_victory_points` and
`victory_points`. Names **both** teams and gains `your_side` (#75).

**`KTGameOperative_Read`** and **`KTGameEquipment_Read`** — each gains `side`.

**`KTGameEvent_Read`** — gains `actor_user_id` and the stored `actor_display_name`, or #70 means
nothing, and `side` (#94), or a client polling the log cannot tell which half of the game
an event changed.

**`PATCH /me/kill-team/games/{game_id}`** narrows rather than disappears. `_UPDATABLE`
holds nine fields; the **four settable fields** `MVP.md` names move to
`PATCH .../sides/{side}`, leaving `status`, `initiative` and `markers` on the game. Two
more of the nine — `opponent_name` and `opponent_victory_points` — are REMOVED rather than
moved, which is why this is four and not six. The count is `MVP.md`'s to state; this cites
it, because the copy that was restated here is the one that rotted. The router's docstring lists four of the nine and is wrong
today, independently of this slice.

**This is a breaking change to a contract the other repo consumes**, and it lands as a
lockstep change rather than an `/api/v2`. The client is pre-release on an unmerged
branch, and a second version for a product with one consumer is ceremony. Recorded with
that reasoning so the next breaking change is not waved through on this precedent.

**It also ships a migration together with the code that depends on it, which
`DEPLOY-GCP.md` says never to do.** That rule is stated without exception: during a rolling
deploy both revisions serve against one database, so the pattern is expand, migrate, contract
across three deploys (`DEPLOY-GCP.md:229-241`). The refused shape is one DEPLOY, not one
revision: K8 moves columns off `kt_games`, reshapes its read models and ships the code that
reads them, all together. Splitting the schema work across revisions does not change that —
expand and contract in two files inside one deploy is still one deploy — so the exemption is
needed whatever the file count. What a split buys is that a three-deploy sequence becomes
POSSIBLE later, not that this slice becomes one.

The exemption is that there is no rolling deploy. Nothing is deployed at all:
`.github/workflows/` holds `ci.yml` and no deploy step, no document records a service URL,
and `DEPLOY-GCP.md` describes a target rather than a running system. The rule's premise is
two revisions against one database, and with no revisions there is no premise, so the rule
does not reach this slice.

This is written down because the rule as stated is absolute and the next reader cannot
derive the exception. It expires at the first deploy, which is `DEPLOY-GCP.md` Stage 2:
two revisions overlap even on a default Cloud Run deploy, so the premise arrives with the
first one. The document files the practice under Stage 3, which is where the traffic-shifting
tools land and not where the hazard starts. K8 MUST land before that deploy, and a later
slice that reshapes a deployed schema gets no such licence.

The older version of this argument is NOT the reason: K1 says the migration "stays
changeable until `fire-team` merges, since nothing is deployed against it", and `fire-team`
merged on 2026-10-08, so that condition has lapsed. The live reason is the absent pipeline,
not the open branch.

**`downgrade()` is lossy by construction, not by oversight.** #77 backfills `opponent_name`
and `opponent_victory_points` onto an opponent side row, and two side rows cannot go back
into one game row. The reverse MUST say so in its docstring rather than appear to reverse.
A genuinely reversible path would mean keeping the dropped columns, which is the contract
step K8 is performing.

### The rows that already exist

Every existing game gets a player side row carrying its CP, VP, rules, ploys, ploys_used
and choices. An opponent side row is backfilled **only** where `opponent_name` or
`opponent_victory_points` holds something, because that is hand-entered data about a game
somebody played and discarding it is not a migration detail.

That forces `kt_game_sides.kill_team_id` to be nullable (#77): a historical game never
recorded the opponent's team. It is safe for a reason rather than by luck — a backfilled
opponent side has no operatives and no equipment, so there are no children for a
half-NULL composite leg to stop protecting.

Unlike K1–K7 this is the first Kill Team slice whose migration is **stacked rather than
redrafted** — the Kill Team tables are on `main` and seeded, so #54's and #64's "free
because the migration was still a draft" no longer applies. A column here costs a
migration with a backfill.

## K9. A second user occupies a side

**Status: Not started.** Decisions #71, #72, #74, #75, #78, #79, #80, #81, #82, #83,
and #70's undo contract.

The authorization change is **two** predicates, not one function. All thirteen nested game
operations reach the game through `get_owned_game`, which does a primary-key fetch and one
Python equality test, and `get_game` — the detail read — has no owner clause of its own.
Widening that test to "owner or participant" covers those thirteen — but **not
uniformly**, which the earlier draft of this entry treated as the slice's cheapness.
Three operations stay host-only behind a second loader (#82): deleting a game, which
cascades the event log with no undelete, minting an invite, which revokes the
outstanding code because #71 keeps one slot, and reading that code back, which discloses
a live credential to whoever asks (#95). #73's "either side may finish" rests on
#66 making a finish reversible, and those two are not. A participant refused one gets
403 rather than the house 404, because #31 hides existence from a stranger and a
participant already has the game on screen (#82). A guest's way out is vacating their
side rather than deleting the game (#83). But `GET /me/kill-team/games`
filters `owner_user_id` inside `list_games` and never calls the dependency (#75), so a
game a user occupies but did not create would be absent from the list of their games.

The earlier draft of this entry priced the whole change as one function. That was wrong in
both directions: the dependency **reads nothing** that identifies a participant until #74's
column exists, and it does not reach every path that needs widening. The column is K8's
work; what lands here is the predicate, the listing, and the invite flow.

The invite code is a capability and is treated as one (#71): generated with `secrets`,
redeemed in a POST body so it never reaches an access log, one slot so regeneration
revokes, cleared on use so a screenshot cannot replay it. A host reads their own live
code back from the path that minted it (#95), because one slot means a re-mint revokes
the code the guest is typing. **Prerequisite, stated rather than assumed:** there is no
rate limiting anywhere in this service, so the code's own entropy is the only thing
standing between it and a brute force. That entropy is **40 bits** — 8 characters from a
32-character single-case alphabet — which needs about 611 million guesses a second for an
even chance inside the 15-minute window.

Convergence is polling, not a socket (#72). The event log is already the game's source of
truth and `sequence` is already monotonic per game, so
`GET .../games/{game_id}/events` is an ordinary paged read over a mechanism that exists
— no cursor envelope, because the sequence is gapless so `offset` and "since" are the same
number (#91, and ARCHITECTURE §2.3). The reasoning is in #72 and the short version is that the reconnect path is
mandatory — Cloud Run caps a request at 60 minutes and a game runs longer — and the
reconnect path is this endpoint, so a socket would be an addition to it rather than an
alternative.

The real work is not the transport. It is that **409 stops being an edge case**, and the
earlier version of this entry assumed a safety net that is not strung. #9's reasoning is
explicitly about one user in two tabs. Two people writing the same game makes a stale
`version` ordinary — and the measured coverage is two of the twelve nested writes, with
the field optional on both, four answering 422 to an extra key and six declaring no body
at all so a sent `version` is discarded with a 200. #79 moves it to a header on all
twelve. #78 settles what the client does with the refusal, which is re-decide rather than
retry, because a blind retry discards exactly the conflict the check detected. Note the coupling this creates, because it is
easy to break: `KTGameEvent.sequence` is assigned in Python as `max(sequence) + 1` and its
`UNIQUE(game_id, sequence)` is the only guard — two concurrent writers cannot collide on
it only because both must also `UPDATE kt_games ... WHERE version = N` in the same
transaction, so the loser rolls back first. A side-scoped write that bumped nothing on
`kt_games` would take no lock and could duplicate a sequence. "Every mutation goes through
`_write`" is a correctness requirement here, not a convention.

Frontend consequence: this lands on KF4, whose 409 handling was planned as defensive.
It becomes the slice's core loop.

## R17. Publish the CHECK'd vocabularies as enums

**Satisfies:** §2.2 (the contract). **Status: Not started.** Raised by the frontend
(`warhammer_web` ROADMAP F4), because the cost lands there and the fix is here.

**Measured from `SQLModel.metadata` and `openapi.json` at `da8141c`:** there are **11**
vocabulary CHECK constraints over **8** columns — `availability`, `category`, `initiative`,
`kind`, `order`, `phase`, `source` and `status`. Those columns publish **23** properties
across **16** schemas, and **zero** carry an `enum`.

**The first draft of this entry selected its fields by name, and the name is not the
authority.** It claimed 22 fields across eight schemas; those eight hold **15** of the 23,
and the list was wrong in both directions. `availability` was absent —
`ck_kt_operative_availability` (`models_killteam.py`) allows `'roster'` and
`'in_battle'`, and the column publishes bare on `KTOperative_Read` and
`KTOperative_ListRead`. Implement R17 from a hand-written list and that column stays exactly
as it is, which is the one outcome this entry exists to prevent. `ValidationIssue_Read.kind`
(`app/api/army.py`) is the same error reversed: it shares a name with a constrained
column, sits on a schema with no table, and so has no vocabulary to publish. The "Watch for"
line below already said the constraint is the authority. The field list was the one place
that did not follow it.

**What that costs the client.** A CHECK constraint lives in the DDL and reaches no
consumer: `schema.d.ts` types the field `string`, `schemas.gen.ts` validates it as
`z.string()`, and so nothing — not `tsc`, not zod, not a typed fixture — can reject a
literal that is merely wrong. The client has had to read `ck_kt_game_operative_status`,
`ck_kt_ploy_kind` and `ck_kt_game_initiative` out of Postgres by hand and hand-mirror them
into `src/lib/killTeamGame.ts` as `as const` arrays, and nothing checks those mirrors stay
right.

It is not hypothetical. Four wrong literals shipped on the client's Kill Team branch and
type-checked on the way: `'in progress'` for `in_progress`, `'on board'` for `on_board`,
`'M'` for `melee`, and `'strategic'` for `strategy`. Each was wrong in the fixture AND in
the assertion, so they agreed with each other.

**The change.** Type these columns' Pydantic fields as `Literal[...]` (or a `StrEnum`) so
FastAPI emits an `enum` in the schema. The values already exist, written once in the CHECK
constraints; this publishes them rather than inventing them, and the DDL stays the
enforcement.

**How to measure this.** Walk `SQLModel.metadata`, and take every `CheckConstraint` whose
text is `col IN (...)`. That is the list in one pass, and it cannot pick a field the
constraints do not govern.

Two shapes the walk MUST handle, both live in this schema:

- A nullable guard — `initiative IS NULL OR initiative IN ('player', 'opponent')`.
- A quoted identifier — `"order" IN ('engage', 'conceal')`, because `order` is a SQL keyword.

A pattern that misses them finds 9 constraints rather than 11 and reports no failure, which
is the vacuous pass `tests/test_docs_style.py` guards against elsewhere.

**The test R17 ships with.** The same walk is the acceptance test: for each such constraint,
assert the published property carries an `enum` whose values equal the constraint's. It MUST
NOT land before the annotations do, because it fails 23 times today. It is what says R17 is
finished, not a gate on the way there.

**Out of scope: the event `type`.** `kt_game_events.type` has no vocabulary CHECK.
`ck_kt_game_event_type` is `length(trim(type)) > 0` (`models_killteam.py`), which
constrains emptiness and not values. R17 publishes constraints, and here there is none to
publish. It matters anyway: the service writes **11** distinct types across the `_write` call
sites in `service_killteam_game.py` at `da8141c`, and #72 makes `type` the field a polling
client switches on. Constraining that vocabulary is a decision K9 owes. R17 does not make it.

**Why it is worth more than a test.** The client's `npm run gen:api` turns a published
enum into a union type, so `tsc` and zod both start refusing a wrong literal with no new
test to write or maintain on either side. It retires the largest single category of defect
the two repositories have hit, and the cost is one annotation per column.

**Watch for:** a value the CHECK allows but no row uses, and the reverse. The constraint is
the authority, not the data — `ck_kt_game_operative_status` allows `reserve`, and every
operative in the dev database reads `on_board`.

---

## R18. Publish the error responses

**Satisfies:** §2.2 (the contract). **Status: Not started.** R17's argument one layer up:
R17 publishes the column vocabularies, this publishes the error vocabulary, and both exist
in Python and not in the contract.

**Measured from `openapi.json` at `0b04671`:** across **72** operations the published
statuses are `200` (45), `201` (14), `204` (13) and `422` (68). Zero publish `400`, `401`,
`403`, `404`, `409` or `500`, and the string `CONFLICT` does not appear in the file.

**The prose half is already complete and good.** `docs/api/conventions.md:44-99` gives the
error body, the uniform `errors` array, and a code-to-status table of seven rows. The
machine half declares none of it, so the contract a client can generate from says every
operation either succeeds or returns a Pydantic validation error.

**What that costs the client.** `ERROR_CODES` is hand-written in `src/api/client.ts` and
commented as mirroring `app/core/errors.py`, exactly as the CHECK'd vocabularies were
hand-mirrored before R17. `scripts/gen-api.mjs` emits a `z.enum` only where the published
node carries one, so no generator can produce the array and nothing checks the copy. Both
parse sites use `z.enum(ERROR_CODES).optional().catch(undefined)`, which turns an unknown
code into `undefined` rather than an error — so a vocabulary the client has not been told
about fails open and silently.

**The change.** One shared `Error` component schema with `code` typed as the `ErrorCode`
enum, declared through `responses=` on the routers rather than per operation. `app/main.py`
already registers one handler against `CodedError`, so the statuses a path can answer are
derivable from the errors its service raises.

**Why it has an id at all.** This was raised in the previous review round, inside an item
about cross-repo numbers. The numbers landed and this did not, which left it the one
finding from that round with no entry anywhere — so it was on course to be rediscovered
rather than scheduled. That is the whole argument for the Order table gate in
`tests/test_docs_style.py`, applied to a finding instead of a heading.

**Watch for:** the eighth code. `MVP.md` adds `GONE` for #88's expired invite, and until
this entry lands that code MUST be added to the client's array by hand.

---

## Condition-gated: a deep readiness probe

**Status: Not needed yet. Build it when a load balancer or an orchestrator is put in
front of this service.**

`/health` today is a LIVENESS check and says so: it returns `{"status": "ok"}` without
touching anything, so it answers "is this process serving HTTP?" and nothing more. That
is the right answer to the right question, and it is the only question a platform can
currently ask.

The gap is readiness, which is a different question with the opposite remedy. Liveness
failing means *restart the container*; readiness failing means *stop routing traffic
here, do not restart* — because if the database is unreachable a fresh instance will be
just as unable to serve. Nothing reads a readiness answer until something is deciding
where to route, which is why this is gated rather than scheduled.

What it would miss in the meantime, and the reason this is written down rather than
dropped: the database is **Neon**, which suspends an idle compute instance. So a wrong
DSN, a rotated password the environment did not learn about, or a suspended compute all
produce the same shape —

    GET /health                      -> 200 {"status": "ok"}     the platform is happy
    GET /api/v1/kill-team/factions   -> 500                      every real request fails

— and the platform keeps the instance in rotation, because the check it was given never
looks at the database.

**The condition:** before pointing Render's "Health Check Path" (DEPLOY.md) or a Cloud
Run / Kubernetes probe (DEPLOY-GCP.md) at this service. Both are configured with a path
today, and whichever path they get is the one that decides whether a broken instance
keeps taking traffic.

**What to build** (SPEC.md item L3, sized S): `GET /health/ready` running `SELECT 1`,
200 when it succeeds and **503** when it does not — `Service Unavailable` is the status
that means "try again elsewhere", where a 500 reads as a bug in the handler. `/health`
stays exactly as it is, for liveness. Three details that matter more than the fifteen
lines:

- **Do not leak the reason.** `{"status": "unavailable"}`, never the driver's message —
  a probe endpoint is usually unauthenticated, and the `IntegrityError` handler in
  `app/main.py` already holds that line ("never the raw driver message, which can
  expose column/constraint internals").
- **Fail fast rather than hang.** A hung connection is the failure mode that matters; a
  probe that blocks for thirty seconds is worse than one that answers 503 immediately.
  `pool_pre_ping=True` is already set on the engine, which helps.
- **Do not point liveness at it.** A readiness probe wired as a liveness probe turns a
  database blip into a restart loop, which is the one outcome worse than no probe.

Note what already deep-checks the database and why that is not a substitute:
`docker-compose.yml` probes Postgres with `pg_isready` and the API waits on
`condition: service_healthy`, and CI does the same with `--health-cmd pg_isready`. Both
are startup checks against the SERVER. Only a probe inside the application can say that
*this app's* credentials and connection pool still work.
