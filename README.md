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
In the Streamlit UI, set the sidebar **API base URL** to `http://127.0.0.1:8010`.

### Web UI (Streamlit — pure Python)

A dashboard that talks to the API: drag-drop CSV → map columns → watch progress →
color-coded results → download cleaned / valid / removed.

```bash
pip install -e ".[frontend]"
uvicorn eve.api.main:app --port 8000        # API (terminal 1)
streamlit run frontend/app.py               # UI  (terminal 2) -> http://localhost:8501
```

`frontend/app.py` is ~230 lines of Python, no HTML/JS.

## Architecture notes

- **Streaming + O(unique) memory.** The pipeline makes two streaming passes and
  dedupes emails *and* domains, so a 1M-row file (≈50–200k unique domains)
  resolves each domain's MX and catch-all **once**, cached.
- **Pluggable backends.** Local filesystem storage / in-memory KV + job store are
  the defaults (zero external services). Swap in S3/R2 (`storage.S3ObjectStore`),
  Redis (`kv.RedisKV`), and Postgres (`jobs.store.SqlJobStore`) via the `s3` /
  `redis` / `postgres` extras — same interfaces, no code change.
- **The SMTP moat** (`eve/smtp_infra/`): `prober` (aiosmtplib, never DATA),
  `providers` (per-provider heuristics), `rate_limiter` (per-MX token bucket),
  `ip_pool` (reputation / rotation / cooldown / warm-up), `blacklist` (DNSBL
  monitoring), composed by `service.SmtpService`. Needs port-25-capable egress
  IPs in production (set `EVE_SMTP_EGRESS_IPS`).

## Docker

```bash
docker compose up --build     # api + postgres + redis + minio
```

Jobs run **in-process** in the API today. For horizontal scale-out, enqueue to
arq and run `arq eve.jobs.worker.WorkerSettings` on port-25-capable hosts (needs
the shared Postgres/S3/Redis backends).

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

See `.env.example` for the full list.
