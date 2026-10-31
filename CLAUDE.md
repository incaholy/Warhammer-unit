# Warhammer Unit Backend

Backend for storing Warhammer 40k unit datasheets. FastAPI + SQLModel +
PostgreSQL + Alembic. The target architecture is in ARCHITECTURE.md and the build
order in ROADMAP.md — read both before making structural changes. SPEC.md is the
detailed specification: data model, paths, and behaviour.

The Kill Team part (a game tracker) is designed in KILLTEAM.md — read it before
working on any Kill Team code, and update it when a design decision changes. It is
kept separate from the 40k army list builder: `/kill-team` vs `/army-list`. Its
build order and status are the K entries in ROADMAP.md.

## Commands

```bash
uvicorn app.main:app --reload        # run the API
alembic revision --autogenerate -m "what this ONE revision does"   # after editing models
alembic upgrade head                 # apply migrations
pytest                               # run tests
make openapi                         # regenerate openapi.json from the app (no DB needed)
make lint-imports                    # check the layering contracts in .importlinter
make check                           # pre-PR gate: ruff check + format check + lint-imports + pytest
```

`make openapi` MUST run after any change to a router or to a request/response
schema. The committed `openapi.json` is the frontend's source of truth, and CI
fails the build when it drifts from the app. `make lint-imports` MUST pass before a
PR — CI runs it too — and `make check` is the whole gate in one command. Both need
`make install-dev` first: `ruff` and `import-linter` live in `requirements-dev.txt`,
not `requirements.txt`.

`DATABASE_URL` must be set (it lives in `.env`, which is gitignored).

## Layout

- `app/api/` — FastAPI routers, one module per resource, with `*_Create`/`*_Read` schemas
- `app/core/services/` — business logic, one `<Thing>Service` class per file
- `app/core/db/` — `models.py` and `models_killteam.py` (SQLModel tables), `columns.py`
  (shared column types and helpers), `connection.py` (engine/session), `alembic/`
  (migrations)

## Conventions

- Layering is strict: API → service → DB. Routers never touch the session;
  services never raise `HTTPException`.
- Services raise typed errors. The cross-cutting ones live in
  `app/core/services/errors.py` — `NotFoundError` (→404) and `ConflictError` for
  duplicates (→409). Each service defines its own `*ValidationError(ValueError)`
  in its own module (→400, with a `field`; e.g. `UnitValidationError` in
  `service_unit.py`). Every error inherits **two** bases: `CodedError` (the marker
  base in `app/core/errors.py`) and the builtin it maps to (`LookupError`/
  `ValueError`), and carries its own `code` (`ErrorCode`), `message`, and `field`.
  `app/main.py` registers **one** handler, against `CodedError`, so a new error
  class is mapped to its status automatically — there is no registry to update.
  Never raise `HTTPException` in a service.
- Every `*_Create`/`*_Update` schema inherits `WriteSchema` (`app/api/fields.py`),
  which forbids unknown keys, and bounds every string and int it declares — reusing
  `Name`, `Username`, `Stat`, `DiceValue`, `WeaponCategory` or `INT32_MAX` rather than restating a
  limit. A SQLModel `max_length` constrains the DDL, not the request schema, so an
  unbounded field is a 500 on Postgres that SQLite cannot see.
  `tests/test_api_write_bounds.py` enforces both over `openapi.json`.
- Schema changes go through `models.py` + Alembic, never raw SQL. Migrations are
  **small and incremental**: one revision per coherent operation, named by its `-m`
  message, so a failure names a revision rather than a line. Two rules keep that safe —
  never split a constraint swap (a drop-and-add is one operation), and give a data
  backfill its own revision. `ARCHITECTURE.md` §4 has the reasoning and the `env.py`
  property it depends on.
- Keep stat names matching the datasheet terms used in `models.py`
  (`movement`, `toughness`, `armor_save`, `wounds`, `leadership`,
  `objective_control`).

## Documentation style

Applies to **new and edited** prose in this repo's markdown, not as a mandate to
rewrite what is already written. Two classes of content take different rules:

- **Reference** — tables, endpoint lists, the data model, the glossary, success
  criteria. One statement per cell or bullet, active voice, sentences under 25 words,
  no noun cluster longer than three. Conditions become a vertical list rather than a
  sentence with commas.
- **Argument** — reasoning, rejected alternatives, known gaps, anything answering
  *why*. Only the fixed terms below and active voice apply. Subordinate clauses are
  the content here; do not flatten them.

**One term, one meaning.** Each of these has already produced a wrong count:

| Use | Means | Not |
|---|---|---|
| `path` | one URL template | `endpoint`, `route` |
| `operation` | one method + path pair | `endpoint`, `route` |
| `table` | a database table | `model` (a model is the SQLModel class) |
| `built` | merged and tested on `main` | `done`, `complete`, `finished` |
| `planned` | designed, not built | `upcoming`, `pending`, `TODO` |

`endpoint` and `route` stay usable as informal collectives ("the Kill Team
endpoints") and with a count of one. They are refused **beside a count of two or
more**, which is the only place the ambiguity misleads: that is where a reader
re-derives the number and gets a different one, because the games router has 11
nested paths carrying 15 nested operations.

**Normative statements use RFC 2119 keywords**, capitalised: `MUST`, `MUST NOT`,
`SHOULD`, `MAY`. Those four only — `SHALL`, `REQUIRED`, `RECOMMENDED` and `OPTIONAL`
are synonyms and the rule above refuses them. Use them where a statement carries
obligation: success criteria, a convention a client must honour, a decision's Choice
cell. Not in descriptive text — a data model row states what is, not what is required.

**Not adopted: ASD-STE100's approved dictionary.** A controlled vocabulary earns its
cost only at full compliance, because partial compliance means a reader can no longer
assume any word is approved — so 80% of a dictionary buys close to none of its benefit
at most of its cost. The fixed-term table does the part that pays: it constrains the
words this project actually equivocates on, not all nine hundred.

**Every count in prose names its source.** A number that cannot be re-derived from a
file, a query or `openapi.json` does not belong in prose, and where a count is measured
at a commit, say which commit.

`tests/test_docs_style.py` enforces the two rules a test can hold. The rest are
habits, and this file says which is which deliberately.
