import json
import math
import os
import sys
from itertools import combinations

from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_strategies as ecs

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

OUT_JSON = os.path.join(os.path.dirname(__file__), "..", "results", "eval_adds_results.json")

def main():
    book = list(ecs.BOOK)
    print(f"Base book has {len(book)} sleeves", flush=True)

    # 1. Evaluate single adds on 300 paths
    PATHS_FAST = 300
    configs = [(book, 0.13, None)] + [(book + [c], 0.13, None) for c in CANDIDATES]

    CACHE_SINGLE = os.path.join(os.path.dirname(__file__), "..", "..", ".cache", "single_adds_screening_300.json")
    if os.path.exists(CACHE_SINGLE):
        print(f"Loading cached single add results from {CACHE_SINGLE}...", flush=True)
        with open(CACHE_SINGLE, "r") as f:
            cached_data = json.load(f)
            base26 = cached_data["base26"]
            base24 = cached_data["base24"]
            single_results = cached_data["single_results"]
    else:
        print(f"\n--- STEP 1: SCREENING ALL 50 SINGLE ADDS (PATHS={PATHS_FAST}) ---", flush=True)
        print("Evaluating 2025-26...", flush=True)
        res_2526 = lab.evaluate("2526", configs, paths=PATHS_FAST, realised=True)
        print("Evaluating 2022-24...", flush=True)
        res_2224 = lab.evaluate("2224", configs, paths=PATHS_FAST, realised=True)

        base26 = res_2526[0]
        base24 = res_2224[0]

        single_results = []
        for c, r26, r24 in zip(CANDIDATES, res_2526[1:], res_2224[1:]):
            item = {
                "candidate": c,
                "med26": r26["med_ret"], "d_med26": r26["med_ret"] - base26["med_ret"],
                "real_ret26": r26["real_ret"],
                "dd99_26": r26["dd99"], "d_dd99_26": r26["dd99"] - base26["dd99"],
                "real_dd26": r26["real_dd"], "p_dd20_26": r26["p_dd20"],
                "med24": r24["med_ret"], "d_med24": r24["med_ret"] - base24["med_ret"],
                "real_ret24": r24["real_ret"],
                "dd99_24": r24["dd99"], "d_dd99_24": r24["dd99"] - base24["dd99"],
                "real_dd24": r24["real_dd"], "p_dd20_24": r24["p_dd20"],
            }
            single_results.append(item)

        os.makedirs(os.path.dirname(CACHE_SINGLE), exist_ok=True)
        with open(CACHE_SINGLE, "w") as f:
            json.dump({"base26": base26, "base24": base24, "single_results": single_results}, f, indent=2)

    print(f"\nBASE (20 sleeves):")
    print(f"  2025-26: med {base26['med_ret']:+6.0f}%  real {base26['real_ret']:+6.1f}%  p99 {base26['dd99']:.2f}%  real_dd {base26['real_dd']:.2f}%  p_dd20 {base26['p_dd20']:.1f}%")
    print(f"  2022-24: med {base24['med_ret']:+6.0f}%  real {base24['real_ret']:+6.1f}%  p99 {base24['dd99']:.2f}%  real_dd {base24['real_dd']:.2f}%  p_dd20 {base24['p_dd20']:.1f}%")

    # Sort candidates by:
    # A) Best return improvements on 2025-26 while holding dd99 within +0.8pp on 25-26 and +1.5pp on 22-24
    viable_return = [x for x in single_results if x["d_med26"] > 0 and x["d_dd99_26"] <= 0.8 and x["d_dd99_24"] <= 1.5]
    viable_return.sort(key=lambda x: -x["d_med26"])

    # B) Best tail hedges on 2022-24 (lowers dd99_24 or keeps it <= base)
    viable_hedges = [x for x in single_results if x["d_dd99_24"] <= 0.5 and x["d_dd99_26"] <= 1.0]
    viable_hedges.sort(key=lambda x: x["d_dd99_24"])

    print("\nTop 15 Return Boosters (with contained dd):")
    for x in viable_return[:15]:
        print(f"  {x['candidate']:35s} | 25-26: med {x['med26']:+6.0f}% (d={x['d_med26']:+5.0f}%) p99={x['dd99_26']:.2f}% (d={x['d_dd99_26']:+.2f}) | 22-24: med {x['med24']:+6.0f}% (d={x['d_med24']:+5.0f}%) p99={x['dd99_24']:.2f}% (d={x['d_dd99_24']:+.2f})")

    print("\nTop 10 Tail Hedges on 2022-24:")
    for x in viable_hedges[:10]:
        print(f"  {x['candidate']:35s} | 22-24: p99={x['dd99_24']:.2f}% (d={x['d_dd99_24']:+.2f}) med {x['med24']:+6.0f}% | 25-26: p99={x['dd99_26']:.2f}% (d={x['d_dd99_26']:+.2f}) med {x['med26']:+6.0f}%")

    # 2. Test Combinations
    # Select pool of top components: top return boosters + top hedges
    pool_for_combos = list(dict.fromkeys(
        [x["candidate"] for x in viable_return[:10]] + 
        [x["candidate"] for x in viable_hedges[:6]]
    ))
    print(f"\n--- STEP 2: TESTING COMBINATIONS FROM {len(pool_for_combos)} SHORTLISTED CANDIDATES ---", flush=True)
    print("Pool for combos:", pool_for_combos, flush=True)

    combo_list = []
    # All pairs from pool
    for pair in combinations(pool_for_combos, 2):
        # Don't stack 2 sleeves from the same family/symbol if redundant
        s1, f1 = pair[0].split(":", 1)
        s2, f2 = pair[1].split(":", 1)
        if s1 == s2 and f1.split("@")[0] == f2.split("@")[0]:
            continue
        combo_list.append(list(pair))

    # Also sample promising triplets (e.g. top return + top hedge + 2nd return)
    top_hedges = [x["candidate"] for x in viable_hedges[:4]]
    top_boosters = [x["candidate"] for x in viable_return[:6]]
    for h in top_hedges:
        for b1, b2 in combinations(top_boosters, 2):
            if h not in (b1, b2):
                triplet = sorted([h, b1, b2])
                if triplet not in combo_list:
                    combo_list.append(triplet)

    print(f"Testing {len(combo_list)} combinations on {PATHS_FAST} paths...", flush=True)
    combo_configs = [(book + c, 0.13, None) for c in combo_list]

    res_combo_26 = lab.evaluate("2526", combo_configs, paths=PATHS_FAST, realised=True)
    res_combo_24 = lab.evaluate("2224", combo_configs, paths=PATHS_FAST, realised=True)

    combo_results = []
    for c, r26, r24 in zip(combo_list, res_combo_26, res_combo_24):
        item = {
            "adds": c,
            "n_adds": len(c),
            "med26": r26["med_ret"], "d_med26": r26["med_ret"] - base26["med_ret"],
            "real_ret26": r26["real_ret"],
            "dd99_26": r26["dd99"], "d_dd99_26": r26["dd99"] - base26["dd99"],
            "p_dd20_26": r26["p_dd20"],
            "med24": r24["med_ret"], "d_med24": r24["med_ret"] - base24["med_ret"],
            "real_ret24": r24["real_ret"],
            "dd99_24": r24["dd99"], "d_dd99_24": r24["dd99"] - base24["dd99"],
            "p_dd20_24": r24["p_dd20"],
        }
        combo_results.append(item)

    # Filter combinations:
    # d_med26 > +50pp, dd99_26 <= base26 + 1.5pp, dd99_24 <= base24 + 2.0pp
    winning_combos = [
        x for x in combo_results
        if x["d_med26"] > 50 and x["d_dd99_26"] <= 1.5 and x["d_dd99_24"] <= 2.0
    ]
    # Rank by 2025-26 return improvement while penalizing tail increase
    winning_combos.sort(key=lambda x: -(x["d_med26"] - 50 * max(0.0, x["d_dd99_26"]) - 30 * max(0.0, x["d_dd99_24"])))

    print("\nTop 15 Qualifying Combinations:")
    for x in winning_combos[:15]:
        add_str = " + ".join(x["adds"])
        print(f"  [{add_str}]")
        print(f"    25-26: med {x['med26']:+6.0f}% (d={x['d_med26']:+5.0f}%)  p99={x['dd99_26']:.2f}% (d={x['d_dd99_26']:+.2f})  real={x['real_ret26']:+6.1f}%")
        print(f"    22-24: med {x['med24']:+6.0f}% (d={x['d_med24']:+5.0f}%)  p99={x['dd99_24']:.2f}% (d={x['d_dd99_24']:+.2f})  real={x['real_ret24']:+6.1f}%")

    # 3. Final Verification on 300 paths across 22-24, 25-26, and 22-26 (NO 1000 paths per user instruction)
    top_candidates_to_confirm = [[]] + [x["adds"] for x in winning_combos[:6]]
    # Ensure top single add is also confirmed
    if viable_return:
        best_single = [viable_return[0]["candidate"]]
        if best_single not in top_candidates_to_confirm:
            top_candidates_to_confirm.append(best_single)

    print(f"\n--- STEP 3: FINAL EVALUATION ACROSS 2022-24, 2025-26, 2022-26 (PATHS={PATHS_FAST}) ---", flush=True)
    confirm_configs = [(book + c, 0.13, None) for c in top_candidates_to_confirm]

    print(f"Running {PATHS_FAST} paths on 2025-26...", flush=True)
    c_2526 = lab.evaluate("2526", confirm_configs, paths=PATHS_FAST, realised=True)
    print(f"Running {PATHS_FAST} paths on 2022-24...", flush=True)
    c_2224 = lab.evaluate("2224", confirm_configs, paths=PATHS_FAST, realised=True)
    print(f"Running {PATHS_FAST} paths on 2022-26...", flush=True)
    c_2226 = lab.evaluate("2226", confirm_configs, paths=PATHS_FAST, realised=True)

    final_report = []
    labels = ["Canon Base (20 sleeves)"] + [" + ".join(c) for c in top_candidates_to_confirm[1:]]
    for lbl, adds, r26, r24, rfull in zip(labels, top_candidates_to_confirm, c_2526, c_2224, c_2226):
        entry = {
            "label": lbl,
            "adds": adds,
            "count": len(book) + len(adds),
            "2526": {
                "med_ret": r26["med_ret"], "real_ret": r26["real_ret"],
                "p5_ret": r26["p5_ret"],
                "real_dd": r26["real_dd"], "dd50": r26["dd50"],
                "dd95": r26["dd95"], "dd99": r26["dd99"],
                "p_dd20": r26["p_dd20"], "p_dd30": sum(d > 30 for d in r26.get("dd", [])) / (PATHS_FAST / 100.0)
            },
            "2224": {
                "med_ret": r24["med_ret"], "real_ret": r24["real_ret"],
                "p5_ret": r24["p5_ret"],
                "real_dd": r24["real_dd"], "dd50": r24["dd50"],
                "dd95": r24["dd95"], "dd99": r24["dd99"],
                "p_dd20": r24["p_dd20"], "p_dd30": sum(d > 30 for d in r24.get("dd", [])) / (PATHS_FAST / 100.0)
            },
            "2226": {
                "med_ret": rfull["med_ret"], "real_ret": rfull["real_ret"],
                "p5_ret": rfull["p5_ret"],
                "real_dd": rfull["real_dd"], "dd50": rfull["dd50"],
                "dd95": rfull["dd95"], "dd99": rfull["dd99"],
                "p_dd20": rfull["p_dd20"], "p_dd30": sum(d > 30 for d in rfull.get("dd", [])) / (PATHS_FAST / 100.0)
            }
        }
        final_report.append(entry)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "single_results": single_results,
            "combo_results": combo_results,
            "final_confirm_300": final_report
        }, f, indent=2)

    print(f"\n=== FINAL RESULTS ({PATHS_FAST} PATHS) ===")
    for entry in final_report:
        lbl = entry["label"]
        d26 = entry["2526"]
        d24 = entry["2224"]
        dfull = entry["2226"]
        print(f"\n{lbl} ({entry['count']} sleeves):")
        print(f"  2025-26 | med {d26['med_ret']:+6.0f}% | real {d26['real_ret']:+6.1f}% | real_dd {d26['real_dd']:5.2f}% | p95 {d26['dd95']:5.2f}% | p99 {d26['dd99']:5.2f}% | P(>20) {d26['p_dd20']:4.1f}%")
        print(f"  2022-24 | med {d24['med_ret']:+6.0f}% | real {d24['real_ret']:+6.1f}% | real_dd {d24['real_dd']:5.2f}% | p95 {d24['dd95']:5.2f}% | p99 {d24['dd99']:5.2f}% | P(>20) {d24['p_dd20']:4.1f}%")
        print(f"  2022-26 | med {dfull['med_ret']:+6.0f}% | real {dfull['real_ret']:+6.1f}% | real_dd {dfull['real_dd']:5.2f}% | p95 {dfull['dd95']:5.2f}% | p99 {dfull['dd99']:5.2f}% | P(>20) {dfull['p_dd20']:4.1f}%")

if __name__ == "__main__":
    main()
