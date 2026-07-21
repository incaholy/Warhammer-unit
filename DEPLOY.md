# Deploying Muster

How to put the app online for free so anyone can use it. Three pieces get
separate homes:

| Piece | What it is | Host |
|---|---|---|
| **Database** | Postgres | **Neon** (free, persistent) |
| **API** | this repo — FastAPI in Docker | **Render** Web Service (free) |
| **Frontend** | `warhammer_web` — static React build | **Render** Static Site (or Cloudflare Pages / Vercel) |

The frontend is served as static files; all per-user/dynamic behaviour lives in
the **API + database** tier. Everything below is free-tier; the trade-off is
idle spin-down (first request after a nap is slow) — fine for a demo.

> **Deploy order matters** (a chicken-and-egg with URLs):
> **Neon → API → seed → frontend → set the API's `ALLOWED_ORIGINS` → redeploy API.**

---

## Prerequisites

- Both repos pushed to GitHub (`warhammer_unit`, `warhammer_web`).
- A **Neon** account (neon.tech) and a **Render** account (render.com) — both free.

---

## Step 1 — Database (Neon)

1. Create a Neon **Project**. **Pick a region matching where the Render API will
   run** (e.g. both AWS `us-east`) — cross-region adds latency to every query.
2. Neon auto-creates a database + role. Open **Connect / Connection Details** and
   copy the **Pooled connection** string (its host contains `-pooler`). It looks
   like:
   ```
   postgresql://neondb_owner:PASSWORD@ep-xxxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
3. No edits needed: this app uses `psycopg2`, so the plain `postgresql://` scheme
   is correct, and `connection.py` already sets `pool_pre_ping=True` (handles
   Neon dropping idle connections). Keep `?sslmode=require`.

---

## Step 2 — API (Render Web Service)

**New → Web Service**, connect the `warhammer_unit` repo, then:

| Setting | Value |
|---|---|
| Runtime | **Docker** (auto-detected from the `Dockerfile` — no build/start command needed) |
| Branch | `main` |
| Region | **same as Neon** |
| Instance type | Free |
| Root Directory | *(blank — Dockerfile is at repo root)* |
| Health Check Path | `/health` |

**Environment variables** (add before the first deploy — the container runs
`alembic upgrade head` on boot and crashes if `DATABASE_URL` is missing):

| Key | Value |
|---|---|
| `DATABASE_URL` | the Neon **pooled** string from Step 1 |
| `SECRET_KEY` | a random value — use Render's **Generate**, or `openssl rand -hex 32` |
| `ALLOWED_ORIGINS` | the frontend URL — **leave blank for now**, fill in at Step 5 |

- `APP_ENV` is **not** needed — unset ⇒ production (the app fail-closes without a
  real `SECRET_KEY`).
- **Port**: nothing to set. The image binds to Render's `$PORT` automatically
  (`CMD` → `--port ${PORT:-8000}`).

On deploy, migrations create all 11 tables in Neon and the service comes up at
`https://<name>.onrender.com`. It has **no catalog data yet** — that's Step 3.

---

## Step 3 — Seed the catalog

The seeded units live only in dev; `scripts/data/datasheets.json` ships **empty**
in git (it's scraped GW content). Seed Neon from your laptop — Neon is reachable
over the internet:

```bash
cd warhammer_unit
make scrape            # regenerates datasheets.json from the local cache (instant, offline)

DATABASE_URL="postgresql://…-pooler…neon.tech/neondb?sslmode=require" \
  python -m scripts.seed_datasheets
```

Loads ~1331 units into Neon. `make scrape` needs `beautifulsoup4`/`lxml`
(dev deps), so scrape **locally**, seed **against Neon**.

> Re-running the seed today **creates only** — it does not refresh changed stats
> (see the Tier 1 "seed upsert" item in the backlog). Fine for a first load.

---

## Step 4 — Frontend (Render Static Site)

**New → Static Site**, connect the `warhammer_web` repo, then:

| Setting | Value |
|---|---|
| Build Command | `npm ci && npm run build` |
| Publish Directory | `dist` |
| Branch | `main` |

**Environment variable** (Vite bakes this into the bundle **at build time**):

| Key | Value |
|---|---|
| `VITE_API_BASE_URL` | the API URL from Step 2, e.g. `https://<api>.onrender.com` |

**Redirect/Rewrite rule** (client-side routing — without it, refreshing on
`/armies/123` 404s):

- Source `/*` → Destination `/index.html` → Action **Rewrite** (not Redirect).

If the build fails on a Node version, add `NODE_VERSION` = `20` (or `22`).

---

## Step 5 — Connect the two (CORS)

The frontend and API are on different origins, so the browser needs CORS:

1. Copy the frontend's URL (e.g. `https://<web>.onrender.com`).
2. In the **API** service, set `ALLOWED_ORIGINS` to that URL and **redeploy**.
   (The CORS middleware only mounts when `ALLOWED_ORIGINS` is non-empty.)

---

## Verify

```bash
curl https://<api>.onrender.com/health                 # {"status":"ok"}
curl "https://<api>.onrender.com/units?limit=3"        # real units (seeded)
```

Then open the frontend URL, register an account, and build an army.

---

## Notes & gotchas

- **Free-tier sleep**: the Render API and Neon both idle-suspend; the first
  request after a nap is slow (~seconds), then fast. `pool_pre_ping` reconnects
  cleanly. A small paid tier keeps things always-on.
- **Region**: keep Neon and the API in the same region/cloud.
- **Secrets**: never commit `SECRET_KEY` or the Neon `DATABASE_URL` — they live
  only in the host's env-var settings.
- **Custom domain**: optional — the `*.onrender.com` subdomain works out of the
  box with HTTPS.
