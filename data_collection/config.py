"""Configuration loader for the Yahoo Finance data collection project."""

import os
from pathlib import Path

# Load dotenv if available (following existing pattern)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# QuestDB configurations
QUESTDB_HOST = os.getenv("QUESTDB_HOST", "127.0.0.1")
QUESTDB_HTTP_PORT = int(os.getenv("QUESTDB_HTTP_PORT", "9000"))
QUESTDB_ILP_PORT = int(os.getenv("QUESTDB_ILP_PORT", "9009"))

# Data Collection configurations
TICKER = os.getenv("TICKER", "NQ=F")
BASE_TABLE_NAME = "nq"  # Table prefix: e.g., nq_1m_yf, nq_5m_yf
