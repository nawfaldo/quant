"""Render the Rust combined-book equity response as a dependency-free SVG."""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html import escape

from sandbox.research.rust_schedule import COMBINED_NAMES

STRATEGIES = list(COMBINED_NAMES)


def request_book(url: str) -> dict:
    body = json.dumps({
        "environmentId": "2", "strategies": STRATEGIES, "symbol": "nq",
        "instrument": "forex", "initialBalance": "400",
        "fromDate": "2025-01-01", "toDate": "2026-08-04", "includeFx": False,
    }).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def day(timestamp: int) -> int:
    return timestamp // 86_400


def stamp(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def render(report: dict, destination: str) -> None:
    initial = float(report["initial_bal"])
    pnl_by_day = defaultdict(float)
    for trade in report["trades"]:
        exit_timestamp = trade.get(
            "xt", trade.get("exit_timestamp", trade.get("exitTimestamp")))
        if exit_timestamp is None:
            raise KeyError("trade has no exit timestamp")
        pnl_by_day[day(int(exit_timestamp))] += float(trade["pnl"])

    start_day, end_day = day(stamp("2025-01-01")), day(stamp("2026-08-04"))
    equity, peak = initial, initial
    curve, underwater = [(start_day, equity)], [(start_day, 0.0)]
    for current in range(start_day, end_day + 1):
        equity += pnl_by_day[current]
        peak = max(peak, equity)
        curve.append((current, equity))
        underwater.append((current, 100.0 * (equity / peak - 1.0)))

    width, height = 1200, 720
    left, right = 92, 1160
    top_a, bottom_a = 92, 470
    top_b, bottom_b = 525, 650
    x_min, x_max = start_day, end_day
    y_min, y_max = min(v for _, v in curve), max(v for _, v in curve)
    dd_min = min(v for _, v in underwater)

    def x(value):
        return left + (value - x_min) / (x_max - x_min) * (right - left)

    def y(value):
        return bottom_a - (value - y_min) / (y_max - y_min) * (bottom_a - top_a)

    def yd(value):
        floor = min(dd_min, -1.0)
        return top_b + (0.0 - value) / (0.0 - floor) * (bottom_b - top_b)

    equity_points = " ".join(f"{x(d):.2f},{y(v):.2f}" for d, v in curve)
    dd_points = " ".join(f"{x(d):.2f},{yd(v):.2f}" for d, v in underwater)
    mtm_peak = day(stamp(report["max_drawdown_peak_date"]))
    mtm_trough = day(stamp(report["max_drawdown_trough_date"]))
    shade_x, shade_w = x(mtm_peak), max(2.0, x(mtm_trough) - x(mtm_peak))

    ticks = ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01",
             "2026-01-01", "2026-04-01", "2026-07-01"]
    tick_svg = []
    for label in ticks:
        px = x(day(stamp(label)))
        tick_svg.append(
            f'<line x1="{px:.1f}" y1="{top_a}" x2="{px:.1f}" y2="{bottom_b}" '
            'stroke="#273248" stroke-width="1"/>'
            f'<text x="{px:.1f}" y="680" text-anchor="middle" '
            f'fill="#9ca9bd" font-size="13">{label[:7]}</text>')

    y_ticks = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        value = y_min + fraction * (y_max - y_min)
        py = y(value)
        y_ticks.append(
            f'<line x1="{left}" y1="{py:.1f}" x2="{right}" y2="{py:.1f}" '
            'stroke="#273248" stroke-width="1"/>'
            f'<text x="78" y="{py + 5:.1f}" text-anchor="end" fill="#9ca9bd" '
            f'font-size="13">${value:,.0f}</text>')

    final = float(report["final_bal"])
    growth = float(report["net_growth"])
    mtm_dd = float(report["max_drawdown"])
    subtitle = (f'Final ${final:,.2f}   Return +{growth:.2f}%   '
                f'MTM max drawdown {mtm_dd:.2f}%   PF {float(report["profit_factor"]):.3f}')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#0b1020"/>
<text x="{left}" y="38" fill="#f5f7fb" font-family="Segoe UI, sans-serif" font-size="25" font-weight="700">Rust Combined Book — $400, NQ + ETH + Commodities</text>
<text x="{left}" y="66" fill="#aebbd0" font-family="Segoe UI, sans-serif" font-size="15">{escape(subtitle)}</text>
{''.join(tick_svg)}{''.join(y_ticks)}
<rect x="{shade_x:.1f}" y="{top_a}" width="{shade_w:.1f}" height="{bottom_b-top_a}" fill="#ef4444" opacity="0.10"/>
<polyline points="{equity_points}" fill="none" stroke="#42d6a4" stroke-width="3" stroke-linejoin="round"/>
<circle cx="{x(curve[-1][0]):.1f}" cy="{y(curve[-1][1]):.1f}" r="5" fill="#42d6a4"/>
<text x="{left}" y="112" fill="#dce5f3" font-family="Segoe UI, sans-serif" font-size="14">Realized account equity</text>
<line x1="{left}" y1="{top_b}" x2="{right}" y2="{top_b}" stroke="#53617a"/>
<polyline points="{dd_points}" fill="none" stroke="#ff718b" stroke-width="2"/>
<text x="{left}" y="550" fill="#dce5f3" font-family="Segoe UI, sans-serif" font-size="14">Close-to-close drawdown</text>
<text x="{right}" y="550" text-anchor="end" fill="#ff9bab" font-family="Segoe UI, sans-serif" font-size="13">MTM DD window: {escape(report['max_drawdown_peak_date'])} → {escape(report['max_drawdown_trough_date'])}</text>
<text x="78" y="{top_b+5}" text-anchor="end" fill="#9ca9bd" font-family="Segoe UI, sans-serif" font-size="13">0%</text>
<text x="78" y="{bottom_b}" text-anchor="end" fill="#9ca9bd" font-family="Segoe UI, sans-serif" font-size="13">{dd_min:.1f}%</text>
<text x="{left}" y="706" fill="#718098" font-family="Segoe UI, sans-serif" font-size="12">Equity line is realized P&amp;L from Rust trades. Official {mtm_dd:.2f}% drawdown includes open-position mark-to-market excursions.</text>
</svg>'''
    with open(destination, "w", encoding="utf-8") as handle:
        handle.write(svg)


def main():
    parser = argparse.ArgumentParser()
    # MUST TRACK `live_trade/src/main.rs`, which reads `PORT` and falls back to
    # 4000. This said 8080, which no longer answers.
    parser.add_argument(
        "--url",
        default=f"http://127.0.0.1:{os.environ.get('PORT', '4000')}/api/combine",
    )
    parser.add_argument("--out", default="sandbox/results/combined_book_target12_equity.svg")
    args = parser.parse_args()
    render(request_book(args.url), args.out)
    print(args.out)


if __name__ == "__main__":
    main()
