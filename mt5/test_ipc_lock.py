from __future__ import annotations

import os
import threading
import time
import unittest

from ipc_lock import synchronized_mt5


class _FakeMt5:
    CONSTANT = 42

    def __init__(self, state: dict[str, int], guard: threading.Lock) -> None:
        self.state = state
        self.guard = guard

    def call(self) -> None:
        with self.guard:
            self.state["active"] += 1
            self.state["maximum"] = max(
                self.state["maximum"], self.state["active"]
            )
        time.sleep(0.05)
        with self.guard:
            self.state["active"] -= 1


@unittest.skipUnless(os.name == "nt", "MetaTrader5 IPC is Windows-only")
class SynchronizedMt5Tests(unittest.TestCase):
    def test_two_clients_cannot_overlap_calls(self) -> None:
        state = {"active": 0, "maximum": 0}
        guard = threading.Lock()
        clients = [
            synchronized_mt5(_FakeMt5(state, guard)),
            synchronized_mt5(_FakeMt5(state, guard)),
        ]
        threads = [threading.Thread(target=client.call) for client in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(state["maximum"], 1)

    def test_constants_pass_through_without_wrapping(self) -> None:
        client = synchronized_mt5(_FakeMt5({}, threading.Lock()))
        self.assertEqual(client.CONSTANT, 42)


if __name__ == "__main__":
    unittest.main()
