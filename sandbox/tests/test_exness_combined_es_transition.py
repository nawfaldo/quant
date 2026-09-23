"""ES source-transition checks for the canonical combined engine.

The three cases are unchanged from the QuestDB version; only what they stand in
for moved. The validator used to issue three SQL queries and these mocked
`sandbox.data.query` in order, which meant the test also encoded the query
ORDER. It now reads the store, so the fakes are a table bound and a column of
Yahoo-tagged timestamps -- the two facts the rule is actually about.
"""

import unittest
from unittest.mock import patch

import numpy

from sandbox.research import exness_combined_strategies as combined

#: Nanoseconds per second, to keep the fixtures readable.
NS = 1_000_000_000


def stamps(*iso_days_and_times):
    """A fake `store.scan` timestamp column, in epoch nanoseconds."""
    from sandbox import parquet_store as store

    return numpy.array(
        [store.to_nanoseconds(value) for value in iso_days_and_times],
        dtype=numpy.int64,
    )


class EsTransitionTests(unittest.TestCase):
    def setUp(self):
        self.saved = combined._ES_TRANSITION
        combined._ES_TRANSITION = None

    def tearDown(self):
        combined._ES_TRANSITION = self.saved

    def run_validator(self, legacy_bounds, yahoo_stamps):
        """`_validate_es_transition` over a faked store.

        `scan` returns `(timestamps, table)` and the validator selects the
        Yahoo rows out of it by the `underlying` column, so the fake marks
        every row as Yahoo and lets the timestamps carry the case.
        """
        rows = len(yahoo_stamps)

        def fake_scan(table, columns=None, start=None, end=None):
            import pyarrow

            return yahoo_stamps, pyarrow.table({"underlying": ["ES=F"] * rows})

        with patch("sandbox.parquet_store.bounds", return_value=legacy_bounds):
            with patch("sandbox.parquet_store.scan", side_effect=fake_scan):
                return combined._validate_es_transition()

    def test_same_day_unique_handoff_is_accepted(self):
        transition = self.run_validator(
            ("2008-12-11", "2026-06-24"),
            stamps("2026-06-24T00:00:00", "2026-08-20T23:30:00"),
        )

        self.assertEqual(transition["transition_day"], "2026-06-24")
        self.assertEqual(transition["yahoo_last"], "2026-08-20T23:30:00.000000Z")

    def test_cross_day_gap_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ES source gap"):
            self.run_validator(
                ("2008-12-11", "2026-06-23"),
                stamps("2026-06-24T00:00:00", "2026-08-20T23:30:00"),
            )

    def test_duplicate_yahoo_timestamp_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "duplicated timestamp"):
            self.run_validator(
                ("2008-12-11", "2026-06-24"),
                stamps(
                    "2026-06-24T00:00:00",
                    "2026-07-01T08:30:00",
                    "2026-07-01T08:30:00",
                    "2026-08-20T23:30:00",
                ),
            )

    def test_missing_yahoo_segment_is_rejected(self):
        """A store with no `ES=F` rows is a failure, not an empty handoff."""
        with self.assertRaisesRegex(RuntimeError, "Yahoo-tagged"):
            self.run_validator(("2008-12-11", "2026-06-24"), stamps())


if __name__ == "__main__":
    unittest.main()
