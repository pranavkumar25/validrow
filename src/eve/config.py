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
    # The app brings the database to head when it boots, which is right for a
    # long-lived process: one startup, one migration, and a deploy needs no
    # separate step. It is wrong for a platform that starts a fresh process per
    # request. Three stores each replay Alembic against a remote database on
    # every cold start, concurrently with every other cold start, which is slow
    # where it is not a lock fight. Set this false there and run
    # `alembic upgrade head` once as a deploy step instead.
    run_migrations_on_startup: bool = True

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

    # DNSBL monitoring of the egress IPs. A listing poisons every result from
    # that IP, so the monitor pulls it from rotation and logs at ERROR. It only
    # runs when SMTP is enabled *and* EVE_SMTP_EGRESS_IPS names IPs to watch:
    # an empty pool means probes leave on the OS default route, which is not an
    # address this process is entitled to cool down.
    dnsbl_enabled: bool = True
    dnsbl_zones: str = ""  # comma-separated; empty = the built-in zone list
    dnsbl_interval_seconds: float = 3600.0

    # Deferred re-probes for greylisted addresses. A 4xx is the receiver
    # deferring us, not a verdict, so the address is retried on this schedule
    # and only settles `unknown` once the attempts run out. The delays are in
    # minutes for a reason: a greylister that cleared in seconds would not be
    # doing its job, so a same-run retry would just collect a second 4xx.
    reprobe_enabled: bool = True
    reprobe_max_attempts: int = 3
    reprobe_delays: str = "900,1800,3600"  # seconds before each retry
    reprobe_poll_seconds: float = 60.0  # how often a process looks for due work
    reprobe_batch: int = 50  # addresses re-probed per sweep

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

    # --- Auth ------------------------------------------------------------
    # Off by default, and warned about outside `local` — the same shape as
    # rate limiting, and for the same reason: turning it on breaks every
    # existing caller, so the default cannot be the safe-for-production one
    # without breaking every existing dev loop instead. The startup warning is
    # what stops "off" from being silent.
    require_auth: bool = False
    # When auth is on and this is off, only the *first* account can be created
    # — the bootstrap for a self-hosted install. Turn it on to let anyone
    # register, which is what a public free tier needs.
    open_signup: bool = False
    session_ttl_seconds: float = 60 * 60 * 24 * 14  # 14 days
    session_cookie: str = "vr_session"
    # Set false only if something in front of this terminates TLS and you are
    # certain the cookie never crosses plain HTTP.
    session_cookie_secure: bool = True

    # --- Landing page offer ----------------------------------------------
    # The only two numbers on the public page that are a promise rather than a
    # measurement, so they live in configuration where they can be changed
    # without a deploy of new copy. Nothing meters usage per account yet
    # (see PRA-71), so the monthly figure is a stated intent, not something the
    # engine currently enforces.
    free_monthly_addresses: int = 10_000
    founding_accounts: int = 100

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
    def smtp_egress_ip_list(self) -> list[str]:
        return [ip.strip() for ip in self.smtp_egress_ips.split(",") if ip.strip()]

    @property
    def reprobe_delay_list(self) -> list[int]:
        out = []
        for part in self.reprobe_delays.split(","):
            part = part.strip()
            if part:
                try:
                    out.append(int(float(part)))
                except ValueError:
                    continue
        return out

    @property
    def dnsbl_zone_list(self) -> list[str]:
        """Configured zones, or ``[]`` meaning "use the built-in list"."""
        return [z.strip() for z in self.dnsbl_zones.split(",") if z.strip()]

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
