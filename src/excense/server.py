"""Run the Radicale CalDAV/CardDAV server against the excense store."""
from __future__ import annotations

import subprocess
import sys

from excense.config import Settings
from excense.radconf import build_radicale_config


def serve(settings: Settings) -> int:
    conf = build_radicale_config(settings)
    return subprocess.call([sys.executable, "-m", "radicale", "--config", conf])