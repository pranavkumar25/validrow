"""Runtime configuration, loaded from environment / .env (Pydantic Settings)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_", env_file=".env", extra="ignore")

    env: str = "local"
    log_level: str = "INFO"

    # Engine feature flags
    enable_dns: bool = True
    enable_smtp: bool = False  # Layer 6/7 — needs the M2 SMTP subsystem.
    dns_timeout: float = 5.0

    # File pipeline (M1)
    local_storage_dir: str = ".eve_storage"
    # Workspace read-model (jobs + every validated address, for the web UI).
    # Empty -> a SQLite file inside local_storage_dir, so a fresh checkout has
    # durable history with nothing to configure.
    workspace_db_url: str = ""

    # Which workspace this process reads and writes. Every row in `jobs` and
    # `addresses` carries it, so two tenants can share one database without
    # their de-duplicated addresses merging into each other. There is no auth
    # yet, so it is declared rather than authenticated — but the *column* is
    # real, which is the part that is expensive to add later.
    workspace_id: str = "default"

    # Who this workspace belongs to, shown in the sidebar. There is no auth yet,
    # so these are declared rather than authenticated: left empty, the UI names
    # the workspace and the engine it is talking to instead of inventing a
    # signed-in person.
    workspace_name: str = ""
    workspace_email: str = ""
    chunk_size: int = 500  # emails per verify chunk
    verify_concurrency: int = 50  # max concurrent mailbox probes

    # SMTP subsystem (M2)
    smtp_port: int = 25
    smtp_timeout: float = 15.0
    smtp_helo_hostname: str = "verifier.local"
    smtp_mail_from: str = "verify@verifier.local"
    smtp_egress_ips: str = ""  # comma-separated source IPs; empty = OS default
    per_mx_rate: float = 5.0  # probes/sec per destination MX host
    ip_warmup_daily_cap: int = 50  # per-IP daily probes while warming
    # Demo/testing: force all SMTP probes to a fixed server instead of real MX.
    # Leave empty in production (probes go to each domain's real MX host).
    smtp_target_host: str = ""
    smtp_target_port: int = 0

    # --- Infra backends -------------------------------------------------
    # Every one of these is *empty by default*, and empty means "use the
    # in-process/on-disk backend". That is deliberate: a placeholder default
    # like "redis://localhost:6379" cannot be told apart from a real setting,
    # so the code could never decide whether Redis was actually configured.
    # Empty-or-set is a question the process can answer.
    redis_url: str = ""
    s3_endpoint: str = ""  # blank for AWS S3; set for R2/MinIO
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = ""
    s3_region: str = ""

    # Bulk jobs: "auto" runs them on arq when Redis is configured and in this
    # process otherwise. "inline" and "arq" force the choice.
    queue_backend: str = "auto"

    # --- API hardening ---------------------------------------------------
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MB
    cors_origins: str = ""  # comma-separated; empty = same-origin only
    # Requests per minute per client IP on the expensive endpoints. 0 disables
    # it, which is right for local dev and wrong for anything public — the API
    # warns at startup when it is off outside `local`.
    rate_limit_per_minute: int = 0

    # --- Webhooks --------------------------------------------------------
    webhook_secret: str = ""  # HMAC-SHA256 signing key; empty = unsigned
    webhook_timeout: float = 10.0
    webhook_max_attempts: int = 5

    # --- Observability ---------------------------------------------------
    sentry_dsn: str = ""
    log_json: bool = False

    # --- Derived -----------------------------------------------------------
    @property
    def s3_configured(self) -> bool:
        """True when object storage should go to S3/R2/MinIO rather than disk."""
        return bool(self.s3_bucket and self.s3_access_key and self.s3_secret_key)

    @property
    def redis_configured(self) -> bool:
        return bool(self.redis_url)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def smtp_identity_is_placeholder(self) -> bool:
        """The shipped HELO/MAIL FROM defaults are not deliverable identities.

        Probing from `verifier.local` gets the connection dropped by most
        receivers, so the API says so at startup rather than letting every
        probe fail with an opaque SMTP error.
        """
        return self.smtp_helo_hostname.endswith(".local") or self.smtp_mail_from.endswith(".local")


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings accessor."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Override the process-wide settings (tests / explicit configuration)."""
    global _settings
    _settings = settings
