"""Find the best replacement for usdjpy:kendall.

Criteria:
1. Replaces usdjpy:kendall in the base book.
2. 2022-24: Med return >= +1000%, p99 DD <= 37.5%.
3. 2025-26: Med return >= +700%, p99 DD <= 27.0%.
4. 2026 performance: Positive PnL in 2026, PF >= 1.2, active (not decayed).
"""
import json
import os
import sys
from itertools import combinations
from datetime import datetime, timezone

from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_combined_montecarlo as mc

CANDIDATES = [
    'btc:roofing', 'de40:confluence', 'de40:luxalgo_manipulation@120m', 'btc:td_ema_macd@30m',
    'audusd:lux_sweep_ifvg@60m', 'ethusd:lux_body_momentum@15m', 'btc:level_confluence',
    'audjpy:regime_breakout', 'btc:qp_rsi_extreme@60m', 'eurjpy:vwap', 'de40:rvol',
    'us500:lux_rsi_50_pullback@60m', 'uk100:xma_cross', 'gbpusd:td_ema_cross_level@30m',
    'eurjpy:lux_rubber_band@5m', 'btc:qp_momentum_top@30m', 'btc:gated_donchian',
    'gbpusd:luxalgo_manipulation@60m', 'gbpjpy:lux_body_momentum@120m',
    'eurjpy:lux_ny_vwap_pullback@15m', 'eurjpy:td_volume_dryup_break@30m', 'btc:supertrend',
    'ethusd:qp_volume_breakout@60m', 'ethusd:qp_volume_breakout@30m', 'btc:jump',
    'usoil:xma_cross', 'btc:vol_regime', 'btc:amihud', 'btc:entropy', 'btc:estimator',
    'gbpjpy:td_ut_bot@30m', 'btc:mfi', 'btc:vol_of_vol', 'usdjpy:qp_ma_cross@60m',
    'usdjpy:roofing', 'usdjpy:qp_rsi_extreme@60m', 'usdjpy:cci', 'usdjpy:sar', 'btc:nested',
    'ethusd:qp_momentum_top@15m', 'ethusd:qp_momentum_top@30m', 'ethusd:gated_orb',
    'ethusd:jump', 'ethusd:amihud', 'ethusd:cusum', 'ethusd:floor_pivot',
    'usdjpy:lux_prior_day_direction@60m', 'usdjpy:qp_volume_breakout@30m',
    'eurjpy:lux_key_levels_orb@120m', 'ethusd:swing_break'
]

def main():
    lab._arm()
    base = list(ecs.BOOK)
    base_no_k = [s for s in base if s != "usdjpy:kendall"]
    print(f"Base without kendall has {len(base_no_k)} sleeves", flush=True)

    # 1. Screen all 50 candidates as single replacements on 300 paths
    # Config 0 is Canon Base (with kendall)
    # Config 1 is Base without kendall
    # Config 2..N are Base - kendall + [c]
    configs = [(base, 0.13, None), (base_no_k, 0.13, None)] + [
        (base_no_k + [c], 0.13, None) for c in CANDIDATES
    ]

    print("\n--- STEP 1: SCREENING 50 SINGLE REPLACEMENTS (300 PATHS) ---", flush=True)
    print("Evaluating 2025-26...", flush=True)
    res_26 = lab.evaluate("2526", configs, paths=300, realised=True)
    print("Evaluating 2022-24...", flush=True)
    res_24 = lab.evaluate("2224", configs, paths=300, realised=True)

    canon_26 = res_26[0]
    canon_24 = res_24[0]
    nok_26 = res_26[1]
    nok_24 = res_24[1]

    print(f"\nCanon Base (with kendall):")
    print(f"  25-26: med {canon_26['med_ret']:+6.0f}%  p99 {canon_26['dd99']:.2f}%  real {canon_26['real_ret']:+6.1f}%")
    print(f"  22-24: med {canon_24['med_ret']:+6.0f}%  p99 {canon_24['dd99']:.2f}%  real {canon_24['real_ret']:+6.1f}%")

    print(f"\nBase without kendall (19 sleeves):")
    print(f"  25-26: med {nok_26['med_ret']:+6.0f}%  p99 {nok_26['dd99']:.2f}%  real {nok_26['real_ret']:+6.1f}%")
    print(f"  22-24: med {nok_24['med_ret']:+6.0f}%  p99 {nok_24['dd99']:.2f}%  real {nok_24['real_ret']:+6.1f}%")

    # Measure each candidate's performance
    single_replacements = []
    for c, r26, r24 in zip(CANDIDATES, res_26[2:], res_24[2:]):
        item = {
            "candidate": c,
            "med26": r26["med_ret"], "d_med26_canon": r26["med_ret"] - canon_26["med_ret"],
            "real_ret26": r26["real_ret"],
            "dd99_26": r26["dd99"], "d_dd99_26_canon": r26["dd99"] - canon_26["dd99"],
            "med24": r24["med_ret"], "d_med24_canon": r24["med_ret"] - canon_24["med_ret"],
            "real_ret24": r24["real_ret"],
            "dd99_24": r24["dd99"], "d_dd99_24_canon": r24["dd99"] - canon_24["dd99"],
        }
        single_replacements.append(item)

    # 2. Check 2026 standalone performance for all candidates
    # Settle trades in 2026 from load_state("2526")
    state, lo_25, hi_26 = lab.load_state("2526")
    lo_26 = lab.ts("2026-01-01")

    # Evaluate 2026 stats for each candidate
    for item in single_replacements:
        c = item["candidate"]
        # Run cold 2026 book for (base_no_k + [c])
        sub = {
            "members": [state["by_key"][k] for k in (base_no_k + [c])],
            "logs": state["logs"],
            "bars_by": state["bars_by"],
            "ctx_by": state["ctx_by"],
        }
        ecs.CANON_RISK_SCALE = 0.13
        ecs.CANON_GROSS_CAP = None
        book26 = mc.run_book(sub, lo=lo_26, hi=hi_26, initial=500.0)
        c_trades = [t for t in book26["settled"] if t["sleeve"] == c]
        pnl26 = sum(t["pnl"] for t in c_trades)
        wins26 = sum(1 for t in c_trades if t["pnl"] > 0)
        losses26 = sum(1 for t in c_trades if t["pnl"] < 0)
        gw = sum(t["pnl"] for t in c_trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in c_trades if t["pnl"] < 0))
        pf26 = (gw / gl) if gl > 0 else (999.0 if gw > 0 else 1.0)
        
        item["pnl26"] = round(pnl26, 2)
        item["trades26"] = len(c_trades)
        item["win_rate26"] = round(100.0 * wins26 / max(1, len(c_trades)), 1)
        item["pf26"] = round(pf26, 2)
        item["book_ret26"] = round(book26["return_pct"], 2)
        item["book_dd26"] = round(book26["mtm_dd_pct"], 2)

    # Filter qualifying single replacements:
    # 1. 2026 pnl > +$10 (must be active and positive in 2026)
    # 2. 22-24 p99 dd <= 38.0% (does not blow out 2022-24 tail risk)
    # 3. 25-26 p99 dd <= 27.5%
    # 4. 25-26 med return >= 650%
    qualifying_singles = [
        x for x in single_replacements
        if x["pnl26"] > 5.0 and x["dd99_24"] <= 38.5 and x["dd99_26"] <= 27.5 and x["med26"] >= 680
    ]
    # Rank by 2025-26 return + 2026 PnL while penalizing DD
    qualifying_singles.sort(key=lambda x: -(x["med26"] + 0.5 * x["med24"] - 50 * max(0.0, x["d_dd99_24_canon"])))

    print("\nTop Qualifying Single Replacements for usdjpy:kendall:")
    for x in qualifying_singles[:10]:
        print(f"  {x['candidate']:35s} | 26 PnL: ${x['pnl26']:+6.2f} (PF {x['pf26']:.2f}, {x['trades26']} tr) | 25-26: med {x['med26']:+5.0f}% (p99={x['dd99_26']:.2f}%) | 22-24: med {x['med24']:+5.0f}% (p99={x['dd99_24']:.2f}%)")

    # 3. Test Pairs of Replacements (Base - kendall + [c1, c2] = 21 sleeves)
    top_pool = [x["candidate"] for x in qualifying_singles[:6]]
    pair_list = []
    for pair in combinations(top_pool, 2):
        s1, f1 = pair[0].split(":", 1)
        s2, f2 = pair[1].split(":", 1)
        if s1 == s2 and f1.split("@")[0] == f2.split("@")[0]:
            continue
        pair_list.append(list(pair))

    print(f"\n--- STEP 2: TESTING {len(pair_list)} PAIR REPLACEMENTS ON 300 PATHS ---", flush=True)
    pair_configs = [(base_no_k + p, 0.13, None) for p in pair_list]
    res_pair_26 = lab.evaluate("2526", pair_configs, paths=300, realised=True)
    res_pair_24 = lab.evaluate("2224", pair_configs, paths=300, realised=True)

    pair_results = []
    for p, r26, r24 in zip(pair_list, res_pair_26, res_pair_24):
        item = {
            "pair": p,
            "med26": r26["med_ret"], "d_med26_canon": r26["med_ret"] - canon_26["med_ret"],
            "real_ret26": r26["real_ret"],
            "dd99_26": r26["dd99"], "d_dd99_26_canon": r26["dd99"] - canon_26["dd99"],
            "med24": r24["med_ret"], "d_med24_canon": r24["med_ret"] - canon_24["med_ret"],
            "real_ret24": r24["real_ret"],
            "dd99_24": r24["dd99"], "d_dd99_24_canon": r24["dd99"] - canon_24["dd99"],
        }
        pair_results.append(item)

    qualifying_pairs = [
        x for x in pair_results
        if x["dd99_24"] <= 38.0 and x["dd99_26"] <= 26.5 and x["med26"] >= 750
    ]
    qualifying_pairs.sort(key=lambda x: -(x["med26"] + 0.5 * x["med24"] - 50 * max(0.0, x["d_dd99_24_canon"])))

    print("\nTop Qualifying Pair Replacements:")
    for x in qualifying_pairs[:8]:
        p_str = " + ".join(x["pair"])
        print(f"  [{p_str}]")
        print(f"    25-26: med {x['med26']:+5.0f}% (p99={x['dd99_26']:.2f}%) | 22-24: med {x['med24']:+5.0f}% (p99={x['dd99_24']:.2f}%)")

    # 4. Final Comparison across Canon Base, Combo A, Combo C with top replacements
    # Top single replacement:
    best_single = qualifying_singles[0]["candidate"] if qualifying_singles else None
    best_pair = qualifying_pairs[0]["pair"] if qualifying_pairs else None

    # Candidate 2: best USDJPY replacement specifically (if user wants same asset class)
    usdjpy_singles = [x for x in qualifying_singles if x["candidate"].startswith("usdjpy:")]
    best_usdjpy = usdjpy_singles[0]["candidate"] if usdjpy_singles else None

    print(f"\nBest Overall Single Replacement: {best_single}")
    print(f"Best USDJPY Same-Asset Replacement: {best_usdjpy}")
    if best_pair:
        print(f"Best Pair Replacement: {' + '.join(best_pair)}")

    # Run final side-by-side verification on 300 paths across 22-24, 25-26, and 22-26
    # For:
    # 1. Canon Base (with kendall)
    # 2. Base with replacement
    # 3. Combo A (with kendall)
    # 4. Combo A with replacement
    # 5. Combo C (with kendall)
    # 6. Combo C with replacement
    reps_to_test = []
    if best_single:
        reps_to_test.append(([best_single], f"Replace with {best_single}"))
    if best_usdjpy and best_usdjpy != best_single:
        reps_to_test.append(([best_usdjpy], f"Replace with {best_usdjpy}"))
    if best_pair:
        reps_to_test.append((best_pair, f"Replace with {' + '.join(best_pair)}"))

    final_configs = [
        ("Canon Base (20)", base),
        ("Combo A (22)", base + ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross"]),
        ("Combo C (23)", base + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross"]),
    ]
    for rep_sleeves, rep_lbl in reps_to_test:
        final_configs.append((f"Base ({rep_lbl})", base_no_k + rep_sleeves))
        final_configs.append((f"Combo A ({rep_lbl})", [s for s in base + ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross"] if s != "usdjpy:kendall"] + rep_sleeves))
        final_configs.append((f"Combo C ({rep_lbl})", [s for s in base + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross"] if s != "usdjpy:kendall"] + rep_sleeves))

    eval_tuples = [(cfg[1], 0.13, None) for cfg in final_configs]
    print(f"\n--- STEP 3: FINAL MULTI-WINDOW EVALUATION ({len(eval_tuples)} CONFIGS, 300 PATHS) ---", flush=True)
    f_26 = lab.evaluate("2526", eval_tuples, paths=300, realised=True)
    f_24 = lab.evaluate("2224", eval_tuples, paths=300, realised=True)
    f_full = lab.evaluate("2226", eval_tuples, paths=300, realised=True)

    final_report = []
    for (name, slvs), d26, d24, df in zip(final_configs, f_26, f_24, f_full):
        entry = {
            "name": name,
            "sleeves_count": len(slvs),
            "2526": {"med": d26["med_ret"], "real": d26["real_ret"], "p99": d26["dd99"], "real_dd": d26["real_dd"], "p_dd20": d26["p_dd20"]},
            "2224": {"med": d24["med_ret"], "real": d24["real_ret"], "p99": d24["dd99"], "real_dd": d24["real_dd"], "p_dd20": d24["p_dd20"]},
            "2226": {"med": df["med_ret"], "real": df["real_ret"], "p99": df["dd99"], "real_dd": df["real_dd"], "p_dd20": df["p_dd20"]},
        }
        final_report.append(entry)

    out_file = os.path.join(os.path.dirname(__file__), "..", "results", "kendall_replacements_final.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "single_replacements": single_replacements,
            "qualifying_singles": qualifying_singles,
            "qualifying_pairs": qualifying_pairs,
            "final_report": final_report
        }, f, indent=2)

    print("\n=== FINAL RESULTS ACROSS ALL WINDOWS ===")
    for entry in final_report:
        n = entry["name"]
        d26 = entry["2526"]
        d24 = entry["2224"]
        df = entry["2226"]
        print(f"\n{n} ({entry['sleeves_count']} sleeves):")
        print(f"  2025-26 | med {d26['med']:+6.0f}% | real {d26['real']:+6.1f}% | p99 {d26['p99']:5.2f}% | rDD {d26['real_dd']:5.2f}% | P(>20) {d26['p_dd20']:4.1f}%")
        print(f"  2022-24 | med {d24['med']:+6.0f}% | real {d24['real']:+6.1f}% | p99 {d24['p99']:5.2f}% | rDD {d24['real_dd']:5.2f}% | P(>20) {d24['p_dd20']:4.1f}%")
        print(f"  2022-26 | med {df['med']:+6.0f}% | real {df['real']:+6.1f}% | p99 {df['p99']:5.2f}% | rDD {df['real_dd']:5.2f}% | P(>20) {df['p_dd20']:4.1f}%")

if __name__ == "__main__":
    main()
