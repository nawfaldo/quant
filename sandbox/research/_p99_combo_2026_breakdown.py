"""Analyze 2026 sleeve contributions (total, monthly, and weekly) for Combo A and Combo C."""
import bisect
import json
import math
import os
import sys
from datetime import datetime, timezone

from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

DAY = 86_400
WEEK = 7 * DAY

def ts(dt_str):
    return int(datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc).timestamp())

def day_str(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")

def month_str(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m")

def run_analysis():
    lab._arm()
    state, lo_25, hi_26 = lab.load_state("2526")
    lo_26 = ts("2026-01-01")

    base_book = list(ecs.BOOK)
    combo_a = base_book + ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross"]
    combo_c = base_book + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross"]

    configs = {
        "Base (20 sleeves)": base_book,
        "Combo A (22 sleeves)": combo_a,
        "Combo C (23 sleeves)": combo_c,
    }

    results = {}

    for name, sleeves in configs.items():
        sub = {
            "members": [state["by_key"][k] for k in sleeves],
            "logs": state["logs"],
            "bars_by": state["bars_by"],
            "ctx_by": state["ctx_by"],
        }
        ecs.CANON_RISK_SCALE = 0.13
        ecs.CANON_GROSS_CAP = None

        # 1. Run 2025-26 compounded run (from $500 cold at 2025-01-01)
        book_compounded = mc.run_book(sub, lo=lo_25, hi=hi_26, initial=500.0)

        # 2. Run 2026 standalone run (from $500 cold at 2026-01-01)
        book_cold = mc.run_book(sub, lo=lo_26, hi=hi_26, initial=500.0)

        def analyze_run(book, window_lo, window_hi):
            curve = sorted(book["curve"])
            stamps = [s for s, _ in curve]
            init = book["initial"]

            def eq_at(t):
                idx = bisect.bisect_right(stamps, t) - 1
                return curve[idx][1] if idx >= 0 else init

            # Filter settled trades in 2026
            t26 = [t for t in book["settled"] if t["exit_ts"] >= lo_26 and t["exit_ts"] < window_hi]
            total_pnl_26 = sum(t["pnl"] for t in t26)
            eq_open_26 = eq_at(lo_26 - 1)
            eq_close_26 = eq_at(window_hi - 1)
            pct_26 = 100.0 * (eq_close_26 - eq_open_26) / eq_open_26

            # Per sleeve 2026 totals
            sleeve_stats = {}
            for s in sleeves:
                trades_s = [t for t in t26 if t["sleeve"] == s]
                pnl = sum(t["pnl"] for t in trades_s)
                wins = sum(1 for t in trades_s if t["pnl"] > 0)
                losses = sum(1 for t in trades_s if t["pnl"] < 0)
                gross_win = sum(t["pnl"] for t in trades_s if t["pnl"] > 0)
                gross_loss = abs(sum(t["pnl"] for t in trades_s if t["pnl"] < 0))
                pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 1.0)
                sleeve_stats[s] = {
                    "pnl": round(pnl, 2),
                    "share_of_pnl_pct": round(100.0 * pnl / total_pnl_26, 2) if total_pnl_26 else 0.0,
                    "trades": len(trades_s),
                    "wins": wins,
                    "win_rate": round(100.0 * wins / len(trades_s), 1) if trades_s else 0.0,
                    "profit_factor": round(pf, 2),
                }

            # Monthly breakdown (Jan 2026 to Aug 2026)
            months = sorted(list({month_str(t["exit_ts"]) for t in t26}))
            monthly_data = {}
            for m in months:
                trades_m = [t for t in t26 if month_str(t["exit_ts"]) == m]
                pnl_m = sum(t["pnl"] for t in trades_m)
                # Month boundaries
                # Timestamp of 1st day of month
                m_start = ts(f"{m}-01")
                # Timestamp of next month start
                yr, mn = int(m.split("-")[0]), int(m.split("-")[1])
                next_yr = yr if mn < 12 else yr + 1
                next_mn = mn + 1 if mn < 12 else 1
                m_end = min(ts(f"{next_yr:04d}-{next_mn:02d}-01"), window_hi)

                m_open = eq_at(m_start - 1)
                m_close = eq_at(m_end - 1)
                m_pct = 100.0 * (m_close - m_open) / m_open if m_open > 0 else 0.0

                sleeve_m = {}
                for s in sleeves:
                    s_trades_m = [t for t in trades_m if t["sleeve"] == s]
                    s_pnl = sum(t["pnl"] for t in s_trades_m)
                    sleeve_m[s] = round(s_pnl, 2)

                monthly_data[m] = {
                    "open": round(m_open, 2),
                    "close": round(m_close, 2),
                    "pnl": round(pnl_m, 2),
                    "pct": round(m_pct, 2),
                    "trades": len(trades_m),
                    "by_sleeve": sleeve_m
                }

            # Weekly breakdown (Mondays)
            weeks = []
            cur_monday = lo_26 - ((lo_26 // DAY + 3) % 7) * DAY
            if cur_monday < lo_26:
                cur_monday += WEEK

            while cur_monday < window_hi:
                w_start = cur_monday
                w_end = min(cur_monday + WEEK, window_hi)
                w_open = eq_at(w_start - 1)
                w_close = eq_at(w_end - 1)
                w_pct = 100.0 * (w_close - w_open) / w_open if w_open > 0 else 0.0

                trades_w = [t for t in t26 if t["exit_ts"] >= w_start and t["exit_ts"] < w_end]
                pnl_w = sum(t["pnl"] for t in trades_w)

                sleeve_w = {}
                for s in sleeves:
                    s_pnl = sum(t["pnl"] for t in trades_w if t["sleeve"] == s)
                    sleeve_w[s] = round(s_pnl, 2)

                weeks.append({
                    "week_start": day_str(w_start),
                    "open": round(w_open, 2),
                    "close": round(w_close, 2),
                    "pnl": round(pnl_w, 2),
                    "pct": round(w_pct, 2),
                    "trades": len(trades_w),
                    "by_sleeve": sleeve_w
                })
                cur_monday += WEEK

            return {
                "eq_open_26": round(eq_open_26, 2),
                "eq_close_26": round(eq_close_26, 2),
                "total_pnl_26": round(total_pnl_26, 2),
                "pct_26": round(pct_26, 2),
                "mtm_dd_pct": round(book["mtm_dd_pct"], 2),
                "sleeve_stats": sleeve_stats,
                "monthly": monthly_data,
                "weekly": weeks
            }

        results[name] = {
            "compounded_from_2025": analyze_run(book_compounded, lo_25, hi_26),
            "cold_2026": analyze_run(book_cold, lo_26, hi_26)
        }

    out_file = os.path.join(os.path.dirname(__file__), "..", "results", "combo_2026_sleeve_breakdown.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("Breakdown successfully saved to", out_file)

if __name__ == "__main__":
    run_analysis()
