# Code review — Warhammer Unit (API + web)

A full review of both repos: `Warhammer-unit` (FastAPI backend) and `warhammer_unit_web` (React frontend).

**Status: the suite is green and the architecture is sound.** 210 backend tests pass, 144 frontend tests pass, frontend lint and build are clean. The layering (`api → services → db`, session-injected services, thin routers, a typed error hierarchy) holds up, and the frontend has exactly one module that owns the token and HTTP, which is the right shape.

There are **four real bugs**, all verified by running the code, plus a few gaps. Everything below says how it was checked so you can reproduce it.

**How this was verified:** `pytest` (210 passed), `npm test` (144 passed), `npm run lint`, `npm run build`, plus a throwaway probe test file that exercised specific endpoints and was deleted afterward.

---

## Correctness bugs

### 1. `PATCH` with an explicit `null` returns a 500

`PATCH /me/armies/{id}` with body `{"faction_id": null}` (or `{"name": null}`) crashes:

```
sqlite3.IntegrityError: NOT NULL constraint failed: armies.faction_id
sqlite3.IntegrityError: NOT NULL constraint failed: armies.name
```

Nothing catches `IntegrityError`, so the client gets a 500.

**Trace it yourself.** Three things line up to cause this:

1. `app/api/army.py` → `update_army` calls `payload.model_dump(exclude_unset=True)`.
2. `app/core/services/service_army.py` (around lines 108 to 120) guards with `if fields.get("faction_id") is not None and ...`.
3. The loop right after: `for key, value in fields.items(): setattr(army, key, value)`.

The question to sit with: **what exactly does `exclude_unset` exclude?** It drops fields the client never sent. But a client that explicitly sends `"faction_id": null` *did* set it, so it survives the dump. Then the guard sees `None`, decides there is nothing to validate, and skips. Then the loop writes `None` into a `NOT NULL` column anyway.

So the guard validated the value and the loop wrote it, and those two steps disagreed about what `None` means. Look up `exclude_unset` vs `exclude_none` in the Pydantic v2 docs and decide which one this endpoint actually wants. Then ask a second question: for a `PATCH`, is "field absent" supposed to mean the same thing as "field explicitly null"? Real APIs have to answer that deliberately, and the answer drives the fix.

This affects **any non-nullable updatable column**, not just these two.

### 2. The catch-all `ValueError` / `TypeError` handlers leak internals and hide bugs

`app/main.py` (around lines 61 to 68) registers app-wide handlers mapping `ValueError → 400` and `TypeError → 400`, with `content={"detail": str(exc)}`.

Verified with two probe routes:

| What the server raised internally | What the client received |
|---|---|
| `ValueError("internal detail: connection string postgres://user:hunter2@db")` | `400` with `{"detail":"internal detail: connection string postgres://user:hunter2@db"}` |
| a genuine bug: `return 1 + "oops"` | `400` with `{"detail":"unsupported operand type(s) for +: 'int' and 'str'"}` |

Two separate problems:

- **Information disclosure.** Any internal exception message is handed to the client verbatim. The probe deliberately put a fake connection string in the message to show what that could mean.
- **A monitoring blind spot.** A real server bug is reported as a `400`, which reads as "the client sent something wrong." You will never see it in a 5xx error count, so the bug stays invisible.

Your own comment already calls these "Fallbacks for any un-migrated raises of the plain builtins", so you know they are transitional. The point is they are live right now, and the typed `ServiceError` hierarchy you built has made them removable.

The generalizable question: **which exceptions in your app represent a client mistake, and which represent your mistake?** Should those two ever produce the same status code? A catch-all handler always fires for a much wider set of inputs than the person who wrote it had in mind, which is the actual lesson here.

### 3. Registration accepts empty and malformed input

All three of these returned `201 Created`:

| Request | Result |
|---|---|
| `{"username": "", "email": "e@e.io", "password": ""}` | `201` (empty username **and** empty password) |
| `{"username": "probe2", "email": "not-an-email", "password": "pw"}` | `201` |

`Register_Create` in `app/api/auth.py` declares three bare `str` fields with no constraints. Worth knowing: `email-validator` and `pydantic-extra-types` are **already in `requirements.txt`**, so `EmailStr` is available to you today with no new dependency.

Concept to look up: Pydantic field constraints as the boundary layer (`Field(min_length=...)`, `EmailStr`) and where that responsibility sits relative to service-layer business rules. You already draw that line well elsewhere, this endpoint just predates it.

### 4. Passwords are silently truncated at 72 bytes

bcrypt only ever looks at the first 72 bytes of its input. Verified:

1. Registered with a 100-character password. Got `201`.
2. Logged in with that same 100-character password. Got `200`, as expected.
3. Logged in with `"a" * 72 + "DIFFERENT"`, that is, the same first 72 bytes but a completely different tail. Got **`200`**.

So a user who picks a long passphrase is authenticated on a truncated prefix of it, and any string sharing those first 72 bytes logs them in.

This is documented bcrypt behavior, not a bug in bcrypt. The bug is that nothing validates or caps the length, so the truncation happens silently. Think about what a user reasonably expects when they type a 100-character passphrase, and where the check belongs (the same place as finding 3).

---

## Security posture

**This part is genuinely well done**, and it is worth naming why, because these are the things people usually get wrong.

- **Object-level authorization is correct.** `get_owned_army` in `app/api/army.py` returns **404, not 403**, when the army is not the caller's. That hides whether the id exists at all. Inventory is scoped to `current_user.id` from the JWT and never to a path parameter, so there is no id to tamper with. The vulnerability class you avoided is **BOLA / IDOR (broken object-level authorization)**, and it is the number one API vulnerability in the OWASP API Top 10. Most people write `GET /armies/{id}` and forget the ownership check entirely.
- **`SECRET_KEY` refuses to fall back to the dev default when `APP_ENV != dev`**, and `docker-compose.yml` enforces it with `${SECRET_KEY:?...}`. Failing loudly beats silently signing tokens with a publicly known key.
- **CORS** is an env-driven allow-list, never `*`, with credentials off.
- **Login** returns one indistinguishable "incorrect username or password" for both failure modes, so it does not leak which usernames exist.
- **`User_Read` never exposes `password_hash`**, and no secrets are committed (`.env` is gitignored, `.env.example` holds only placeholders).

Two open items that are decisions rather than bugs:

- **The JWT lives in `localStorage`** (`src/api/client.ts`). This is the common SPA tradeoff and it is defensible, but it means any XSS on the page can read the token. The alternative is an httpOnly cookie, which trades away some convenience and brings CSRF into scope. Worth knowing you made a tradeoff, even if you keep it.
- **No last-admin guard** on `PATCH /users/{user_id}`. An admin can demote the only admin, and then nobody can administer anything without going back through `make create-admin` against the database.

---

## Performance

**N+1 queries when listing armies.** `list_armies` calls `_army_read` per army, which calls `service.points_total(army.id)`, which loops that army's entries calling `self.session.get(Unit, ...)` once per entry. Listing N armies with M units each issues roughly N × (1 + M) queries instead of a couple.

This is fine at your current scale, and it is not worth contorting the code over today. But learn to spot the shape: **a query inside a loop that is itself inside a loop over query results.** The pattern is called an **N+1 select**, and the fixes to read about are a join, SQLAlchemy's `selectinload`, or computing the totals in a single aggregate query.

---

## Process and docs gaps

- **The backend has no CI.** `warhammer_unit_web` has `.github/workflows/ci.yml` running lint, build, and test on every push and PR. This repo has no `.github/workflows` directory at all, so 210 tests exist that nothing runs automatically. You already wrote the frontend workflow, so porting it is mostly mechanical: swap Node for Python, `npm ci` for `pip install -r requirements.txt -r requirements-dev.txt`, and `npm test` for `pytest`. This is the highest value per minute of anything in this document.
- **`README.md` is 2 lines** while `SPEC.md` is 1,179. Someone landing on the repo learns nothing about what this is or how to run it. The content largely exists already, it just needs a front door that links to `SPEC.md`, `MVP.md`, and `DEPLOY.md`.

---

## Nits

Genuinely minor, batch them whenever.

- `UserAdmin_Read` in `app/api/user.py` is a byte-for-byte duplicate of `User_Read`, and its comment ("Admin-only view — unlike User_Read, it surfaces the admin flag") is factually wrong, because `User_Read` also has `is_admin`. Either the comment or the class should go.
- `_UNAUTHORIZED` in `app/core/security.py` is a module-level exception **instance** that gets raised repeatedly. Each `raise` mutates that one shared object's `__traceback__`. It works, but prefer building a fresh exception (or a small factory function) per raise.
- The Wahapedia scraper is polite, with a sleep between requests and a real User-Agent, which is the right instinct. Be aware that the datasheet content is Games Workshop IP, so republishing it has real ToS and copyright implications if this ever goes public. You already keep `datasheets.json` out of git, which is the right call.

---

## Suggested fix order

1. **Remove the catch-all `ValueError` / `TypeError` handlers (finding 2), then fix the explicit-null `PATCH` (finding 1), together.** They interact: today the handlers are the only reason some internal failures do not look like 500s, so pulling them makes real failures visible. Do them as one change and add a regression test that `PATCH`es an explicit `null` and asserts the response is a clean `400`, not a `500`.
2. **Registration validation and the 72-byte password cap (findings 3 and 4), together.** Both live in the same DTO and are the same kind of fix.
3. **Add backend CI.** Cheapest, highest leverage, and it protects everything above.
4. **README front door.**
5. **Nits**, whenever.

The N+1, the localStorage tradeoff, and the last-admin guard are worth understanding now and fixing when they actually bite. They are on the list so they are not a surprise later, not because they are urgent.
