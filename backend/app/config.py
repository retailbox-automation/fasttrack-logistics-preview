from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Zeabur auto-injects POSTGRES_CONNECTION_STRING via service reference
    # DATABASE_URL is set in service env vars as ${POSTGRES_CONNECTION_STRING}
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"

    # CORS — allow GitHub Pages frontend
    cors_origins: str = "https://retailbox-automation.github.io,http://localhost:8000,http://localhost:5500"

    # API config
    api_title: str = "Fast Track Platform API"
    api_version: str = "0.1.0"

    # Auth — Phase 1: shared password gate (Andrés/team logs in with one password)
    # Override both via Zeabur env vars for production.
    auth_password: str = "fasttrack-dev-2026"
    jwt_secret: str = "change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24 * 14  # 2 weeks — long-lived for daily ops use

    # Microsoft Graph (Outlook/M365) — app-only, read-only mail. Set via Zeabur env.
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_graph_mailboxes: str = ""  # comma-separated mailbox addresses to ingest
    email_sync_interval_seconds: int = 300  # background auto-sync cadence (0 = disabled)
    email_sync_top: int = 25  # messages pulled per mailbox per cycle

    # Weekly ops report auto-generation (Stage 4.1)
    weekly_report_auto: bool = True                     # background scheduler on/off
    weekly_report_check_seconds: int = 6 * 3600         # how often the scheduler wakes to check
    weekly_report_recipients: str = ""                  # comma-separated emails (empty → snapshot only, no email)

    # Outbound email (password reset + notifications). Set SMTP_* via Zeabur env to enable
    # delivery; if unset, messages are logged (so the reset flow works + is retrievable).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""            # From address (defaults to smtp_user)
    smtp_use_tls: bool = True
    app_base_url: str = "https://ftmsc1.zeabur.app"  # for building reset links
    reset_token_ttl_minutes: int = 60

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def graph_mailbox_list(self) -> list[str]:
        return [m.strip() for m in self.ms_graph_mailboxes.split(",") if m.strip()]

    @property
    def weekly_report_recipient_list(self) -> list[str]:
        return [m.strip() for m in self.weekly_report_recipients.split(",") if m.strip()]


settings = Settings()
