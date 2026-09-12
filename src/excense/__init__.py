"""excense - Microsoft 365 Graph gateway exposing CalDAV/CardDAV for Android."""

__version__ = "0.1.0"


class ExcenseError(Exception):
    """Base error for expected failures (auth, config, sync)."""