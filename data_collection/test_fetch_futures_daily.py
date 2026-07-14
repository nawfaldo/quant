import unittest
from pathlib import Path

import pandas as pd

from fetch_futures_daily import (
    DEFAULT_REGISTRY,
    load_registry,
    normalize_yahoo,
    select_symbols,
    sync_symbol,
)


class RegistryTests(unittest.TestCase):
    def test_registry_is_the_back_adjusted_es_nq_universe(self):
        symbols = load_registry(Path(DEFAULT_REGISTRY))
        self.assertEqual(["es", "nq"], [symbol.prefix for symbol in symbols])
        self.assertEqual(2, len({symbol.prefix for symbol in symbols}))

    def test_selects_by_prefix_or_ticker(self):
        symbols = load_registry(Path(DEFAULT_REGISTRY))
        selected = select_symbols(symbols, ["NQ", "ES=F"])
        self.assertEqual(["es", "nq"], [symbol.prefix for symbol in selected])


class ExternalSourceTests(unittest.TestCase):
    def test_registry_marks_only_es_and_nq_external(self):
        symbols = load_registry(Path(DEFAULT_REGISTRY))
        external = {s.prefix for s in symbols if s.externally_sourced}
        self.assertEqual({"es", "nq"}, external)

    def test_sync_never_touches_an_external_table(self):
        class ExplodingQuestDB:
            def __getattr__(self, name):
                raise AssertionError(f"QuestDB.{name} called for an external symbol")

        es = next(
            s for s in load_registry(Path(DEFAULT_REGISTRY)) if s.prefix == "es"
        )
        # missing_only=False is the documented "incremental refresh" and is what
        # would otherwise upsert Yahoo front-month bars over back-adjusted ones.
        result = sync_symbol(
            ExplodingQuestDB(), es, overlap_days=7, missing_only=False, dry_run=False
        )
        self.assertEqual("skipped", result.status)
        self.assertEqual(0, result.inserted)
        self.assertEqual(0, result.updated)


class NormalizeTests(unittest.TestCase):
    def test_adjusts_ohlc_and_rejects_invalid_rows(self):
        frame = pd.DataFrame(
            {
                "Open": [10.0, 20.0],
                "High": [12.0, 19.0],
                "Low": [9.0, 18.0],
                "Close": [11.0, 20.0],
                "Adj Close": [22.0, 20.0],
                "Volume": [100, 50],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        normalized, rejected = normalize_yahoo(
            frame, now=pd.Timestamp("2024-01-10 18:00", tz="America/New_York")
        )
        self.assertEqual(1, rejected)
        self.assertEqual(1, len(normalized))
        self.assertEqual(20.0, normalized.iloc[0]["Open"])
        self.assertEqual(22.0, normalized.iloc[0]["Close"])

    def test_excludes_incomplete_current_day(self):
        frame = pd.DataFrame(
            {"Open": [10], "High": [11], "Low": [9], "Close": [10], "Volume": [1]},
            index=pd.to_datetime(["2024-01-03"]),
        )
        normalized, _ = normalize_yahoo(
            frame, now=pd.Timestamp("2024-01-03 16:00", tz="America/New_York")
        )
        self.assertTrue(normalized.empty)


if __name__ == "__main__":
    unittest.main()
