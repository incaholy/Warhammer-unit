# Deploying Muster on Google Cloud (production shape)

[`DEPLOY.md`](DEPLOY.md) is the fast path: free tier, click through two dashboards, online in an hour. Keep it. It is the right tool for a demo.

**This document is a different exercise.** It deploys the same app the way you would deploy something people depend on, and every practice in it is one you will use again on the next project. The order is deliberate: each stage is only worth doing once the stage before it is real, so treat it as a ladder rather than a checklist. You can stop at Stage 2 and have a working deployment. Stages 3 and up are what separate "it is online" from "we can change it on a Tuesday without fear."

The point is not that Muster needs Google-scale infrastructure. It does not. The point is that the *shape* of the problems does not change with traffic, only the consequences do, and this app is small enough to learn them on.

---

## Target architecture

| Piece | Service | Notes |
|---|---|---|
| **API** | Cloud Run | The `Dockerfile` already binds `$PORT` and runs non-root, so it deploys unchanged. |
| **Frontend** | Firebase Hosting | CDN, plus a rewrite to Cloud Run so the SPA and API share an origin. |
| **Database** | Cloud SQL for PostgreSQL | Managed Postgres with automated backups, point-in-time recovery, and IAM-brokered connections. This is the one component with a real monthly cost. |
| **Images** | Artifact Registry | Tagged by commit SHA, so rollback is redeploying an older tag. |
| **Secrets** | Secret Manager | Injected as env vars. Never in the image, never in the repo. |
| **Migrations** | Cloud Run Job | A gate in the pipeline, not something that happens on container start. |
| **Background work** | Cloud Tasks or Pub/Sub | See Stage 5. This is the one that surprises people. |
| **CI/CD** | GitHub Actions + Workload Identity Federation | No long-lived service account key anywhere. |

```
                    ┌──────────────────────┐
  browser  ───────► │  Firebase Hosting    │  static SPA on a CDN
                    │                      │
                    │  /api/**  ──rewrite──┼──► Cloud Run (API)  ──► Postgres
                    └──────────────────────┘         ▲
                                                     │
   GitHub Actions:  test ─► build ─► migrate job ─► deploy (no traffic) ─► smoke ─► shift traffic
```

---

## Stage 1: Foundations

### Separate environments, from day one

One project is a demo. Two is a deployment. Create **`muster-staging`** and **`muster-prod`** as separate GCP projects, each with its own database, its own secrets, and its own Cloud Run service.

This feels like overhead until the first time you want to test a migration against real-shaped data without risking the real data. Separate projects (rather than separate services in one project) give you a hard blast radius: a mistake in staging cannot reach prod credentials, because they are not in the same IAM boundary.

The rule that makes it useful: **nothing reaches prod that has not run in staging first**, and the promotion is the *same image*, not a rebuild. Rebuilding for prod means you shipped something you never tested.

### Describe the infrastructure in code

Do the first deploy by hand with `gcloud` so you understand what the pieces are. Then write it down as **Terraform**, and never click again.

The reason is not elegance. It is that click-configured infrastructure is undocumented, unreviewable, and unreproducible: six months later nobody knows why a setting is what it is, and rebuilding it means archaeology. Terraform makes the infrastructure a diff you can review like any other code, which means environments cannot drift apart silently.

Start with just the Cloud Run service, the Artifact Registry repo, and the secrets. Do not try to codify everything at once.

### Least privilege

Cloud Run defaults to the project's Compute Engine default service account, which is broadly privileged. Create a dedicated runtime service account with exactly two grants: read the two secrets it needs, and connect to the database. Nothing else.

Same for the deploy identity: the GitHub Actions service account needs to push images and deploy revisions. It does not need to read your database.

The habit to build: **when something asks for a permission, ask what breaks if you say no.** Most of the time nothing does.

**Read for this stage**

- [Terraform: Get Started on Google Cloud](https://developer.hashicorp.com/terraform/tutorials/gcp-get-started). HashiCorp's official tutorial, and the right first hour with Terraform. Do it against a scratch project.
- [Terraform Google provider reference](https://registry.terraform.io/providers/hashicorp/google/latest/docs). The resource you want first is `google_cloud_run_v2_service`.
- [What is Infrastructure as Code?](https://developer.hashicorp.com/terraform/intro). The short version of why the previous two matter.
- [The Twelve-Factor App: Config](https://12factor.net/config). The discipline of keeping configuration out of code. Ten minutes, and it explains a lot of choices in this document.
- [GCP resource hierarchy](https://cloud.google.com/resource-manager/docs/cloud-platform-resource-hierarchy) and [Using IAM securely](https://cloud.google.com/iam/docs/using-iam-securely). Why projects are the blast-radius boundary.

---

## Stage 2: Ship it

Set up once:

```bash
export PROJECT=muster-staging
export REGION=us-central1          # keep this close to the database
export REPO=muster
export SERVICE=muster-api

gcloud config set project $PROJECT
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com sqladmin.googleapis.com
```

**Artifact Registry:**

```bash
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
```

**Cloud SQL.** Use the smallest instance that exists, in the same region as Cloud Run (a cross-region hop is added latency on every single query):

```bash
gcloud sql instances create muster-db \
  --database-version=POSTGRES_16 \
  --region=$REGION \
  --tier=db-f1-micro \
  --storage-size=10GB \
  --backup --enable-point-in-time-recovery

gcloud sql databases create muster --instance=muster-db
gcloud sql users create muster_app --instance=muster-db --password='<generate one>'

export INSTANCE=$PROJECT:$REGION:muster-db      # the "instance connection name"
```

Tiers change, so check what is currently available if `db-f1-micro` is rejected. Turn backups and point-in-time recovery on now, at creation, rather than after you need them.

**Do not give the instance a public IP.** Cloud Run connects through the built-in Cloud SQL socket, which is brokered by IAM and needs no publicly reachable database. That is the whole point: the database has no internet-facing surface to attack.

**Secrets** (grant the runtime service account `roles/secretmanager.secretAccessor` on each):

```bash
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create SECRET_KEY --data-file=-

# Note the shape: empty host, and the socket path passed as ?host=
printf '%s' "postgresql+psycopg2://muster_app:PASSWORD@/muster?host=/cloudsql/$INSTANCE" \
  | gcloud secrets create DATABASE_URL --data-file=-
```

That URL form is worth understanding rather than copying. There is no hostname and no port: SQLAlchemy connects over a **unix socket** that Cloud Run mounts at `/cloudsql/<instance connection name>`, and the Cloud SQL Auth Proxy on the other end handles TLS and authentication. No database password crosses the public internet, and there is no IP allow-list to maintain.

The runtime service account also needs `roles/cloudsql.client`.

**Build, tagged by commit** (never `latest`, because `latest` makes "what is actually running" unanswerable):

```bash
export IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/api:$(git rev-parse --short HEAD)
gcloud builds submit --tag $IMAGE
```

**Deploy:**

```bash
gcloud run deploy $SERVICE \
  --image $IMAGE --region $REGION \
  --service-account muster-api@$PROJECT.iam.gserviceaccount.com \
  --add-cloudsql-instances $INSTANCE \
  --allow-unauthenticated \
  --set-env-vars APP_ENV=production \
  --set-secrets SECRET_KEY=SECRET_KEY:latest,DATABASE_URL=DATABASE_URL:latest \
  --memory 512Mi --max-instances 5 --min-instances 0
```

`--add-cloudsql-instances` is what mounts the `/cloudsql/...` socket the `DATABASE_URL` refers to. Forget it and the app starts fine and then fails on the first query, which is a confusing way to find out.

`--allow-unauthenticated` means the public internet can reach it. Your JWT auth is still what protects the data. That flag is about Google's IAM layer, not your application's.

### One origin, via a Firebase rewrite

Firebase Hosting can forward matching paths to Cloud Run. If the SPA and API answer on the same origin, then **CORS is not involved at all** (no `ALLOWED_ORIGINS`, no "deploy the frontend then go back and redeploy the API" ordering), and **`VITE_API_BASE_URL` can be empty**, which deletes the build-time baking trap that `DEPLOY.md` calls out as biting everyone.

Firebase forwards the full path, so the API has to actually answer on `/api/...`. Mount the routers under a parent prefix in `app/main.py`:

```python
api = APIRouter(prefix="/api")
api.include_router(auth_router)
# ... the rest
app.include_router(api)
```

Keep `/health` at the root so platform probes have an unprefixed target.

`firebase.json` (order matters, the run rewrite must precede the SPA catch-all or the catch-all swallows API calls):

```json
{
  "hosting": {
    "public": "dist",
    "rewrites": [
      { "source": "/api/**", "run": { "serviceId": "muster-api", "region": "us-central1" } },
      { "source": "**", "destination": "/index.html" }
    ]
  }
}
```

> **Coordinated change:** adding the `/api` prefix changes the API's public paths. If the Render deployment is still live, its `VITE_API_BASE_URL` needs the `/api` suffix at the same time, and the frontend needs a **rebuild**, not just a redeploy.

**Read for this stage**

- [Cloud Run container runtime contract](https://cloud.google.com/run/docs/container-contract). What the platform expects of your image (listen on `$PORT`, be stateless, start fast). Your Dockerfile already satisfies it; read it so you know *why* it does.
- [Connecting from Cloud Run to Cloud SQL](https://cloud.google.com/sql/docs/postgres/connect-run). The socket path, the IAM grant, and the URL shape used above.
- [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy). What is actually terminating that connection, and how to use it from your laptop.
- [Secret Manager best practices](https://cloud.google.com/secret-manager/docs/best-practices). Versioning, rotation, and access patterns.
- [Firebase Hosting: serverless rewrites to Cloud Run](https://firebase.google.com/docs/hosting/cloud-run). The mechanism behind the same-origin trick.

---

## Stage 3: Deploy safely

This is where a deployment stops being a leap of faith.

### Migrations are a pipeline gate, not a container startup step

`docker-entrypoint.sh` currently runs `alembic upgrade head` and then execs uvicorn. That is correct for exactly one container, and wrong as soon as the platform runs several:

- Every cold start pays the migration cost before serving.
- Concurrent instances race. Alembic locks its version table so they mostly serialize, but "mostly" is not a deployment strategy.
- **A bad migration crash-loops every instance** instead of failing one pipeline step, so a schema mistake takes the service down rather than stopping the deploy.

Gate the entrypoint so local Compose keeps its current behavior:

```sh
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "==> alembic upgrade head"
  alembic upgrade head
fi
exec "$@"
```

Set `RUN_MIGRATIONS=1` in `docker-compose.yml`, leave it unset on Cloud Run, and migrate as an explicit job:

```bash
gcloud run jobs deploy migrate \
  --image $IMAGE --region $REGION \
  --service-account muster-api@$PROJECT.iam.gserviceaccount.com \
  --set-cloudsql-instances $INSTANCE \
  --set-secrets DATABASE_URL=DATABASE_URL:latest \
  --command alembic --args upgrade,head

gcloud run jobs execute migrate --region $REGION --wait
```

`--wait` is the point: the deploy fails if the migration fails, before any new container serves traffic.

### Migrations must be backward compatible (expand and contract)

This is the most important idea in this document, and it is not obvious.

During a rolling deploy, **the old revision and the new revision are both serving, against the same database**. That is not an edge case, it is how zero-downtime deploys work. So a migration that renames or drops a column breaks the old revision instantly, and if you have to roll back, the new schema breaks the code you rolled back to.

The pattern is **expand and contract**, in three separate deploys:

1. **Expand.** Add the new column, nullable. Do not remove anything. Deploy code that writes both old and new, reads old.
2. **Migrate.** Backfill. Deploy code that reads the new column.
3. **Contract.** Once nothing reads the old column, drop it, in a later deploy.

The rule to internalize: **a migration and the code that depends on it never ship in the same deploy.** Renaming a column in one step is the single most common way to take down a service that "deployed fine."

### Revisions, traffic, and rollback

Cloud Run keeps every revision. Use that:

```bash
# deploy without sending traffic
gcloud run deploy $SERVICE --image $IMAGE --no-traffic --tag candidate --region $REGION

# smoke test the candidate directly
curl https://candidate---$SERVICE-xxxx.run.app/health

# shift traffic gradually
gcloud run services update-traffic $SERVICE --to-tags candidate=10 --region $REGION
gcloud run services update-traffic $SERVICE --to-tags candidate=100 --region $REGION

# rollback is instant, no rebuild
gcloud run services update-traffic $SERVICE --to-revisions PREVIOUS=100 --region $REGION
```

A deploy you cannot undo in thirty seconds is a deploy you will be afraid to make, and being afraid to deploy is how projects rot.

### The pipeline

`test → build → migrate → deploy (no traffic) → smoke → shift traffic`, with staging before prod, promoting the **same image**.

Authenticate GitHub Actions with **Workload Identity Federation**, not a downloadable JSON key:

```yaml
- uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: projects/NUM/locations/global/workloadIdentityPools/github/providers/repo
    service_account: deployer@PROJECT.iam.gserviceaccount.com
```

A service account key in a GitHub secret is a credential that never expires and cannot be scoped to a branch. WIF trades it for a short-lived token issued only to your repo.

> This repo currently has **no CI at all** (see [`CODE-REVIEW.md`](CODE-REVIEW.md)). Add the test job first and get it green before wiring deployment to it. Automated shipping without automated checking is worse than manual shipping.

**Read for this stage** (the most important reading in the document)

- [Martin Fowler: ParallelChange](https://martinfowler.com/bliki/ParallelChange.html). Expand, migrate, contract, in four hundred words. If you read one link from this entire document, read this one.
- [Martin Fowler and Pramod Sadalage: Evolutionary Database Design](https://martinfowler.com/articles/evodb.html). The long-form version: how schemas change continuously without downtime.
- [Cloud Run: rollouts, rollbacks, and traffic migration](https://cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration). Revisions, tags, and gradual traffic shifting.
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html) and [operations reference](https://alembic.sqlalchemy.org/en/latest/ops.html). You already use this; the part worth re-reading is writing a `downgrade` that actually reverses.
- [google-github-actions/auth](https://github.com/google-github-actions/auth). The Workload Identity Federation setup, end to end, with the exact IAM bindings.
- [DORA: the four key metrics](https://dora.dev/guides/dora-metrics-four-keys/). Deployment frequency, lead time, change failure rate, time to restore. This is the research that explains why "rollback in thirty seconds" is a real engineering goal and not just tidiness.

---

## Stage 4: See what is happening

Right now, if the deployed app misbehaves, you have no way to find out why. Three gaps, all cheap to close:

### Structured logs with a request ID

The app configures **no logging at all** today. In production you want JSON log lines (Cloud Logging parses them into queryable fields) and a **request ID on every line**, generated at the edge and returned in a response header.

Why the request ID matters: a user says "it broke around 2pm." Without correlation you are grepping timestamps. With it, they quote the ID from the error, and you get every log line for exactly that request.

Attention's API contract already specifies exactly this (a `trace_id` in every response and an `X-Request-ID` header). Build it here and you will have done it once already.

### Error tracking

`sentry-sdk==2.51.0` is **already in `requirements.txt` and never imported**. You have the tool and it is not plugged in. Three lines in `main.py` and unhandled exceptions arrive with a stack trace, the request path, and the user, instead of vanishing into a 500.

Note the interaction with [`CODE-REVIEW.md`](CODE-REVIEW.md) finding 2: the catch-all `ValueError`/`TypeError` handlers turn real bugs into 400s. Error tracking will not report them, because as far as the app is concerned nothing failed. **Fix that finding first, or you will wire up monitoring that reports nothing.**

### Liveness, readiness, and alerts

`/health` returns `{"status": "ok"}` without touching the database, and the docstring correctly calls it a liveness check. That is the right answer for liveness ("is the process alive"), and the wrong one for readiness ("can this instance actually serve"). A second endpoint that does a trivial `SELECT 1` tells you the difference between "the app is down" and "the app is up but the database is unreachable" without a human diagnosing it.

Then add an uptime check hitting it, and alerts on the handful of things that mean something: elevated 5xx rate, latency p95, and a **budget alert** so a runaway loop is a notification rather than a bill.

**Read for this stage**

- [Google SRE Book, "Monitoring Distributed Systems"](https://sre.google/sre-book/monitoring-distributed-systems/). The four golden signals (latency, traffic, errors, saturation). Free online, and the single best chapter on deciding what is worth alerting on. The rest of the book is free too.
- [SRE Workbook: Implementing SLOs](https://sre.google/workbook/implementing-slos/). How to pick a target that means something instead of alerting on everything.
- [Cloud Logging: structured logging](https://cloud.google.com/logging/docs/structured-logging). Emit JSON and the fields become queryable. Note the special fields, particularly `logging.googleapis.com/trace`.
- [Sentry: FastAPI integration](https://docs.sentry.io/platforms/python/integrations/fastapi/). The three lines that plug in the dependency you already have.
- [The Twelve-Factor App: Logs](https://12factor.net/logs). Why an app should write to stdout and never manage log files.

---

## Stage 5: Scale correctly

### Background work does not survive on serverless (read this one twice)

Cloud Run allocates CPU **during request processing** by default. Work you kick off to run after the response is throttled to almost nothing and may simply never finish. FastAPI's `BackgroundTasks`, a bare `asyncio.create_task`, a thread you spawned: all unreliable here, and the failure is silent, which is the worst kind.

Muster does not do background work today, so this costs you nothing right now. Learn it anyway, because the options are:

| Approach | When |
|---|---|
| `--cpu-always-allocated` | Simplest. Changes billing to instance-based. Fine for small, frequent tasks. |
| **Cloud Tasks** pushing to a worker endpoint | The general answer. Durable, retried with backoff, and the work survives the instance that queued it. |
| **Pub/Sub** push subscription | Same shape, better for fan-out to several consumers. |
| **Cloud Run Jobs** | Batch and scheduled work, not per-request work. |

### Connection pooling

This is the one that will actually bite you, so do the arithmetic before it does.

`get_engine()` uses SQLAlchemy's default pool: 5 connections plus up to 10 overflow, so up to **15 per process**. Cloud Run runs N instances, so the real ceiling is **N × 15**. With `--max-instances 5` that is 75 connections. A `db-f1-micro` Cloud SQL instance allows on the order of 25, so a modest traffic spike does not slow the app down, it makes it start throwing `FATAL: too many connections` while the database sits nearly idle.

The trap is that autoscaling and connection pooling are both trying to help and they multiply. Nothing warns you, because each layer's setting looks reasonable on its own.

Do all three:

- Keep `--max-instances` small and deliberate (the deploy above uses 5).
- Size the pool for serverless: `create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=3)`.
- Know your instance's actual limit: `gcloud sql instances describe muster-db --format='value(settings.databaseFlags)'`, or query `SHOW max_connections;`.

Check your own numbers with `max_instances × (pool_size + max_overflow) < max_connections`, and leave headroom for the migration job and for you connecting with `psql`.

If you outgrow that, the next step is a connection pooler in front of the database (PgBouncer, or Cloud SQL's built-in managed connection pooling where available) so that N application pools multiplex onto a much smaller number of real Postgres connections.

Related: every endpoint in this app is a sync `def`, so FastAPI runs them in a threadpool. Threads and pool connections are separate limits and the pool is the smaller one, which is usually what actually caps throughput.

### Concurrency and sizing

Cloud Run's default is 80 concurrent requests per instance. That is generous for an app whose handlers block on database calls in a threadpool. Lower concurrency means more instances (more connections, more cost) and less queueing per instance. Do not guess: put load through it and read the numbers.

**Read for this stage**

- [Cloud Run: CPU allocation](https://cloud.google.com/run/docs/configuring/cpu-allocation). Read this before you ever write background work on Cloud Run. It is the whole reason `BackgroundTasks` is unreliable there.
- [Cloud Run: concurrency](https://cloud.google.com/run/docs/configuring/concurrency). How the setting interacts with instance count and cost.
- [Cloud Tasks overview](https://cloud.google.com/tasks/docs) and [Pub/Sub push subscriptions](https://cloud.google.com/pubsub/docs/push). The two durable ways to run work outside a request.
- [SQLAlchemy: connection pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html). What `pool_size`, `max_overflow`, and `pool_pre_ping` actually do.
- [Cloud SQL: managing connections](https://cloud.google.com/sql/docs/postgres/manage-connections). Instance connection limits and how to avoid exhausting them.

---

## Stage 6: Harden

- **Rate limiting.** `/auth/login` is an unauthenticated endpoint that does a bcrypt verify, which is deliberately expensive. That is a free CPU-exhaustion lever for anyone who wants it, and an unlimited password-guessing surface. Cloud Armor in front, or application-level limiting.
- **Dependency scanning.** Dependabot or `pip-audit` in CI. Pinned requirements (which you have, and which is good) go stale silently otherwise.
- **Secret rotation.** Secret Manager versions exist so that rotating `SECRET_KEY` is a new version plus a redeploy. Know that rotating it invalidates every issued JWT, which is a feature when you need it.
- **Backups, and a restore you have actually performed.** You enabled automated backups and point-in-time recovery at instance creation. That is half the job. The other half is running a restore once, into a throwaway instance, while nothing is on fire, so that the procedure is familiar rather than theoretical. An untested backup is a hypothesis.

**Read for this stage**

- [OWASP API Security Top 10](https://owasp.org/API-Security/editions/2023/en/0x11-t10/). The canonical list. You already avoided number one (BOLA, see `CODE-REVIEW.md`); read the rest so that is not luck.
- [Cloud Armor: rate limiting](https://cloud.google.com/armor/docs/rate-limiting-overview). Throttling `/auth/login` before it becomes a free CPU-exhaustion lever.
- [pip-audit](https://pypi.org/project/pip-audit/) and [Dependabot](https://docs.github.com/en/code-security/dependabot). Pinned dependencies (which you have, correctly) go stale silently without one of these.
- [Cloud SQL: backups and recovery](https://cloud.google.com/sql/docs/postgres/backup-recovery/backups). Including how to actually run the restore you should practice.

---

## Where these skills show up again on Attention

This is why the effort is worth it. Attention is the same shape (FastAPI, Postgres, Alembic, a React SPA) with one addition that makes several of these lessons load-bearing rather than theoretical:

| Skill here | Where it lands on Attention |
|---|---|
| **Background work on serverless** (Stage 5) | Attention generates its comprehension challenge **asynchronously** with FastAPI `BackgroundTasks`. On Cloud Run that silently does not work, so the design has to move to Cloud Tasks or a worker. This is the single most transferable lesson in this document. |
| **Expand and contract migrations** (Stage 3) | Attention's next assignment adds columns and **drops** one. Dropped in a single deploy, that breaks the still-running old revision and blocks rollback. |
| **Request IDs and structured logs** (Stage 4) | Attention's API contract already mandates a `trace_id` in every response and an `X-Request-ID` header. Same idea, already specified. |
| **Migrations as a pipeline gate** (Stage 3) | Same Alembic setup, same container-start temptation. |
| **Environments and promotion** (Stage 1) | Attention has real users' data in a way a personal collection app does not. |
| **Rate limiting auth** (Stage 6) | Attention's contract already reserves a `RATE_LIMITED` error code for exactly this. |

Do these here, on an app where the stakes are your own hobby data, and they will already be habits when they matter.

---

## Where to start reading

Each stage above has its own reading list. If you want an order rather than a pile:

**This week, in about two hours:**

1. [Martin Fowler: ParallelChange](https://martinfowler.com/bliki/ParallelChange.html). Short, and it changes how you write every migration from here on.
2. [Cloud Run: CPU allocation](https://cloud.google.com/run/docs/configuring/cpu-allocation). Short, and it is the thing that will silently break Attention's background generation.
3. [Google SRE Book: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/). One chapter, free, and it is the difference between "we have logs" and "we know when it breaks."

**Then, hands on:** the [Terraform GCP tutorial](https://developer.hashicorp.com/terraform/tutorials/gcp-get-started) against a scratch project. Reading about IaC does very little; doing one `terraform apply` does a lot.

**Books, if you want depth rather than links:**

- **[Designing Data-Intensive Applications](https://dataintensive.net/)** (Martin Kleppmann). The reference for everything about data and distributed systems. Chapter 7 (Transactions) is the one that pays off soonest given the concurrency notes in `CODE-REVIEW.md`.
- **Release It!** (Michael Nygard). Specifically about what fails when software meets production: timeouts, retries, circuit breakers, cascading failure. Reads like a series of war stories, which is why it sticks.
- **[Site Reliability Engineering](https://sre.google/books/)** (Google). Free online in full. Skim rather than read cover to cover.
- **Accelerate** (Forsgren, Humble, Kim). The research behind the DORA metrics: why deploying more often, in smaller pieces, with fast rollback, correlates with everything else going well.

---

## Cost

**Cloud SQL is the line item.** It does not scale to zero, so it bills whether or not anyone uses the app. The smallest instance runs roughly ten dollars a month and up, plus storage. Everything else is close to free at this traffic: Cloud Run scales to zero and has a generous free tier, Firebase Hosting's free tier covers a small SPA, and Artifact Registry is cents per month for image storage. Verify against the current pricing calculator, since tiers change.

That is the honest trade for the managed-Postgres properties you are buying: automated backups, point-in-time recovery, no publicly reachable database, and IAM-brokered connections.

Two more things turn on a real bill: `--min-instances 1` (a warm container, which removes the one to two second cold start) and `--cpu-always-allocated`. Start with neither.

Set a **budget alert** on the project before you walk away. Two staging and prod databases running always-on is exactly the kind of thing that quietly accrues, and a runaway retry loop should reach you as a notification, not as a statement.

---

## Verify

```bash
curl https://<service>.run.app/health            # direct to Cloud Run
curl https://<site>.web.app/api/units?limit=3    # through the rewrite
```

Open the site, register, build an army. In the browser Network tab, confirm API calls go to **your own origin** under `/api/...` with no CORS preflight. A preflight means the rewrite is not matching and you are hitting Cloud Run cross-origin.

## Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| API calls return HTML with a `200` | The `/api/**` rewrite is missing or ordered after the SPA catch-all | Put the run rewrite first in `firebase.json` |
| `404` on every API path | Routers not mounted under `/api`, or wrong service/region in the rewrite | Match the prefix to the rewrite exactly |
| Container will not start, logs mention `SECRET_KEY` | `APP_ENV=production` with no secret bound | Bind it with `--set-secrets` |
| Background work never completes | Cloud Run throttles CPU after the response | Stage 5 |
| `FATAL: too many connections` | N instances times the pool size exceeds the instance limit | Lower `--max-instances`, shrink the pool (Stage 5) |
| App starts, then every query fails | `--add-cloudsql-instances` missing, so `/cloudsql/...` is not mounted | Add it to the service and the migrate job |
| Old revision 500s during a deploy | A non-backward-compatible migration | Expand and contract, Stage 3 |
| First request after idle is slow | Scale-to-zero cold start | Expected. `--min-instances 1` only if it genuinely matters |
