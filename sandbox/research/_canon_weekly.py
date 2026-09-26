"""Realised canon (BOOK) on 2025-01-01..CANON_DATA_END: years, every week, every sleeve.

A week's % is against the equity the week OPENED with, so compounding does not
inflate later weeks.

    py -3 -m sandbox.research._canon_weekly
"""
import os
import platform
platform._wmi = None
import bisect
from collections import defaultdict
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
#: `CANON_END=2026-09-26` (exclusive) runs past the sealed data end.
if os.environ.get("CANON_END"):
    ecs.CANON_DATA_END = os.environ["CANON_END"]
    ecs.ef.OOS_END = int(datetime.fromisoformat(os.environ["CANON_END"])
                         .replace(tzinfo=timezone.utc).timestamp())
os.environ["BOOK_KEYS"] = ",".join(ecs.BOOK)
os.environ.setdefault("BOOK_NAME", "canon_weekly")
from sandbox.research import _mc_books as mb
from sandbox.research import exness_combined_montecarlo as mc

mb.arm()
state = mb.prepare()
mc.pin_external_window()
book = mc.run_book(state)
init = book["initial"]
curve = sorted(book["curve"])
stamps = [s for s, _ in curve]


def eq_at(ts):
    i = bisect.bisect_right(stamps, ts) - 1
    return curve[i][1] if i >= 0 else init


day = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
lo = min(stamps)
first_monday = (lo // 86400) - ((lo // 86400 + 3) % 7)
end = max(stamps)
print(f"\nCANON {len(ecs.BOOK)} sleeves, ${init:.0f} start, live fills, realised, "
      f"{day(lo)} .. {day(end)}")
print(f"2025-26: {book['return_pct']:+,.1f}%  closed dd {book['max_dd_pct']:.2f}%  "
      f"MTM dd {book['mtm_dd_pct']:.2f}%  {book['trades']} trades  final ${book['final']:,.0f}")

y26 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
e25, e26 = eq_at(y26 - 1), book["final"]
print(f"2025:    {100 * (e25 / init - 1):+,.1f}%   ${init:,.0f} -> ${e25:,.0f}")
print(f"2026:    {100 * (e26 / e25 - 1):+,.1f}%   ${e25:,.0f} -> ${e26:,.0f}  (to {day(end)})")

# per sleeve, by year of exit
by = defaultdict(lambda: defaultdict(float))
cnt = defaultdict(int)
for p in book["settled"]:
    name = p.get("sleeve") or f"{p.get('symbol')}:{p.get('family')}"
    ts = p.get("exit_ts") or p.get("closed_ts") or p.get("ts")
    pnl = p.get("pnl", p.get("realized", 0.0))
    by[name]["2025" if ts < y26 else "2026"] += pnl
    cnt[name] += 1
print(f"\n{'sleeve':<28}{'2025 $':>10}{'2026 $':>10}{'total $':>10}{'trades':>8}")
for name in sorted(by, key=lambda n: -(by[n]['2025'] + by[n]['2026'])):
    a, b = by[name]["2025"], by[name]["2026"]
    wk = " (weekend)" if name in ecs.WEEKEND_ONLY else ""
    print(f"{name + wk:<28}{a:>+10,.0f}{b:>+10,.0f}{a + b:>+10,.0f}{cnt[name]:>8}")

print(f"\n{'week of':<12}{'start $':>10}{'end $':>10}{'week %':>9}")
d = first_monday
wins = losses = 0
while d * 86400 <= end:
    s, e = eq_at(d * 86400 - 1) if d * 86400 > lo else init, eq_at((d + 7) * 86400 - 1)
    r = 100 * (e / s - 1)
    wins += r > 0
    losses += r < 0
    print(f"{day(d * 86400):<12}{s:>10,.0f}{e:>10,.0f}{r:>+8.2f}%")
    d += 7
print(f"\nweeks up {wins}, down {losses}")
