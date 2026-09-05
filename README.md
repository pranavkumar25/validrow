# Validrow

Email verification that proves the mailbox. Upload a CSV, get every column back
with a verdict on every row.

Seven layers run in order from cheapest to most expensive, and an address that
is settled early never reaches the ones that cost a network call. The last two
are an SMTP mailbox probe and catch-all detection, run on our own infrastructure
rather than resold from a third party.

One process serves the landing page at `/`, the web app at `/app`, the JSON API
at `/v1` and the API reference at `/docs`.

## Quickstart

```bash
make install     # venv + pip install -e ".[dev]"
make test        # 314 tests
make run         # API and web app on http://localhost:8000
```

Python 3.9 or newer. No external services are required to run it: the defaults
are a SQLite file, local disk and an in-process queue.

## The pipeline

| # | Layer | Module | What settles here |
|---|---|---|---|
| 1 | Syntax | `layers/syntax.py` | RFC 5322 parsing, plus the limits the RFC leaves to the implementation |
| 2 | Normalize and dedupe | `layers/normalize.py` | Gmail dots and plus tags collapse to one key |
| 3 | Typo correction | `layers/typo.py` | Damerau-Levenshtein against known domains, cached per domain |
| 4 | DNS and MX | `layers/dns_mx.py` | Resolved once per domain and cached for the run |
| 5 | Classification | `layers/classify.py` | Role accounts, free providers, 8,720 disposable domains |
| 6 | SMTP mailbox probe | `smtp_infra/prober.py` | EHLO, MAIL FROM, RCPT, QUIT. Never DATA |
| 7 | Catch-all detection | `smtp_infra/service.py` | One probe per domain |

`engine.validate(email)` runs one address. `jobs/pipeline.run_job` runs a file.

## Verdicts

The engine emits five statuses and a 0-100 score:

| Status | Meaning |
|---|---|
| `valid` | The receiving server answered for this mailbox |
| `invalid` | Bad syntax, no MX, or the server rejected the recipient |
| `risky` | Real but not a clean send: a role account, or a catch-all domain |
| `unknown` | Greylisted, timed out, or a provider that will not answer honestly |
| `disposable` | A throwaway domain |

The product rolls these into four (`deliverable`, `risky`, `unknown`,
`undeliverable`); `disposable` folds into `undeliverable` and keeps its
specificity in the sub-reason. Exports, filters and API payloads see all five.

Two consequences worth knowing before you read a report:

- **With SMTP off, a mailbox that was never probed comes back `unknown`.** Not
  `valid`. That is the honest answer, and rounding it up is what costs sender
  reputation.
- **Gmail, Outlook and Yahoo accept almost any recipient at the door** and
  bounce it later. `smtp_infra/providers.py` knows which providers do this and
  refuses to report a confident `valid` on an acceptance that proves nothing.

There is no `spam_trap` status. Detecting one needs a list of seed addresses,
and a trap only works while its addresses are secret, so no such list exists to
buy. A status no layer can emit reads as a structural zero and invites the
conclusion that a list is trap-free when nothing looked.

## Using it

### One address

```bash
curl -s localhost:8000/v1/verify -H 'content-type: application/json' \
  -d '{"email":"john@gmial.com"}'
# status "unknown", suggested_correction "john@gmail.com"
```

```python
from eve import validate

v = validate("sales@acme.io", enable_dns=False)
print(v.status, v.sub_status, v.score)   # Status.RISKY SubStatus.ROLE_ACCOUNT 50
```

A misspelled domain returns a suggestion rather than a deletion. The row keeps
its place in the file.

### A file

```bash
# 1. upload; the response carries a file_id and the detected columns
curl -s -F "file=@examples/sample_list.csv" localhost:8000/v1/files

# 2. start a job. Only the email column has to be mapped
curl -s localhost:8000/v1/jobs -H 'content-type: application/json' \
  -d '{"file_id":"<file_id>","mapping":{"email":"email"}}'

# 3. poll, then download
curl -s localhost:8000/v1/jobs/<job_id>
curl -s "localhost:8000/v1/jobs/<job_id>/download?segment=cleaned"
```

Three sheets come back:

- **`cleaned`** is your file, every row and every column, with eleven columns
  appended: `email_status`, `sub_status`, `score`, `normalized_email`,
  `suggested_correction`, `is_disposable`, `is_role`, `is_free`, `is_catch_all`,
  `mx_found`, `duplicate_of`.
- **`valid`** is the rows worth sending to.
- **`removed`** is what came out, with the reason each row came out.

`valid` and `removed` partition the input. The engine only needs to know which
column holds the address; everything else is carried through untouched, down to
the delimiter and the line endings you uploaded with.

### The web app

Served by the same process, so it cannot fall out of step with the engine it
reports on.

**Dashboard** (volume, verdict mix, recent results) · **Validate** (upload,
preview, map columns, live progress, downloads) · **Single check** (one address,
the full seven-layer trace) · **Contacts** (every address across all jobs,
de-duplicated, expandable to its trace) · **Analytics** · **Exports** ·
**History** · **Settings** · **How it works**.

Filters, sorting, paging and the wizard step all live in the query string, so
every view is linkable and the back button works. A fresh install has no data
and the screens say so: period deltas stay hidden until there is a previous
period, and a chart with one reading says "not enough history" rather than
drawing a spike.

| File | Role |
|---|---|
| `web/views.py` | View-model: store rows to what each screen renders |
| `web/format.py` | Palette, number formatting, SVG path geometry |
| `web/series.py` | Time bucketing for the charts |
| `web/apidocs.py` | The `/docs` reference, built from the app's own OpenAPI document |
| `web/templates/` | One Jinja template per screen |
| `web/static/app.js` | Only what must not round-trip: chart hover, row expansion, selection, job polling |

### The API reference

`/docs` is generated from `app.openapi()` at request time, so a route added to a
router appears on it and a field renamed in `api/schemas.py` is renamed on it.
`/openapi.json` serves the same document for machines.

### Seeing SMTP verdicts locally

Port-25 probing needs egress IPs you do not have on a laptop, and the large
providers lie to probes. Point the engine at a mock mailserver instead:

```bash
python scripts/mock_mailserver.py &                       # mock MX on :2525
EVE_ENABLE_SMTP=true EVE_ENABLE_DNS=false \
  EVE_SMTP_TARGET_HOST=127.0.0.1 EVE_SMTP_TARGET_PORT=2525 \
  uvicorn eve.api.main:app --port 8010
# then run examples/smtp_demo.csv through http://localhost:8010
```

`alice@acme-demo.com` is valid, `ghost@acme-demo.com` invalid,
`info@acme-demo.com` risky (role), `*@catchall-demo.com` risky (catch-all).

## Architecture

- **Two streaming passes, dedupe by address and by domain.** A 1M-row file with
  50-200k unique domains resolves each domain's MX and catch-all state once.
  Cost is dominated by unique addresses, not rows.
- **Backends are configuration, not code.** Local disk, in-process KV and SQLite
  are the defaults. Set `EVE_S3_*`, `EVE_REDIS_URL` or `EVE_WORKSPACE_DB_URL`
  and the process resolves S3/R2, Redis and Postgres through the same
  interfaces. Every infra setting is empty by default, because a placeholder
  like `redis://localhost:6379` cannot be told apart from a real value. The API
  logs which backend each subsystem resolved to at startup.
- **One workspace per row.** `jobs` and `addresses` both carry `workspace_id`,
  and address identity is `(workspace_id, email)`, so de-duplication happens
  within a tenant. `tenancy.current_workspace_id` is a request-scoped lookup
  resolved from the session or API key.
- **Alembic owns the schema.** There is no `create_all`. A fresh SQLite file and
  a year-old Postgres reach head by replaying the same revisions, which run on
  startup.
- **The SMTP subsystem** (`smtp_infra/`) composes `prober` (aiosmtplib, never
  DATA), `providers` (per-provider heuristics), `rate_limiter` (per-MX token
  bucket), `ip_pool` (reputation, rotation, cooldown, warm-up) and `blacklist`
  (DNSBL monitoring) behind `service.SmtpService`.
- **The DNSBL monitor is running, not merely available.** Every probing process
  scans its own egress IPs on a loop while SMTP is on. A listing pulls the IP
  from rotation and logs at `ERROR`, because a listed IP invalidates every
  verdict it produced. With no `EVE_SMTP_EGRESS_IPS` the scan does not start:
  probes then leave on the OS default route, which rotation cannot avoid.

The reasoning behind the UI and the taxonomy is logged in
[`DESIGN_NOTES.md`](DESIGN_NOTES.md).

## Performance

```bash
python scripts/soak.py                            # 1,000,000 synthetic rows
python scripts/soak.py --rows 200000 --unique-ratio 0.5
```

The soak runs the real pipeline with DNS and SMTP off. Leaving the network out
keeps the number about this process rather than someone else's resolver.

On a laptop, 1M rows / 798k unique / 150k domains: **~318s, ~3,100 rows/sec,
peak RSS ~1.4 GB**. Budget roughly 1.5 KB per unique address. The same 1M rows
at 100k unique finish in ~168s using ~360 MB.

The soak surfaced two defects, both since fixed: an uncached typo comparison
that was 91% of `validate()` (678 to 3,335 rows/sec on a 50k file, with every
suggestion unchanged), and output CSVs buffered whole in memory. Details in
`DESIGN_NOTES.md`.

## Running it

### Docker

```bash
docker compose up --build      # api + worker + postgres + redis + minio
```

This exercises the shared-backend paths that `make run` does not. It is a local
test rig, not a deployment: it reads `.env.example` (auth off, rate limiting
off) and binds Postgres, Redis and MinIO to `0.0.0.0` with default passwords.

### Production

```bash
cp .env.production.example .env && $EDITOR .env
docker compose -f docker-compose.prod.yml up -d --build
```

`docker-compose.prod.yml` reads `.env`, gives the internal services no host
ports, and fails the deploy on a missing secret rather than standing up
`minioadmin`.

`SITE_DOMAIN` (`validrow.inboxrow.com`) is served from one box. The worker has
to sit on the port-25-capable host anyway, so splitting the API onto a PaaS
would mean paying twice for a split nothing needs at this size.

Caddy is a functional requirement rather than hardening.
`EVE_SESSION_COOKIE_SECURE` defaults to true, and a browser silently discards a
Secure cookie sent over plain HTTP, which presents as a login that does nothing.
Caddy obtains and renews the certificate for `SITE_DOMAIN`.

After the first deploy, read the startup log once. It names the backend each
subsystem resolved to and warns about every setting that is fine locally and
wrong in production: auth off, rate limiting off, SQLite, local disk, a
placeholder SMTP identity.

### Workers

Bulk jobs run wherever `EVE_QUEUE_BACKEND` resolves.

| | in-process (`inline`) | worker (`arq`) |
|---|---|---|
| When | no `EVE_REDIS_URL` | Redis configured, `worker` extra installed |
| Survives an API restart | no, the run is orphaned | yes |
| Per-MX rate limit | per process | shared across workers |
| Probes originate from | whichever box served the upload | the worker hosts |

```bash
make worker
```

Run the worker on the port-25-capable egress hosts. Without Redis, every worker
gets a full probe budget against the same provider, which is the fast way to get
an IP blocked.

### Migrations

The app migrates to head on startup, so there is usually nothing to run.

```bash
make migrate                      # as a separate deploy step
make migration m="add credits"    # new revision
```

That default suits a long-lived process: one startup, one migration. It is wrong
where a platform starts a fresh process per request, because the three stores
each replay Alembic against a remote database on every cold start, concurrently
with every other cold start. Set `EVE_RUN_MIGRATIONS_ON_STARTUP=false` there and
run `make migrate` once per deploy. Only the startup path is gated; the CLI
migrates regardless, which is what makes turning it off safe.

Revisions 0001-0003 tolerate a database that predates Alembic. Write anything
from 0004 on strictly.

## Accounts and API keys

Auth is off by default (`EVE_REQUIRE_AUTH=false`). Outside `local`, the startup
warning says plainly that anyone who can reach the API has full access with no
credential.

```bash
EVE_REQUIRE_AUTH=true uvicorn eve.api.main:app --port 8000
```

The first visit goes to `/signup`, because a fresh install has nobody to sign in
as. The first account adopts `EVE_WORKSPACE_ID`, so an engine that has been
running without auth keeps every job and address it already has. Accounts after
it get their own workspace. `EVE_OPEN_SIGNUP=false` closes registration after
that first account.

Two credentials, one identity. The web app uses a session cookie (`HttpOnly`,
`SameSite=Lax`, `Secure` unless turned off); signing out ends the session
server-side, so a copied token dies with it. Scripts send a key:

```bash
curl -s localhost:8000/v1/verify -H "X-API-Key: eve_..." \
  -H 'content-type: application/json' -d '{"email":"john@acme.io"}'
```

Keys are created and revoked on Settings. The plaintext is shown once; only its
SHA-256 is stored. A revoked key is kept rather than deleted, so a key seen in a
log stays identifiable. Rate limiting counts the key when one is used, not the
IP: two keys behind one office NAT are two callers, and one key across a fleet
of workers is one.

Passwords use scrypt where the build exposes it and PBKDF2-HMAC-SHA256 at 600k
iterations where it does not. The scheme is recorded in the hash and upgraded on
the owner's next login.

Not built: password reset and email verification, both of which need a way to
send mail. Until then an operator resets a password against the `users` table.

## Greylisting

A `4xx` reply defers rather than answers, which is what greylisting does to a
stranger. Settling there would report `unknown` for mail that would have been
delivered, so the address is queued for a re-probe (900s, 1800s, 3600s by
default) and only settles `unknown` once the attempts run out.

Greylisters clear in minutes, so the retry happens after the run. The durable
record is a `reprobes` row rather than a timer: a process that dies mid-wait
loses nothing, because the next process to poll finds the row still due. This
works on the inline backend as well as on arq.

A cleared retry updates the address row and nothing else. A job's CSVs are a
snapshot taken when it finished, so Contacts, Dashboard and Analytics show the
cleared verdict while the export still says what was true when it was written.

## Webhooks

Pass `webhook_url` when creating a job and a callback is POSTed when it
finishes, including on failure. Set `EVE_WEBHOOK_SECRET` and verify:

```
X-Eve-Signature: sha256=HMAC-SHA256(secret, "<X-Eve-Timestamp>.<raw body>")
```

Delivery retries with backoff on 5xx, 429 and network errors, and gives up on
other 4xx. With Redis it is a queued task and survives a restart; without it, a
background task in the API process that does not.

## Layout

```
src/eve/
  layers/       syntax · normalize · typo · dns_mx · classify · smtp (seam)
  smtp_infra/   prober · providers · rate_limiter · ip_pool · blacklist · greylist · service
  jobs/         models · store · csv_io · pipeline · queue · worker
  web/          views · format · series · apidocs · templates · static
  api/          FastAPI routes: /health /v1/verify /v1/files /v1/jobs /v1/addresses
  data/         disposable · roles · free · top_domains lists
  migrations/   Alembic revisions
  engine.py     validate() orchestrator and scoring
  verdict.py    Status · SubStatus · PrimaryVerdict
  addresses.py  the workspace read-model every screen queries
  auth.py       accounts · sessions · API keys
  tenancy.py    request-scoped workspace_id
  reprobe.py    deferred retries for greylisted addresses
  webhooks.py   signed job-completion callbacks
  storage.py    ObjectStore (local + S3)      kv.py  KV (memory + Redis)
tests/          per-layer · pipeline e2e · SMTP integration (aiosmtpd) · SQL store · web screens
scripts/        mock_mailserver.py · soak.py · refresh_disposable.py
```

## Configuration

Environment variables, prefix `EVE_`. See `.env.example` for the full list,
including the SMTP probe identity that must be set before enabling
`EVE_ENABLE_SMTP`.

### Engine

| Var | Default | Meaning |
|---|---|---|
| `ENABLE_DNS` | `true` | Perform live MX lookups |
| `ENABLE_SMTP` | `false` | SMTP mailbox probe. Needs port-25 egress |
| `DNS_TIMEOUT` | `5.0` | Resolver timeout, seconds |
| `CHUNK_SIZE` | `500` | Addresses per verify chunk |
| `VERIFY_CONCURRENCY` | `50` | Max concurrent mailbox probes |
| `SMTP_EGRESS_IPS` | `""` | Comma-separated port-25 source IPs. Empty uses the OS default route |
| `PER_MX_RATE` | `5.0` | Probes per second per destination MX |
| `IP_WARMUP_DAILY_CAP` | `50` | Per-IP daily probes while warming |
| `DNSBL_ENABLED` | `true` | Scan egress IPs for blacklist listings |
| `DNSBL_ZONES` | `""` | Comma-separated. Empty uses Spamhaus, Barracuda, SpamCop |
| `DNSBL_INTERVAL_SECONDS` | `3600` | Seconds between scans |
| `REPROBE_ENABLED` | `true` | Retry greylisted addresses |
| `REPROBE_MAX_ATTEMPTS` | `3` | Retries before settling `unknown` |
| `REPROBE_DELAYS` | `900,1800,3600` | Seconds before each retry |
| `REPROBE_POLL_SECONDS` | `60` | How often a process looks for due retries |

### Infrastructure

Empty means "use the local default", so these are also the switches that turn
each shared backend on.

| Var | Default | Meaning |
|---|---|---|
| `WORKSPACE_DB_URL` | `""` | Postgres URL. Empty uses a SQLite file |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` | Bring the schema to head on boot. Set `false` where a fresh process starts per request, and run `alembic upgrade head` as a deploy step instead |
| `WORKSPACE_ID` | `default` | Which workspace this process reads and writes |
| `S3_BUCKET` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `""` | All three switch storage to S3/R2 |
| `S3_ENDPOINT` | `""` | Blank for AWS. Set for R2, MinIO or Spaces |
| `REDIS_URL` | `""` | Job queue and shared per-MX rate limiter |
| `QUEUE_BACKEND` | `auto` | `auto`, `inline` or `arq` |
| `MAX_UPLOAD_BYTES` | `104857600` | Rejected with 413 above this |
| `RATE_LIMIT_PER_MINUTE` | `0` | Per key or IP. `0` disables |
| `CORS_ORIGINS` | `""` | Comma-separated. Empty is same-origin only |
| `SENTRY_DSN` | `""` | Needs the `sentry` extra |

### Accounts

| Var | Default | Meaning |
|---|---|---|
| `REQUIRE_AUTH` | `false` | Gate the app and `/v1` behind an account |
| `OPEN_SIGNUP` | `false` | `false` means only the first account may register |
| `SESSION_TTL_SECONDS` | `1209600` | Session lifetime, 14 days |
| `SESSION_COOKIE_SECURE` | `true` | Never send the cookie over plain HTTP |
| `WEBHOOK_SECRET` | `""` | HMAC signing key. Empty means unsigned |
| `WORKSPACE_NAME` / `WORKSPACE_EMAIL` | `""` | Shown in the sidebar when signed out |
