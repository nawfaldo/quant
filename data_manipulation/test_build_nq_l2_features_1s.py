import unittest

from build_nq_l2_features_1s import (
    Event,
    FeatureAccumulator,
    depth_events,
    timestamp_ns,
    validate_target_table,
)


def depth(second: int, side: str, price: float, size: float, sequence: int = 1) -> Event:
    return Event(second * 1_000_000_000, 0, sequence, "D", side, price, size)


def trade(second: int, side: str, price: float, size: float, sequence: int = 1) -> Event:
    return Event(second * 1_000_000_000, 1, sequence, "T", side, price, size)


class TargetSafetyTests(unittest.TestCase):
    def test_accepts_only_l2_targets(self) -> None:
        self.assertEqual(
            validate_target_table("nq_l2_features_1s"),
            "nq_l2_features_1s",
        )
        for unsafe in (
            "bm_nq_depth",
            "dbento_nq_depth",
            "features_nq",
            "l2_nq_1s",
            "nq_l2_features_1s;drop",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                validate_target_table(unsafe)

    def test_timestamp_parser_preserves_nanoseconds(self) -> None:
        self.assertEqual(
            timestamp_ns("1970-01-01T00:00:01.123456789Z"),
            1_123_456_789,
        )

    def test_same_timestamp_depth_rows_are_sorted_by_sequence(self) -> None:
        rows = iter(
            [
                {
                    "timestamp": "1970-01-01T00:00:01.000000000Z",
                    "side": "BID",
                    "price": "100",
                    "size": "2",
                    "sequence": "2",
                    "stream_id": "s",
                },
                {
                    "timestamp": "1970-01-01T00:00:01.000000000Z",
                    "side": "BID",
                    "price": "100",
                    "size": "1",
                    "sequence": "1",
                    "stream_id": "s",
                },
            ]
        )
        self.assertEqual([event.sequence for event in depth_events(rows)], [1, 2])


class FeatureAccumulatorTests(unittest.TestCase):
    def test_book_features_and_flow_are_directional(self) -> None:
        features = FeatureAccumulator(0.25)
        features.on_event(depth(1, "BID", 100.0, 30.0))
        features.on_event(depth(1, "ASK", 100.25, 10.0))
        features.on_event(trade(1, "BUY", 100.25, 6.0))
        rows = features.on_event(depth(2, "ASK", 100.25, 16.0))

        self.assertEqual(len(rows), 1)
        row = rows[0].values
        self.assertTrue(row["book_valid"])
        self.assertEqual(row["midprice"], 100.125)
        self.assertEqual(row["spread"], 0.25)
        self.assertAlmostEqual(row["top1_imbalance"], 0.5)
        self.assertEqual(row["aggressive_buy_volume"], 6.0)
        self.assertEqual(row["trade_delta"], 6.0)
        self.assertEqual(row["executed_at_ask"], 6.0)

    def test_replenishment_matches_execution_and_same_price_addition(self) -> None:
        features = FeatureAccumulator(0.25)
        features.seed("BID", 100.0, 10.0)
        features.seed("ASK", 100.25, 10.0)
        features.on_event(trade(1, "SELL", 100.0, 8.0))
        features.on_event(depth(1, "BID", 100.0, 16.0))
        row = features.finish()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.values["bid_replenishment"], 6.0)
        self.assertEqual(row.values["ask_replenishment"], 0.0)
        self.assertEqual(row.values["replenishment_score"], 1.0)

    def test_zero_size_removes_a_level_and_counts_cancellation(self) -> None:
        features = FeatureAccumulator(0.25)
        features.seed("BID", 100.0, 10.0)
        features.seed("ASK", 100.25, 10.0)
        features.on_event(depth(1, "BID", 100.0, 0.0))
        row = features.finish()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertFalse(row.values["book_valid"])
        self.assertEqual(row.values["bid_cancel_volume"], 10.0)

    def test_live_flush_emits_only_an_elapsed_bucket(self) -> None:
        features = FeatureAccumulator(0.25)
        features.on_event(depth(10, "BID", 100.0, 10.0))
        features.on_event(depth(10, "ASK", 100.25, 10.0))

        self.assertIsNone(features.flush_completed(10))
        row = features.flush_completed(11)

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.timestamp_ns, 10_000_000_000)
        self.assertIsNone(features.flush_completed(12))


if __name__ == "__main__":
    unittest.main()
