"""Radicale configuration shared by the DAV server and the sync engine."""
from __future__ import annotations

from excense.config import Settings


def build_radicale_config(settings: Settings) -> str:
    """Write data/radicale.conf and return its path."""
    settings.ensure_dirs()
    text = f"""[server]
hosts = {settings.host}:{settings.port}
max_connections = 20

[auth]
type = htpasswd
htpasswd_filename = {settings.passwd_path}
htpasswd_encryption = bcrypt

[rights]
type = owner_only

[storage]
type = multifilesystem
filesystem_folder = {settings.radicale_dir}

[logging]
level = warning

[web]
type = internal
"""
    settings.radicale_conf.write_text(text)
    return str(settings.radicale_conf)