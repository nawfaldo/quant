import json
from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_combined_montecarlo as mc

def main():
    lab._arm()
    book = list(ecs.BOOK)
    print(f"Canon book has {len(book)} sleeves:")
    for i, s in enumerate(book, 1):
        print(f"  {i:2d}. {s}")

    configs = [(book, 0.13, None)]
    r26 = lab.evaluate("2526", configs, paths=300, realised=True)[0]
    r24 = lab.evaluate("2224", configs, paths=300, realised=True)[0]
    rfull = lab.evaluate("2226", configs, paths=300, realised=True)[0]

    # Cold 2026 replay
    state, lo_25, hi_26 = lab.load_state("2526")
    lo_26 = lab.ts("2026-01-01")
    sub = {
        "members": [state["by_key"][k] for k in book],
        "logs": state["logs"],
        "bars_by": state["bars_by"],
        "ctx_by": state["ctx_by"]
    }
    ecs.CANON_RISK_SCALE = 0.13
    ecs.CANON_GROSS_CAP = None
    book26 = mc.run_book(sub, lo=lo_26, hi=hi_26, initial=500.0)

    print("\n=== NEW CANON (23 SLEEVES) - VERIFIED METRICS ===")
    print(f"2025-26 | Med: {r26['med_ret']:+6.0f}% | Realised: {r26['real_ret']:+6.1f}% | MC p95: {r26['dd95']:5.2f}% | MC p99: {r26['dd99']:5.2f}% | Real DD: {r26['real_dd']:5.2f}% | P(>20): {r26['p_dd20']:4.1f}%")
    print(f"2022-24 | Med: {r24['med_ret']:+6.0f}% | Realised: {r24['real_ret']:+6.1f}% | MC p95: {r24['dd95']:5.2f}% | MC p99: {r24['dd99']:5.2f}% | Real DD: {r24['real_dd']:5.2f}% | P(>20): {r24['p_dd20']:4.1f}%")
    print(f"2022-26 | Med: {rfull['med_ret']:+6.0f}% | Realised: {rfull['real_ret']:+6.1f}% | MC p95: {rfull['dd95']:5.2f}% | MC p99: {rfull['dd99']:5.2f}% | Real DD: {rfull['real_dd']:5.2f}% | P(>20): {rfull['p_dd20']:4.1f}%")
    print(f"2026    | Return: {book26['return_pct']:+6.1f}% | Real PnL: +${book26['final'] - 500:,.2f} | Final: ${book26['final']:,.2f} | MTM DD: {book26['mtm_dd_pct']:5.2f}% | Trades: {book26['trades']}")

    # Save summary
    out = {
        "sleeves": book,
        "count": len(book),
        "2526": r26,
        "2224": r24,
        "2226": rfull,
        "2026_cold": {
            "return_pct": book26["return_pct"],
            "pnl": book26["final"] - 500,
            "final": book26["final"],
            "mtm_dd_pct": book26["mtm_dd_pct"],
            "trades": book26["trades"]
        }
    }
    with open("sandbox/results/new_canon_23_verified.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    main()
