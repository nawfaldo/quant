"""Canon (`BOOK`) realised over one window: combined + per sleeve, YTD, monthly
and weekly (each % against the balance the period OPENED with).

    MC_START=2026-01-01 MC_END=2026-09-29 BOOK_NAME=ytd py -m sandbox.research._canon_ytd
"""
import platform

platform._wmi = None

import bisect
import os
from collections import defaultdict
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs

os.environ.setdefault("BOOK_KEYS", ",".join(ecs.BOOK))
from sandbox.research import _mc_books as mb                    # noqa: E402
from sandbox.research import exness_combined_montecarlo as mc   # noqa: E402

mb.arm()
mc.prepare = mb.prepare
state = mc.load()
mc.pin_external_window()
lo, hi = ecs.ef.IS_END, ecs.ef.OOS_END
book = mc.run_book(state, lo=lo, hi=hi)
init = book["initial"]
curve = sorted(book["curve"])
stamps = [s for s, _ in curve]


def eq_at(ts):
    i = bisect.bisect_right(stamps, ts) - 1
    return curve[i][1] if i >= 0 else init


def day(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


last = max(t["exit_ts"] for t in book["settled"]) if book["settled"] else lo
print(f"\nCANON {len(state['members'])} sleeves, ${init:.0f}, live fills, "
      f"{day(lo):%Y-%m-%d} .. {day(hi - 1):%Y-%m-%d} (last exit {day(last):%Y-%m-%d %H:%M})")
print(f"YTD {book['return_pct']:+,.2f}%   ${init:,.0f} -> ${book['final']:,.2f}   "
      f"MTM dd {book['mtm_dd_pct']:.2f}%   closed dd {book['max_dd_pct']:.2f}%   "
      f"{book['trades']} trades")
for symbol, bars in sorted(state["bars_by"].items()):
    print(f"  data {symbol:7s} last bar {day(bars[-1][0]):%Y-%m-%d %H:%M}")

# months
months = []
y, m = day(lo).year, day(lo).month
while True:
    start = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())
    if start >= hi:
        break
    y2, m2 = (y + (m == 12), 1 if m == 12 else m + 1)
    end = min(int(datetime(y2, m2, 1, tzinfo=timezone.utc).timestamp()), hi)
    s = init if start <= lo else eq_at(start - 1)
    e = eq_at(end - 1)
    months.append((f"{y}-{m:02d}", s, e, start, end))
    y, m = y2, m2

by_month = defaultdict(lambda: defaultdict(float))
count = defaultdict(int)
for p in book["settled"]:
    name = p["sleeve"]
    month = f"{day(p['exit_ts']):%Y-%m}"
    by_month[name][month] += p["pnl"]
    count[name] += 1

print(f"\nMONTHLY (combined, % of month-open balance)")
print(f"{'month':<9}{'open $':>11}{'close $':>11}{'month %':>10}")
for label, s, e, *_ in months:
    print(f"{label:<9}{s:>11,.2f}{e:>11,.2f}{100 * (e / s - 1):>+9.2f}%")

labels = [label for label, *_ in months]
print(f"\nPER SLEEVE (P&L $ by exit month, YTD, trades)")
print(f"{'sleeve':<34}" + "".join(f"{l[5:]:>8}" for l in labels) + f"{'YTD $':>10}{'n':>5}")
rows = sorted(by_month, key=lambda n: -sum(by_month[n].values()))
for name in rows:
    tot = sum(by_month[name].values())
    print(f"{name:<34}" + "".join(f"{by_month[name].get(l, 0.0):>+8.0f}" for l in labels)
          + f"{tot:>+10.2f}{count[name]:>5}")
print(f"{'BOOK':<34}" + "".join(f"{sum(by_month[n].get(l, 0.0) for n in rows):>+8.0f}" for l in labels)
      + f"{sum(sum(v.values()) for v in by_month.values()):>+10.2f}{sum(count.values()):>5}")

print(f"\nWEEKLY (combined, % of week-open balance)")
d = lo // 86400
start = (d - (d + 3) % 7) * 86400
up = down = 0
while start < hi:
    s = init if start <= lo else eq_at(start - 1)
    e = eq_at(min(start + 7 * 86400, hi) - 1)
    r = 100 * (e / s - 1)
    up += r > 0
    down += r < 0
    print(f"{day(start):%Y-%m-%d}  {s:>10,.2f} -> {e:>10,.2f}  {r:>+7.2f}%")
    start += 7 * 86400
print(f"weeks up {up}, down {down}")
