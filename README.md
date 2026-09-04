# Email Validation Engine

In-house email verification engine for cold email / GTM / sales / marketing /
transactional list cleaning. Upload a list, validate every address, get a
cleaned sheet back — with the SMTP mailbox probe owned in-house (the part
Smartlead / Instantly / Mailchimp resell).

Milestones **M0 (engine) · M1 (file pipeline) · M2 (SMTP subsystem)** are
implemented and tested.

> Full architecture + roadmap live in Linear project **Email Validation Engine**
> (team `PRA`), document *Architecture & Technical Design*.

## The pipeline (cheap → expensive, stop early)

| # | Layer | Module | Status |
|---|-------|--------|--------|
| 1 | Syntax (RFC 5322) | `eve/layers/syntax.py` | ✅ M0 |
| 2 | Normalize + dedupe key (Gmail dot/plus) | `eve/layers/normalize.py` | ✅ M0 |
| 3 | Typo suggestion (Damerau-Levenshtein) | `eve/layers/typo.py` | ✅ M0 |
| 4 | DNS / MX (cached) | `eve/layers/dns_mx.py` | ✅ M0 |
| 5 | Classify (disposable / role / free) | `eve/layers/classify.py` | ✅ M0 |
| 6 | SMTP mailbox probe (never sends DATA) | `eve/smtp_infra/prober.py` | ✅ M2 |
| 7 | Catch-all detection (cached per-domain) | `eve/smtp_infra/service.py` | ✅ M2 |

Orchestrated by `eve/engine.py::validate(email) -> Verdict` (single address) and
`eve/jobs/pipeline.py::run_job` (bulk files).

### Statuses

`valid` · `invalid` · `risky` (role / catch-all) · `unknown` · `disposable` · `spam_trap`,
plus a `0–100` deliverability score.

With SMTP **disabled**, a valid address on a mail-accepting domain returns
**`unknown`** (honest — the mailbox wasn't probed). With SMTP **enabled**, the
prober confirms the mailbox → `valid` / `invalid`, detects catch-all domains →
`risky`, and — crucially — does *not* report a confident `valid` for
Gmail/Outlook/Yahoo, which lie to probes (see `smtp_infra/providers.py`).

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                   # 57 tests incl. accuracy gate + SMTP integration
uvicorn eve.api.main:app --reload --port 8000   # docs at /docs
```

### Real-time single address

```bash
curl -s localhost:8000/v1/verify -H 'content-type: application/json' \
  -d '{"email":"john@gmial.com"}'
# -> status "unknown", suggested_correction "john@gmail.com"
```

### Bulk file (upload → map → process → download)

```bash
# 1. upload a CSV, get a file_id + detected columns
curl -s -F "file=@examples/sample_list.csv" localhost:8000/v1/files

# 2. start a job with a column mapping
curl -s localhost:8000/v1/jobs -H 'content-type: application/json' \
  -d '{"file_id":"<file_id>","mapping":{"email":"email","first_name":"first_name","last_name":"last_name"}}'

# 3. poll status, then download
curl -s localhost:8000/v1/jobs/<job_id>
curl -s "localhost:8000/v1/jobs/<job_id>/download?segment=cleaned"   # or valid | removed
```

The **cleaned** sheet preserves every original column and appends verdict columns
(`email_status`, `sub_status`, `score`, `normalized_email`, `suggested_correction`,
`is_disposable`, `is_role`, `is_free`, `is_catch_all`, `mx_found`, `duplicate_of`).
`valid` and `removed` partition the rows (`valid ∪ removed = all`).

### Programmatic use

```python
from eve import validate
v = validate("sales@acme.io", enable_dns=False)
print(v.status, v.sub_status, v.score)   # Status.RISKY SubStatus.ROLE_ACCOUNT 48
```

### See SMTP verdicts locally (mock mailserver)

Real port-25 probing needs egress IPs you don't have locally, and big providers
lie to probes — so to *see* the SMTP engine produce genuine `valid` / `invalid` /
`catch-all` verdicts, point it at a local mock mailserver:

```bash
python scripts/mock_mailserver.py &                       # mock MX on :2525
EVE_ENABLE_SMTP=true EVE_ENABLE_DNS=false \
  EVE_SMTP_TARGET_HOST=127.0.0.1 EVE_SMTP_TARGET_PORT=2525 \
  uvicorn eve.api.main:app --port 8010                    # SMTP-enabled API
# then verify examples/smtp_demo.csv against http://localhost:8010
```

Result: `alice@acme-demo.com`→valid, `ghost@acme-demo.com`→invalid,
`info@acme-demo.com`→risky (role), `*@catchall-demo.com`→risky (catch-all).
Browse the run at `http://localhost:8010`.

### Web UI (Validrow)

The web app is served by the engine itself — one process, one port, no separate
front-end to start or keep in sync:

```bash
uvicorn eve.api.main:app --port 8000     # API + UI -> http://localhost:8000
```

Screens: **Dashboard** (volume, verdict mix, recent results), **Validate**
(upload → preview → map columns → live progress → downloads), **Single check**
(one address, full seven-layer trace), **Contacts** (every address across all
jobs, de-duplicated, expandable to its trace), **Analytics**, **Exports**,
**History**, **Settings** and **How it works**.

Implementation lives in `src/eve/web/`:

| File | Role |
| --- | --- |
| `views.py` | View-model — store rows → what each screen renders |
| `format.py` | Palette, number formatting, SVG path geometry |
| `series.py` | Time bucketing for the charts (daily/weekly/monthly) |
| `templates/` | One Jinja template per screen |
| `static/app.js` | Only what must not round-trip: chart hover, row expansion, selection, job polling |

Filters, sorting, paging and the wizard step all live in the query string, so
every view is linkable and the back button works.

**Empty states are real.** A fresh install has no data, and the app says so
rather than inventing numbers: the period-over-period deltas stay hidden until
there is a previous period to compare against, and a chart with one reading says
"not enough history" instead of drawing a spike.

**The sidebar names the workspace, not a person.** There is no auth, so the
footer reads *Local workspace* over the engine host by default. Set
`EVE_WORKSPACE_NAME` / `EVE_WORKSPACE_EMAIL` to show an owner.

## Architecture notes

- **Streaming + O(unique) memory.** The pipeline makes two streaming passes and
  dedupes emails *and* domains, so a 1M-row file (≈50–200k unique domains)
  resolves each domain's MX and catch-all **once**, cached.
- **Pluggable backends, selected by configuration.** Local disk storage,
  in-process KV and a SQLite workspace database are the defaults (zero external
  services). Set `EVE_S3_*`, `EVE_REDIS_URL` or `EVE_WORKSPACE_DB_URL` and the
  process picks S3/R2, Redis and Postgres instead — same interfaces, no code
  change. Every infra setting is *empty* by default, because a placeholder like
  `redis://localhost:6379` cannot be told apart from a real one. The API logs
  which backend each subsystem resolved to at startup, and warns when a
  non-`local` environment is still running on a single-process default.
- **One workspace per row.** `jobs` and `addresses` both carry a
  `workspace_id`, and the address identity is `(workspace_id, email)` — so
  de-duplication happens *within* a tenant rather than across all of them.
  There is no auth yet, so the value comes from `EVE_WORKSPACE_ID`; when auth
  lands, `tenancy.current_workspace_id` becomes a request-scoped lookup and
  nothing else changes.
- **Alembic owns the schema.** There is no `create_all`: a fresh SQLite file and
  a year-old Postgres reach head by replaying the same revisions, which run on
  startup. Databases predating Alembic are adopted by revisions 0001–0003,
  which check what is present before acting.
- **The SMTP moat** (`eve/smtp_infra/`): `prober` (aiosmtplib, never DATA),
  `providers` (per-provider heuristics), `rate_limiter` (per-MX token bucket),
  `ip_pool` (reputation / rotation / cooldown / warm-up), `blacklist` (DNSBL
  monitoring), composed by `service.SmtpService`. Needs port-25-capable egress
  IPs in production (set `EVE_SMTP_EGRESS_IPS`).
- **The DNSBL monitor runs, it is not just available.** Every process that
  probes — the API and each worker — scans its own egress IPs on a loop while
  SMTP is on. A listing pulls the IP from rotation via the pool's cooldown and
  logs at `ERROR`, which is the level that should page someone: a listed IP
  invalidates every verdict it produces. With no `EVE_SMTP_EGRESS_IPS` the scan
  does not start, because probes then leave on the OS default route — an
  address rotation cannot avoid and this process has no business cooling down.

## Docker

```bash
docker compose up --build   # api + worker + postgres + redis + minio
```

Compose runs the **production** backends, which is the point: `make run` uses
SQLite and local disk, so this is where the shared-backend paths actually get
exercised before a deploy does it for you.

## Running jobs

Bulk jobs go wherever `EVE_QUEUE_BACKEND` resolves:

| | in-process (`inline`) | worker (`arq`) |
|---|---|---|
| When | no `EVE_REDIS_URL` | Redis configured + `worker` extra |
| Survives an API restart | no — the run is orphaned | yes |
| Per-MX rate limit | per process | shared across workers |
| Probes originate from | whichever box served the upload | the worker hosts |

```bash
make worker      # arq eve.jobs.worker.WorkerSettings
```

Run the worker on the port-25-capable egress hosts. The shared per-MX limiter
is not optional at that point: without Redis, every worker gets a *full* probe
budget against the same provider, which is the fast way to get an IP blocked.

## Migrations

The app migrates to head on startup, so there is usually nothing to run.

```bash
make migrate                      # alembic upgrade head, as a separate deploy step
make migration m="add credits"    # new revision
```

Revisions 0001–0003 tolerate a database that predates Alembic. Write anything
from 0004 on strictly.

## Webhooks

Pass `webhook_url` when creating a job and a callback is POSTed when it
finishes — on failure too, which is when the caller most needs it. Set
`EVE_WEBHOOK_SECRET` and verify:

```
X-Eve-Signature: sha256=HMAC-SHA256(secret, "<X-Eve-Timestamp>.<raw body>")
```

Delivery retries with backoff on 5xx/429/network errors, and gives up
immediately on other 4xx. With Redis it is a queued task and survives a
restart; without it, it is a background task in the API process and does not.

## Layout

```
src/eve/
  layers/      syntax · normalize · typo · dns_mx · classify · smtp(seam)
  smtp_infra/  prober · providers · rate_limiter · ip_pool · blacklist · service   (M2)
  jobs/        models · store · csv_io · pipeline                                   (M1)
  data/        disposable / roles / free / top_domains lists
  engine.py    validate() orchestrator + scoring
  storage.py   ObjectStore (local + S3)   kv.py  KV (memory + redis)
  api/         FastAPI: /health /v1/verify /v1/files /v1/jobs
tests/         per-layer + pipeline e2e + SMTP integration (aiosmtpd) + SQL store
```

## Config (env, prefix `EVE_`)

| Var | Default | Meaning |
|-----|---------|---------|
| `EVE_ENABLE_DNS` | `true` | perform live MX lookups |
| `EVE_ENABLE_SMTP` | `false` | SMTP mailbox probe (needs port-25 egress) |
| `EVE_DNS_TIMEOUT` | `5.0` | resolver timeout (s) |
| `EVE_CHUNK_SIZE` | `500` | emails per verify chunk |
| `EVE_VERIFY_CONCURRENCY` | `50` | max concurrent mailbox probes |
| `EVE_SMTP_EGRESS_IPS` | `""` | comma-separated port-25 source IPs (empty = OS default) |
| `EVE_PER_MX_RATE` | `5.0` | probes/sec per destination MX |
| `EVE_IP_WARMUP_DAILY_CAP` | `50` | per-IP daily probes while warming |
| `EVE_DNSBL_ENABLED` | `true` | scan the egress IPs for blacklist listings |
| `EVE_DNSBL_ZONES` | `""` | comma-separated; empty = Spamhaus / Barracuda / SpamCop |
| `EVE_DNSBL_INTERVAL_SECONDS` | `3600` | seconds between scans |

Infra — **empty means "use the local default"**, so these are also the switches
that turn each shared backend on:

| Var | Default | Meaning |
|-----|---------|---------|
| `EVE_WORKSPACE_DB_URL` | `""` | Postgres URL; empty = SQLite file on local disk |
| `EVE_WORKSPACE_ID` | `default` | which workspace this process reads and writes |
| `EVE_S3_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` | `""` | all three switch storage to S3/R2 |
| `EVE_S3_ENDPOINT` | `""` | blank for AWS; set for R2/MinIO/Spaces |
| `EVE_REDIS_URL` | `""` | job queue + shared per-MX rate limiter |
| `EVE_QUEUE_BACKEND` | `auto` | `auto` \| `inline` \| `arq` |
| `EVE_MAX_UPLOAD_BYTES` | `104857600` | rejected with 413 above this |
| `EVE_RATE_LIMIT_PER_MINUTE` | `0` | per client IP; `0` disables |
| `EVE_CORS_ORIGINS` | `""` | comma-separated; empty = same-origin only |
| `EVE_WEBHOOK_SECRET` | `""` | HMAC signing key; empty = unsigned |
| `EVE_SENTRY_DSN` | `""` | needs the `sentry` extra |

See `.env.example` for the full list, including the SMTP probe identity you
must set before enabling `EVE_ENABLE_SMTP`.
