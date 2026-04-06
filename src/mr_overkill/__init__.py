"""Mr. Overkill — AI-powered code review loop."""

try:
    from importlib.metadata import version as _v

    __version__ = _v("overkill")
except Exception:
    __version__ = "0.0.0-dev"
