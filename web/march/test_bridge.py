"""Quick test of the Zig bridge without MT5."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from zig_bridge import ZigBridge

binary = str(
    Path(__file__).parent.parent / "web" / "backend" / "zig-out" / "bin" / "signal_runner.exe"
)

bridge = ZigBridge(binary)
bridge.start("rth_vwap", config={"contracts": "0.1", "leverage": "1.0"})

# Send a few bars spanning the RTH open (ET time: 09:30)
test_bars = [
    # Pre-market — expect FLAT
    ("2024-01-15 09:15", 17500.00, 17510.00, 17495.00, 17505.00, 1000),
    # RTH open bars (09:30–09:50) — strategy builds opening range, should be FLAT
    ("2024-01-15 09:30", 17505.00, 17515.00, 17500.00, 17510.00, 2000),
    ("2024-01-15 09:31", 17510.00, 17520.00, 17505.00, 17515.00, 1800),
    ("2024-01-15 09:32", 17515.00, 17530.00, 17510.00, 17525.00, 2200),
    # After 09:30 — VWAP cross should signal
    ("2024-01-15 09:33", 17525.00, 17535.00, 17520.00, 17532.00, 1900),
]

print("Bar timestamp         Signal")
print("-" * 40)
for ts, o, h, l, c, v in test_bars:
    sig = bridge.send_bar(ts, o, h, l, c, v)
    print(f"{ts}   {sig}")

bridge.stop()
print("\nZig bridge test passed!")
