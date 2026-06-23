"""Configuration loader for the march trading bot (API mode)."""

import os
from pathlib import Path
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


@dataclass
class Config:
    # MT5 connection
    mt5_login:    int   = int(os.getenv("MT5_LOGIN", "0"))
    mt5_password: str   = os.getenv("MT5_PASSWORD", "")
    mt5_server:   str   = os.getenv("MT5_SERVER", "")

    # Trading
    symbol:       str   = os.getenv("SYMBOL", "NAS100")
    volume:       float = float(os.getenv("VOLUME", "0.1"))
    magic_number: int   = int(os.getenv("MAGIC_NUMBER", "20240101"))

    # API endpoints
    zig_api_url:     str = os.getenv("ZIG_API_URL", "http://127.0.0.1:4000")
    python_api_port: int = int(os.getenv("PYTHON_API_PORT", "5001"))

    # Misc
    timezone: str  = os.getenv("TIMEZONE", "America/New_York")
    dry_run:  bool = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")


config = Config()
