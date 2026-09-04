"""Logging and error reporting.

Both are configured from settings and both degrade quietly: no Sentry DSN means
no Sentry, not a crash. JSON logging is off by default because a human reading
`make run` output wants lines, not objects — turn it on with ``EVE_LOG_JSON``
wherever something is collecting the logs.
"""
from __future__ import annotations

import json
import logging
import sys

logger = logging.getLogger(__name__)

_configured = False


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for a log collector to parse."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything attached via `logger.info(..., extra={"job_id": ...})`.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message", "asctime", "taskName",
}


def configure_logging(settings=None) -> None:
    """Install a single stderr handler at the configured level. Idempotent."""
    global _configured
    if _configured:
        return

    from eve.config import get_settings

    s = settings or get_settings()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if s.log_json
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, s.log_level.upper(), logging.INFO))
    _configured = True


def init_sentry(settings=None) -> bool:
    """Start Sentry if a DSN is configured. Returns whether it did."""
    from eve.config import get_settings

    s = settings or get_settings()
    if not s.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "EVE_SENTRY_DSN is set but sentry-sdk is not installed; "
            "errors will not be reported. Install with: pip install -e '.[sentry]'"
        )
        return False

    sentry_sdk.init(dsn=s.sentry_dsn, environment=s.env, send_default_pii=False)
    logger.info("sentry initialised (environment=%s)", s.env)
    return True


def warn_about_configuration(settings=None) -> list[str]:
    """Configuration that is fine locally and wrong in production.

    Returned as well as logged so a health check or a test can assert on it
    rather than scraping log output.
    """
    from eve.config import get_settings

    s = settings or get_settings()
    warnings: list[str] = []

    if s.env != "local":
        if not s.rate_limit_per_minute:
            warnings.append(
                "rate limiting is off (EVE_RATE_LIMIT_PER_MINUTE=0) on a non-local "
                "environment — the API will accept unlimited requests per client"
            )
        if not s.workspace_db_url:
            warnings.append(
                "no EVE_WORKSPACE_DB_URL: falling back to a SQLite file on local disk, "
                "which is lost on redeploy and cannot be shared between instances"
            )
        if not s.s3_configured:
            warnings.append(
                "no S3 bucket configured: uploads and result CSVs are written to local "
                "disk, which is lost on redeploy and not visible to other instances"
            )
        if not s.redis_configured:
            warnings.append(
                "no EVE_REDIS_URL: jobs run in the API process and the per-MX rate "
                "limiter is per-process, so multiple instances will each use a full "
                "probe budget against the same provider"
            )

        if not s.require_auth:
            warnings.append(
                "auth is off (EVE_REQUIRE_AUTH=false) on a non-local environment — "
                "anyone who can reach this process gets full access to the "
                f"'{s.workspace_id}' workspace and to /v1, with no credential"
            )

    if s.require_auth and s.open_signup and s.env != "local":
        warnings.append(
            "EVE_OPEN_SIGNUP is on: anyone who can reach the sign-up page can "
            "create an account and start jobs on this engine"
        )

    if s.enable_smtp and s.smtp_identity_is_placeholder:
        warnings.append(
            f"SMTP is enabled but the probe identity is still a placeholder "
            f"(HELO={s.smtp_helo_hostname}, MAIL FROM={s.smtp_mail_from}); most "
            f"receivers will drop the connection. Set EVE_SMTP_HELO_HOSTNAME and "
            f"EVE_SMTP_MAIL_FROM to a real domain with matching PTR and SPF records"
        )

    for w in warnings:
        logger.warning("configuration: %s", w)
    return warnings


def describe_backends(settings=None) -> dict[str, str]:
    """Which backend each subsystem resolved to — logged once at startup.

    Worth saying out loud: every one of these silently falls back to a
    single-process default, and the failure mode is data quietly not being
    where you expect rather than an error.
    """
    from eve.config import get_settings
    from eve.jobs.queue import resolve_backend

    s = settings or get_settings()
    url = s.workspace_db_url
    if not url:
        database = "sqlite (local file, not shared)"
    elif url.startswith("sqlite"):
        database = "sqlite (explicit, not shared)"
    else:
        database = url.split("://", 1)[0]  # driver only — the URL holds a password

    return {
        "database": database,
        "object_store": f"s3:{s.s3_bucket}" if s.s3_configured else "local disk (not shared)",
        "kv": "redis" if s.redis_configured else "in-process (not shared)",
        "queue": resolve_backend(s),
    }
