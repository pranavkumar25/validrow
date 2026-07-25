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

    # Infra (unused until wired to real backends; declared so .env validates)
    database_url: str = "postgresql+asyncpg://eve:eve@localhost:5432/eve"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "eve-uploads"


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
