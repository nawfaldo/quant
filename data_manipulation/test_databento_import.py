import unittest

import pandas as pd

from databento_import import (
    DEPTH_SCHEMA,
    SCHEMA_VERSION,
    TICK_SCHEMA,
    TransformState,
    bookmap_depth_events,
    compact_ticks,
    create_sql,
    manifest_matches,
    new_york_wall_clock_as_utc,
    prepare_source,
    safe_identifier,
)


def sample_source() -> pd.DataFrame:
    rows = []
    for index, (action, side, size) in enumerate(
        [("A", "B", 10), ("T", "A", 3), ("C", "A", 4), ("M", "B", 6)]
    ):
        row = {
            "ts_recv": pd.Timestamp("2026-05-27T13:30:00.100000000Z")
            + pd.Timedelta(index * 100, "ms"),
            "ts_event": pd.Timestamp("2026-05-27T13:30:00.099999900Z")
            + pd.Timedelta(index * 100, "ms"),
            "rtype": 10,
            "publisher_id": 1,
            "instrument_id": 123,
            "action": action,
            "side": side,
            "depth": 0,
            "price": 20_000_250_000_000,
            "size": size,
            "flags": 0,
            "ts_in_delta": 100,
            "sequence": 40 + index,
        }
        for level in range(10):
            row[f"bid_px_{level:02d}"] = 20_000_000_000_000 - level * 250_000_000
            row[f"ask_px_{level:02d}"] = 20_000_250_000_000 + level * 250_000_000
            row[f"bid_sz_{level:02d}"] = 10 + level
            row[f"ask_sz_{level:02d}"] = 20 + level
            row[f"bid_ct_{level:02d}"] = 1 + level
            row[f"ask_ct_{level:02d}"] = 2 + level
        if index >= 1:
            row["ask_sz_00"] = 25
        if index >= 2:
            row["ask_px_09"] = 0
            row["ask_sz_09"] = 0
            row["ask_ct_09"] = 0
        if index >= 3:
            row["bid_sz_00"] = 16
        rows.append(row)
    return pd.DataFrame(rows).set_index("ts_recv")


class BookmapCompatibleTransformationTests(unittest.TestCase):
    def test_designated_timestamp_uses_new_york_wall_clock(self) -> None:
        converted = new_york_wall_clock_as_utc(
            pd.Series([pd.Timestamp("2026-05-27T13:30:00Z")])
        )
        self.assertEqual(
            converted.iloc[0],
            pd.Timestamp("2026-05-27T09:30:00Z"),
        )

    def test_keeps_trade_fields(self) -> None:
        frame = prepare_source(sample_source(), 100)
        ticks = compact_ticks(frame, "day.dbn.zst")
        self.assertEqual(list(ticks.columns), [name for name, _ in TICK_SCHEMA])
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks.iloc[0]["side"], "SELL")
        self.assertEqual(ticks.iloc[0]["price"], 20_000.25)
        self.assertEqual(ticks.iloc[0]["size"], 3)
        self.assertNotIn("ts_recv_ns", ticks.columns)
        self.assertNotIn("ts_event_ns", ticks.columns)
        self.assertEqual(ticks.iloc[0]["best_bid"], 20_000.0)
        self.assertEqual(ticks.iloc[0]["best_ask"], 20_000.25)
        self.assertEqual(ticks.iloc[0]["buy_vol"], 0)
        self.assertEqual(ticks.iloc[0]["sell_vol"], 3)
        self.assertEqual(ticks.iloc[0]["delta"], -3)
        self.assertEqual(ticks.iloc[0]["trade_sequence"], 102)
        self.assertEqual(ticks.iloc[0]["alias"], "NQ:123")
        self.assertEqual(ticks.iloc[0]["stream_id"], "day.dbn.zst:123")
        self.assertEqual(
            ticks.iloc[0]["timestamp"],
            pd.Timestamp("2026-05-27T09:30:00.200000000Z"),
        )

    def test_emits_absolute_level_updates_and_removals(self) -> None:
        frame = prepare_source(sample_source(), 0)
        depth = bookmap_depth_events(frame, "day.dbn.zst", TransformState())
        self.assertEqual(list(depth.columns), [name for name, _ in DEPTH_SCHEMA])
        self.assertEqual(len(depth), 23)
        self.assertNotIn("ts_recv_ns", depth.columns)
        self.assertNotIn("ts_event_ns", depth.columns)
        first = depth.iloc[0]
        self.assertEqual(
            first["timestamp"],
            pd.Timestamp("2026-05-27T09:30:00.100000000Z"),
        )
        removed = depth.loc[
            (depth["side"] == "ASK")
            & (depth["price"] == 20_002.5)
            & (depth["size"] == 0)
        ]
        self.assertEqual(len(removed), 1)
        changed_bid = depth.loc[
            (depth["side"] == "BID")
            & (depth["price"] == 20_000.0)
            & (depth["size"] == 16)
        ]
        self.assertEqual(len(changed_bid), 1)
        self.assertEqual(changed_bid.iloc[0]["price_level"], 80_000)
        self.assertEqual(changed_bid.iloc[0]["size_level"], 16)


class SafetyTests(unittest.TestCase):
    def test_identifier_validation(self) -> None:
        self.assertEqual(safe_identifier("dbento_", "prefix"), "dbento_")
        with self.assertRaises(ValueError):
            safe_identifier("dbento_;drop", "prefix")

    def test_manifest_requires_event_schema_version(self) -> None:
        identity = {"size": 1, "mtime_ns": 2}
        self.assertFalse(manifest_matches(identity, identity))
        self.assertTrue(
            manifest_matches({**identity, "schema_version": SCHEMA_VERSION}, identity)
        )

    def test_depth_schema_matches_bookmap_shape_and_uses_nanoseconds(self) -> None:
        sql = create_sql("dbento_nq_depth", DEPTH_SCHEMA)
        self.assertIn("price_level LONG", sql)
        self.assertIn("size_level LONG", sql)
        self.assertIn("stream_id SYMBOL", sql)
        self.assertIn("timestamp TIMESTAMP_NS", sql)
        self.assertIn("PARTITION BY DAY WAL", sql)


if __name__ == "__main__":
    unittest.main()
