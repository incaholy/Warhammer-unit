# Architecture review: Warhammer Unit

A structural review of both repos (`Warhammer-unit` and `warhammer_unit_web`), measured against a
reference FastAPI + React codebase built on the same stack. This is about **shape**: where contracts
live, who owns a transaction, what a test proves. It is not a bug hunt.

Where a difference exists, it gets judged on merits. Several of your choices are better than the
reference's and are called out as such in "Where you are ahead."

**Status: the architecture is sound and the suite is green.** 225 backend tests pass, 144 frontend
tests pass, frontend lint and build are clean. The layering you set up (`api → services → db`,
session-injected services, thin routers, typed service errors) is the right skeleton, and it held up
under everything below. The findings here are about the contract *between* the layers and between
the two repos, not about the layers themselves.

**Companion docs.** [`CODE-REVIEW.md`](CODE-REVIEW.md) covers correctness bugs in both repos, and
[`DEPLOY-GCP.md`](DEPLOY-GCP.md) covers the production topology. This doc deliberately does not
restate them; it cross-references where a structural cause sits underneath a bug they already found.

**How this was verified.** `pytest` (225 passed), `npm test` (144 passed), `npm run lint`,
`npm run build`, plus throwaway probe scripts (SQL query counting, SQLite DDL inspection,
error-shape enumeration) run against the real app and deleted afterwards. Response bodies, DDL, and
counts below are quoted from those runs or from `grep` over the repos. Two figures are borrowed from
the companion reviews and attributed where they appear. Where something is a latent risk rather than
a live defect, it says so explicitly.

---

## What you have already fixed

Worth stating first, because it changes what is left. Since `CODE-REVIEW.md` landed you have closed
all four of its correctness bugs, one of its two security open items, and one of its two process
gaps:

- The explicit-`null` `PATCH` 500 is fixed with a `_NOT_NULLABLE` guard
  (`app/core/services/service_army.py:52`, `:128`), and the reasoning is in the comment rather than
  just the diff.
- The catch-all `ValueError` / `TypeError` handlers are gone, and `app/main.py:74-77` now carries a
  comment explaining *why* they must not come back. That comment is the valuable part: it stops a
  future you from re-adding them.
- Registration validation and the bcrypt 72-byte cap are both handled at the DTO boundary
  (`app/api/auth.py:21-28`), with the deliberate note that `EmailStr` checks syntax and not
  deliverability.
- The last-admin demotion guard is in.
- **Backend CI now exists** (`.github/workflows/ci.yml`). That was called the highest-value item in
  the previous review and you shipped it.

The remaining item from that review is the two-line `README.md`, which is still two lines.

---

## Where you are ahead of the reference

These are not consolation prizes. In each case I looked at both and yours is the better call.

**Lazy, cached engine creation.** `get_engine()` (`app/core/db/connection.py:10-24`) is
`@lru_cache`-decorated and reads `DATABASE_URL` on first use. The reference calls `create_engine` at
import time and raises `RuntimeError` if the variable is missing, which means importing any module
that transitively touches the DB requires a configured database. Yours makes the app importable
without one, which is exactly why your 225 tests run in CI with no database service. This is a small
decision with a large downstream payoff, and you got it right.

**The typed service error hierarchy.** `app/core/services/errors.py` subclasses the builtin each
error replaces (`NotFoundError(ServiceError, LookupError)`), so a partially migrated codebase keeps
behaving correctly during the migration, and one `ServiceError` handler in `app/main.py:48` maps the
whole tree. The reference's *service* errors are thinner: two classes that each hardcode their own
`code` and `status_code`, with no shared base. Yours is the better-factored half.

Note the scope of that credit, though, because it matters for finding 1: the reference's *API* layer
has something you do not, a central `ErrorCode` enum plus a code-to-status lookup table, so a code
and its HTTP status can never disagree. Your service hierarchy is the cleaner foundation; the piece
sitting on top of it is what finding 1a asks you to build.

**Server state on the frontend.** TanStack Query with a single key factory
(`src/api/queries.ts:37-48`), custom hooks per resource, and precise invalidation factored into two
shared helpers so sibling mutations cannot drift. There is zero inline `useQuery` in the app. The
reference frontend has **no query library at all**: hand-rolled `useEffect` + `useState`, no cache,
no dedupe, no invalidation, and failed list loads that only `console.error`. You are not slightly
ahead here, you are ahead by a whole architectural layer.

**One HTTP client.** Exactly one `fetch(` call site in the entire frontend
(`src/api/client.ts:73`), with the token, error parsing, and 204 handling owned in one place. The
401 handler notifies the auth layer through a module-level listener, so the data layer never imports
React. The reference threads its token through every call site by hand as a positional argument. Yours
is better factored.

**Design tokens and CSS Modules.** `src/styles/theme.css` is a real token file, and 18 `.module.css`
files map 1:1 to their components. The reference is a zero-config Tailwind install with utility
strings copy-pasted between four components and no token layer.

**CI at all.** Both repos have a pipeline. The reference has none in either repo.

**A parser test that does not touch the network.** `tests/test_scrape.py` exercises the Wahapedia
parsing logic against a synthetic HTML fixture, so the expensive, flaky, rate-limited part is not in
the test path. Be precise about what that does and does not cover: the file's own docstring says the
fetch layer "hits the live site and is exercised manually," so the external dependency itself is
still untested. Splitting a scraper into "fetch" and "parse" so the parse half is testable is the
right instinct and most people do not bother. The next step, when you want it, is a fake for the
fetch half so the seam is covered too. The reference does this for its LLM calls with a stub class
swapped in by an environment variable, which is a different mechanism from a fixture file and worth
reading as a second example.

---

## 1. The API contract is implicit, and it is already costing the frontend

**Severity: highest.** This is the one finding that spans both repos, and it is the one whose cost
grows fastest the longer it waits.

Your API has no versioning prefix, no stable machine-readable error codes, and no request
correlation ID. More importantly, it emits **two mutually incompatible error shapes**, and the
frontend can only model one of them.

Probed against the running app:

| Request | Status | `detail` type | Body |
|---|---|---|---|
| `GET /units/<unknown uuid>` | 404 | `str` | `{"detail": "unit ... not found"}` |
| `GET /me` (no token) | 401 | `str` | `{"detail": "Not authenticated"}` |
| `POST /auth/register` (bad email) | 422 | **`list`** | `{"detail": [{"type": "value_error", "loc": [...], "msg": "..."}]}` |
| `GET /units/not-a-uuid` | 422 | **`list`** | `{"detail": [{"type": "uuid_parsing", ...}]}` |

Now look at what the frontend does with that. `src/api/client.ts:29` types the body as
`detail?: string`, and `client.ts:90` assigns it straight to the user-facing message. When a 422
arrives `detail` is an array, so the message becomes that array stringified: `[object Object]` for a
single error, `[object Object],[object Object]` for two. Whether the user sees it in a toast or
inline depends on the call path (mutations routed through the `MutationCache` in `main.tsx:17-20`
toast it; `AuthView` catches and renders inline at `:131-133`), but either way the text is garbage.

Worth noting what does **not** catch this, because the obvious guess is wrong. TypeScript is no help
here even with `strict` on: `res.json()` returns `any`, and `client.ts:89` uses an `as ApiErrorBody`
assertion, which is a claim to the compiler rather than a check. No compiler flag validates an
assertion against what actually arrived at runtime. The tests do not catch it either, because they
stub `fetch` and the stub returns the shape the author expected. The lesson generalizes past this
bug: **every `as` on data that crossed the network is an unverified assumption.** The tools that do
catch it are runtime schema validation at the boundary (look up `zod`) or types generated from the
real schema (finding 5), not the type checker.

The deeper point: **the frontend bug is not a frontend bug.** It is the predictable consequence of a
backend that never declared one error shape. The client had to guess, guessed the common case, and
the uncommon case renders as garbage.

There are three separate things bundled here. They have very different costs, so decide on them
separately rather than as one "adopt the envelope" decision.

### 1a. Unify the error shape and add a stable code (do this one)

High value, low cost, and it does not require touching a single successful response.

Two moves. First, register a `RequestValidationError` handler so Pydantic's 422 is reshaped into the
same contract your `ServiceError` handler already produces, instead of leaking FastAPI's default
array. Second, add a `code` field carrying a stable `UPPER_SNAKE_CASE` string (`NOT_FOUND`,
`VALIDATION_FAILED`, `INVALID_BODY`, `NOT_AUTHENTICATED`, `CONFLICT`).

Why the code and not just the message: a `code` is a **contract**, a message is **copy**. Today the
frontend has nothing to branch on, which is exactly why `ArmyView` collapses 404, 500, network
failure, and expired token into the single sentence "Army not found" (frontend `CODE-REVIEW.md`
finding 3). Give it a code and that becomes a `switch`. The rule that makes codes useful: once
shipped, a code's meaning never changes. Adding codes is free, redefining one is a breaking change.

Concepts to look up: `RequestValidationError` and `@app.exception_handler` in the FastAPI docs;
Pydantic v2's `ValidationError.errors()` and the structure of `loc`. Note that your `ServiceError`
subclasses already carry `field`, so half the work is done.

### 1b. Add a version prefix (do this one, and do it with the deploy work)

Every route is currently mounted at the root (`app/main.py:94-99`). There is no way to ship a
breaking change without breaking the deployed frontend at the same instant.

The reference mounts everything under `/api/v1` and deliberately leaves `/health` unversioned,
because load balancers and uptime checks want a stable path that outlives any API version. That
exception is the interesting part of the convention, not a footnote.

You get this nearly free right now: `DEPLOY-GCP.md:155` **already tells you to mount the routers
under a parent prefix** so the Firebase rewrite can forward `/api/**` to Cloud Run. Make it
`/api/v1` rather than `/api` while you are in there. The cost today is one `APIRouter(prefix=...)`
plus the frontend base URL. The cost after the API has real clients is a migration.

### 1c. The full `{data, errors, meta}` envelope (decide deliberately)

The reference wraps every response in `{data, errors, meta}`, with `meta.trace_id` echoed in an
`X-Request-ID` header and `meta.pagination` on its paginated list endpoint.

**Be honest about the cost**: this touches every route, every response model, and every frontend call
site. It is the most expensive item in this document, and "the reference does it" is not a reason.

The three things it actually buys you:

1. **Pagination metadata travels in the body.** See finding 3, this is the strongest argument.
2. **Multiple validation errors in one response.** Your current shape carries one `field`, so a form
   with three bad inputs takes three round-trips to discover. Pydantic already found all three.
3. **A trace ID on every response.** Right now `app/main.py:80` logs a full traceback and returns
   `{"detail": "internal server error"}`. Nothing connects the user's report to that log line. In
   Cloud Run, with logs in one place and a bug report in another, a correlation ID is the difference
   between grepping for one string and reading a haystack.

Item 3 is worth having on its own and does **not** require the envelope: a small HTTP middleware that
generates a `req_<uuid>`, stashes it on `request.state`, echoes it as `X-Request-ID`, and includes it
in the error body gets you most of the value. Concepts: ASGI middleware, `request.state`, and
structured logging with a correlation ID.

My recommendation: do 1a and 1b now, do the trace ID as a standalone middleware, and treat the full
envelope as a deliberate decision to make **before** the API grows more consumers, not as a
follow-on. If you do adopt it, adopt it in one migration rather than per endpoint, because a
half-enveloped API is worse than either end state.

---

## 2. The transaction boundary is in the wrong place

**Severity: high, and it is a latent correctness issue rather than a live bug.** Be precise about
that distinction: I checked, and there is no partial-write bug in the code today.

Your services call `self.session.commit()` directly: **27 call sites** across four service files
(6 in `service_army.py`, 16 in `service_unit.py`, 3 in `service_inventory.py`, 2 in
`service_user.py`). `get_session` (`app/core/db/connection.py:27`) rolls back on exception but never
commits, and its docstring is explicit that this is intentional: "On success the service's own
`commit()` has already persisted the work."

The reference inverts this: `get_session` commits on success and rolls back on exception, and no
service method ever calls `commit()`. The request is the transaction.

**Why this matters, stated carefully.** Today every mutating route calls exactly one committing
service method. I verified this by reading all six routers. So the invariant "one request, one
transaction" currently holds, by coincidence of the feature set rather than by construction.

It breaks the first time you write a route that does two things. Concretely, from your own roadmap:
"add a unit to an army and decrement the owner's inventory" is two service calls. With commits inside
the services, the first one is durable the instant it returns. If the second raises, `get_session`
rolls back a transaction that no longer contains the first write, and you are left with a unit in the
army and inventory that never moved. No error, no log, just quietly wrong data. Nothing in the
current design prevents this, and no test would catch it.

This is also why `session.refresh()` appears after 20 of those 27 commits (the other seven are
deletes, where there is nothing to read back): once you commit, the identity map is expired, so you
pay a round-trip to re-read a row you just wrote.

**The shape of the fix**, which is mostly deletion: move the commit into `get_session`, delete the 27
in-service commits, and where a service needs the database to assign a value before the request ends
(a generated primary key, or `add_unit` needing to know whether the row was new), reach for
`session.flush()` instead. Flush sends the SQL without ending the transaction, which is exactly the
distinction you want to internalize here.

Two things to watch, because this is not purely mechanical:

- **`refresh()` after a flush behaves differently than after a commit.** Work through what each of
  your `refresh()` calls is actually for; several will turn out to be unnecessary.
- **Your test fixture overrides `get_session` with a bare session** (`tests/conftest.py`), so the
  commit behavior under test will no longer match production once the boundary moves. The fixture has
  to move with it, or your tests will pass while asserting the wrong thing.

Concepts to look up: the SQLAlchemy "unit of work" pattern, `Session.flush()` vs `Session.commit()`,
and the "session per request" idiom.

---

## 3. Pagination is one endpoint's feature, not a convention

**Severity: high**, because it has already produced the headline bug in the frontend review.

Exactly one endpoint paginates. `GET /units` takes `limit` / `offset` (`app/api/unit.py:117-118`,
capped `le=200`) and returns the total in an `X-Total-Count` header. Every other list endpoint returns
an unbounded array:

| Endpoint | Pagination |
|---|---|
| `GET /units` | `limit` / `offset`, `le=200`, `X-Total-Count` |
| `GET /me/armies` | none |
| `GET /me/inventory` | none |
| `GET /factions` | none |
| `GET /weapons` | none |
| `GET /abilities` | none |
| `GET /me/armies/{id}/shortfall` | none |
| `GET /me/armies/{id}/validate` (`issues`) | none |

Three distinct problems fall out.

**The client had to invent a workaround, and it broke.** Because there is no endpoint that answers
"how many units per faction," `CatalogView` asks for `limit=1000` to count client-side. The API caps
at 200, so the request 422s and every faction renders `0` (frontend `CODE-REVIEW.md` finding 1). The
structural cause is that the API exposes no aggregate, so the client tried to compute one, and the
client is the wrong place to compute it.

**`X-Total-Count` is a side channel with a trap in it.** A header is invisible to cross-origin
JavaScript unless the server lists it in the CORS `expose_headers` allow-list, and
`app/main.py:38-43` sets `allow_origins`, `allow_methods`, and `allow_headers`, but **not**
`expose_headers`. This is **not a live bug**: `DEPLOY-GCP.md:151` routes the SPA and API through one
origin via a Firebase rewrite, so CORS is not involved. But `ALLOWED_ORIGINS` exists precisely to
support the cross-origin deployment, and in that mode `headers.get('X-Total-Count')`
(`src/api/units.ts:36`) returns `null` and the catalog silently falls back to the row count. Silently
is the problem: no error, no warning, just a wrong number. A total that travels in the response body
is not subject to this rule at all, which is the concrete argument for `meta.pagination` from 1c.

**Unbounded lists are a scaling cliff.** `GET /units` is capped. `GET /weapons` and `GET /abilities`
are not, and they grow with the same catalog, which the frontend review measured at roughly 1,331
units against a seeded database.

**What to do**: pick one pagination convention and apply it to every list endpoint, including the ones
that feel small today.

The reference is a cautionary tale rather than a model here, and it is worth seeing why. Its
`conventions.md` documents page-based pagination (`page` / `size`, 1-indexed, with `total_elements`
and `total_pages` in the body) as a cross-cutting rule, but only one of its three list endpoints
implements it; the other two use `offset` / `limit` and return no pagination metadata at all. So it
has the same inconsistency you do, plus a document asserting otherwise, which is strictly worse than
having no document. **A convention that lives only in prose is not a convention.** The thing that
makes it real is applying it uniformly and then having something check, which is the argument for
writing it into `docs/api/conventions.md` (finding 5) *and* generating client types from
`openapi.json` so a drifting endpoint shows up as a diff.

Page-based versus offset-based is a real choice with real trade-offs, so make it knowingly: look up
**keyset (cursor) pagination** and the
problem it solves, which is that `OFFSET` on a large table gets linearly slower and can skip or repeat
rows when the underlying data changes between requests. For a catalog that is mostly static, offset is
defensible. Decide it once, write it down (see finding 5), and apply it uniformly.

Separately, add the aggregate the catalog actually wants: a per-faction unit count belongs in a
`GROUP BY` on the server, not in a 1,000-row download on the client.

---

## 4. Your tests prove less than you think

**Severity: medium.** CI is green, but green means something narrower than it appears.

`tests/conftest.py` runs the entire suite against **in-memory SQLite** while production is Postgres.
That is a deliberate, well-documented choice with real benefits (4 second runs, no database service
in CI, one clean schema per test), and you already do the important part of mitigating it by turning
on `PRAGMA foreign_keys=ON` so cascades behave.

But SQLite does not enforce the same constraints. I probed this directly:

```
max_length=128 column accepted a 500-char value: stored length = 500
```

`Faction.name` is declared `max_length=128`. SQLite stored 500 characters without complaint. Postgres
would raise `value too long for type character varying(128)`. The generated DDL diverges too:

| Column | SQLite | PostgreSQL |
|---|---|---|
| `id` | `CHAR(32)` | `UUID` |
| `created_at` | `DATETIME` | `TIMESTAMP WITH TIME ZONE` |

So three classes of bug can pass CI and fail in production: **length-constraint violations**,
**timestamp handling** (SQLite hands back a naive `datetime`, Postgres an aware one, so any code that
compares or serializes them can raise "can't compare offset-naive and offset-aware datetimes" only in
production), and **native type behavior** (UUID and JSON column handling differ). Nothing reads
`created_at` outside the models today, so the timestamp one is latent rather than live; it activates
the first time you sort or filter by it, which your roadmap will want.

There is a second gap: **your migrations are never executed by any test.** The suite builds its
schema with `SQLModel.metadata.create_all()`, which reads your models directly and bypasses Alembic
entirely. Nothing verifies that `alembic upgrade head` produces a schema matching `models.py`. Model
and migration drift is the classic way this fails, and it fails at deploy time.

**What to do**, in order of value:

1. Add a small Postgres-backed integration job to CI. You have already written the harness:
   `docker-compose.test.yml` exists and its own comment says it is "the Postgres-parity harness for
   later integration tests." Wire it up as a second CI job. Keep the fast SQLite suite as the default
   for local iteration; you want both, not a replacement.
2. In that job, build the schema by running `alembic upgrade head` rather than `create_all`. That one
   change makes every migration executable-tested.
3. Look up Alembic's **autogenerate diff check**: run autogenerate against the migrated schema and
   fail if it produces a non-empty diff. That is a direct, automated assertion that models and
   migrations agree.

---

## 5. `SPEC.md` is doing too many jobs

**Severity: medium.** `SPEC.md` is 89KB in a single file. `README.md` is two lines.

The reference splits this into `docs/api/conventions.md` (cross-cutting behavior: auth, errors,
pagination, sort, filter), one `docs/api/<resource>.md` per resource, `docs/data-model.md`,
`docs/mvp.md`, and `docs/roadmap.md`. The organizing principle is worth stealing even if you keep
fewer files: **a doc should have one audience and one question it answers.** An 89KB file has neither,
so nobody opens it, and it goes stale precisely because nobody opens it.

The single highest-value split is **`docs/api/conventions.md`**, because that document is the
cross-repo contract. It is where findings 1 and 3 get written down: the error shape, the code enum,
the pagination convention, the versioning rule. Right now that contract exists only as the union of
what the code happens to do, which is exactly why the frontend guessed wrong twice.

You already have a real asset here that is going unused: **`openapi.json` is checked in** and
`make openapi` regenerates it. That is the machine-readable half of the contract, and it is better
than the reference, which has no equivalent. Two things follow:

- Add a CI step that regenerates it and fails if the committed copy is stale. A checked-in generated
  file that can drift is worse than no file, because it is trusted.
- The frontend hand-maintains `src/api/types.ts` to mirror your schema, and its own header comment
  says it should be generated with `openapi-typescript`. The frontend `Makefile:27-28` already has a
  `gen-api` target (marked "planned"), but it calls `npm run gen:api`, **which is not a script in
  `package.json`**, so the target fails if run. You are one dependency and one script away from
  deleting an entire class of drift.

Also worth a small note: `CLAUDE.md` says stat names should match `models.py` and lists `save`, but
the model field is `armor_save` (`app/core/db/models.py:233`). Small, but it is the kind of drift the
doc exists to prevent.

---

## 6. Tooling asymmetry between the two repos

**Severity: low, and these are the cheapest items in the document.**

**The backend has no linter or formatter.** No `ruff.toml`, no `pyproject.toml`, no `setup.cfg`,
nothing. The frontend has ESLint wired into CI. Backend CI runs `pytest` and nothing else. Add
**ruff** (it replaces flake8, isort, and black in one tool, and it is fast enough to be invisible),
then add the lint step to `ci.yml` so it matches what the frontend pipeline already does.

**Frontend `strict` mode is off.** `grep -rn "strict" tsconfig*.json` returns nothing, so
`strictNullChecks` and `noImplicitAny` are both disabled. I checked what turning it on would cost:

```
npx tsc -p tsconfig.app.json --strict --noEmit   →   exits 0, zero errors
```

The code is **already strict-clean**. This is a one-line change with no migration cost that stops the
next `null` dereference from compiling. To be clear about its limits: as finding 1 explains, it would
*not* have caught the `detail` mismatch, because that bug hides behind an `as` assertion on an `any`.
Turn it on because it is free and it holds the line going forward, not because it fixes anything you
currently have.

Two smaller ones: CI has no `concurrency` group, so superseded runs keep consuming runner time on
every push to an open PR. And there is no `.env.example` in the frontend, so the only record of
`VITE_API_BASE_URL` is prose in `SPEC.md` and `MVP.md` plus the declaration in `src/vite-env.d.ts`. A
committed example file is the conventional place people look.

---

## Suggested order of work

Ordered by value per unit of effort, not by severity. The first three are small and independent.

1. **Turn on TypeScript `strict`** in both frontend tsconfigs. One line, already passes, no
   migration. It is first because it is free, not because it is the most important.
2. **Add ruff to the backend** and add lint steps to both CI workflows. Mechanical.
3. **Unify the error shape and add a `code` field** (finding 1a). This is the highest-value backend
   change in the document: it fixes the `[object Object]` defect, and it gives the frontend something
   to branch on so `ArmyView` can stop calling every failure "Army not found."
4. **Move the transaction boundary into `get_session`** (finding 2). Do it before you write the first
   route that mutates two things, because after that you are debugging silent partial writes instead
   of doing a mechanical refactor. Move the `conftest.py` fixture in the same change.
5. **Pick a pagination convention and apply it everywhere** (finding 3), and add the per-faction count
   aggregate the catalog needs. This unblocks the frontend's headline bug at the root rather than by
   lowering a constant from 1000 to 200.
6. **Add the `/api/v1` prefix** (finding 1b), bundled with the Firebase rewrite work in
   `DEPLOY-GCP.md` so you only touch the frontend base URL once.
7. **Add the Postgres integration job** driven by `alembic upgrade head` (finding 4). Keep the SQLite
   suite as the fast default.
8. **Split `SPEC.md`, starting with `docs/api/conventions.md`** (finding 5), and write findings 1 and
   3 into it as you land them. Wire up `openapi-typescript` on the frontend and fix the broken
   `gen-api` target.
9. **Decide on the response envelope** (finding 1c) as an explicit yes or no, and write the decision
   down either way. If yes, do it in one migration.
10. **The two-line `README.md`**, still outstanding from `CODE-REVIEW.md`.

Items 3 through 6 each change a contract the other repo depends on. Land the backend side and the
frontend side of each together, or you will spend the gap debugging a mismatch you introduced on
purpose.
