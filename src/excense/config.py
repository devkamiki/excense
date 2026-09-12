"""Application configuration loaded from environment / .env (prefix EXCENSE_)."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

GRAPH_RESOURCE = "https://graph.microsoft.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXCENSE_", env_file=".env", extra="ignore"
    )

    client_id: str = Field(default="", description="Entra application (client) ID")
    tenant: str = Field(default="common", description="Tenant ID/name or 'common'")
    data_dir: Path = Field(default=Path("./data"), description="State root (tokens, store)")

    graph_api: str = Field(default="v1.0")

    host: str = "0.0.0.0"
    port: int = 5232

    username: str = Field(default="excense", description="DAV account username")
    htpasswd_path: Path | None = None  # default: <data_dir>/auth/htpasswd

    #: Graph delegated permission names (email scopes will join them later).
    delegated_scopes: list[str] = Field(
        default_factory=lambda: [
            "Calendars.ReadWrite",
            "Contacts.ReadWrite",
            "Tasks.ReadWrite",
            "User.Read",
            "offline_access",
        ]
    )

    sync_interval_seconds: int = 300
    calendar_past_days: int = 365
    calendar_future_days: int = 400

    # ------------------------------------------------------------------ derived

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}"

    @property
    def graph_scopes(self) -> list[str]:
        return [
            scope if scope.startswith("https://") else f"{GRAPH_RESOURCE}/{scope}"
            for scope in self.delegated_scopes
        ]

    @property
    def auth_dir(self) -> Path:
        return self.data_dir / "auth"

    @property
    def token_path(self) -> Path:
        return self.auth_dir / "graph_token.json"

    @property
    def passwd_path(self) -> Path:
        return self.htpasswd_path or (self.auth_dir / "htpasswd")

    @property
    def radicale_dir(self) -> Path:
        return self.data_dir / "radicale"

    @property
    def radicale_conf(self) -> Path:
        return self.data_dir / "radicale.conf"

    @property
    def state_db_path(self) -> Path:
        return self.data_dir / "state" / "excense.db"

    @property
    def dav_user(self) -> str:
        return self.username

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.auth_dir, self.radicale_dir, self.state_db_path.parent):
            d.mkdir(parents=True, exist_ok=True)