"""`bridge.hand_deal` retries a broken window call at once, and never sends twice.

    py -3 mt5/test_hand_retry.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import win32gui

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge  # noqa: E402

INVALID = win32gui.error(1400, "PostMessage", "Invalid window handle.")


class HandRetry(unittest.TestCase):
    def setUp(self):
        for target, value in (("log", lambda *a, **k: None),):
            patcher = mock.patch.object(bridge, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(bridge.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_window_error_is_retried_in_the_same_command(self):
        once = mock.Mock(side_effect=[INVALID, (0.8217, 555)])
        with mock.patch.object(bridge, "_hand_deal_once", once), \
                mock.patch.object(bridge, "hand_recover_close", return_value=None):
            self.assertEqual(bridge.hand_deal("USDCHF", 0, 0.12, "", 174070831), (0.8217, 555))
        self.assertEqual(once.call_count, 2)

    def test_a_close_that_landed_is_not_sent_again(self):
        once = mock.Mock(side_effect=INVALID)
        with mock.patch.object(bridge, "_hand_deal_once", once), \
                mock.patch.object(bridge, "hand_recover_close", return_value=(0.8217, 777)):
            self.assertEqual(bridge.hand_deal("USDCHF", 0, 0.12, "", 174070831), (0.8217, 777))
        self.assertEqual(once.call_count, 1)

    def test_an_open_that_landed_is_not_sent_again(self):
        position = mock.Mock(price_open=1.1204, ticket=888)
        once = mock.Mock(side_effect=INVALID)
        with mock.patch.object(bridge, "_hand_deal_once", once), \
                mock.patch.object(bridge, "hand_recover_open", return_value=position):
            self.assertEqual(bridge.hand_deal("EURUSD", 0, 0.13, "march:609", 0), (1.1204, 888))
        self.assertEqual(once.call_count, 1)

    def test_gives_up_after_the_last_attempt(self):
        once = mock.Mock(side_effect=INVALID)
        with mock.patch.object(bridge, "_hand_deal_once", once), \
                mock.patch.object(bridge, "hand_recover_close", return_value=None):
            with self.assertRaises(bridge.BridgeError):
                bridge.hand_deal("USDCHF", 0, 0.12, "", 174070831)
        self.assertEqual(once.call_count, bridge.HAND_WINDOW_ATTEMPTS)

    def test_the_brokers_refusal_is_read_from_the_journal(self):
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as root:
            logs = Path(root) / "logs"
            logs.mkdir()
            (logs / f"{datetime.now():%Y%m%d}.log").write_text(
                "JE\t0\t03:59:00.977\tTrades\t'1': market buy 0.12 USDCHF, close #174292518 sell 0.12 USDCHF 0.82481\n"
                "IK\t2\t03:59:01.329\tTrades\t'1': failed market buy 0.12 USDCHF, close #174292518 sell 0.12 "
                "USDCHF 0.82481 [Market closed]\n", encoding="utf-16")
            with mock.patch.object(bridge.mt5, "terminal_info", return_value=mock.Mock(data_path=root)):
                self.assertEqual(bridge.journal_refusal(174292518, "USDCHF"), "[Market closed]")
                self.assertIsNone(bridge.journal_refusal(999, "USDCHF"))


if __name__ == "__main__":
    unittest.main()
