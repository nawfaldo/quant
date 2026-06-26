"""Configuration for the march trading bot (API mode).

MT5 accounts (login / password / server) and the symbol traded per strategy now
live in march.db — managed from the web UI (right sidebar on the March page) and
read here via db.py. They are NO LONGER configured through a .env file.

Only process-wide constants remain below.
"""

from dataclasses import dataclass


@dataclass
class Config:
    # Order tag written on every MT5 deal so we only manage our own positions.
    magic_number: int = 20240101

    # API endpoints.
    zig_api_url:     str = "http://127.0.0.1:4000"
    python_api_port: int = 5001

    # Misc.
    timezone: str = "America/New_York"

    # Dry run — when True, log intended orders but never send them to MT5.
    dry_run: bool = False


config = Config()
