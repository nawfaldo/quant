import json
import os
from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_strategies as ecs

def main():
    base = list(ecs.BOOK)
    base_no_k = [s for s in base if s != "usdjpy:kendall"]

    combo_a = base + ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross"]
    combo_a_no_k = [s for s in combo_a if s != "usdjpy:kendall"]

    combo_c = base + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross"]
    combo_c_no_k = [s for s in combo_c if s != "usdjpy:kendall"]

    configs = [
        (base, 0.13, None),
        (base_no_k, 0.13, None),
        (combo_a, 0.13, None),
        (combo_a_no_k, 0.13, None),
        (combo_c, 0.13, None),
        (combo_c_no_k, 0.13, None),
    ]

    names = [
        "Base (20)",
        "Base - kendall (19)",
        "Combo A (22)",
        "Combo A - kendall (21)",
        "Combo C (23)",
        "Combo C - kendall (22)"
    ]

    print("Evaluating 2025-26...", flush=True)
    r26 = lab.evaluate("2526", configs, paths=300, realised=True)
    print("Evaluating 2022-24...", flush=True)
    r24 = lab.evaluate("2224", configs, paths=300, realised=True)
    print("Evaluating 2022-26...", flush=True)
    rfull = lab.evaluate("2226", configs, paths=300, realised=True)

    results = []
    print("\n" + "=" * 125)
    print(f"{'Configuration':<24} | {'25-26 Med':>9} {'25-26 Real':>10} {'25-26 p99':>9} {'25-26 rDD':>9} | {'22-24 Med':>9} {'22-24 Real':>10} {'22-24 p99':>9} {'22-24 rDD':>9} | {'22-26 Med':>9} {'22-26 p99':>9}")
    print("=" * 125)
    for n, d26, d24, df in zip(names, r26, r24, rfull):
        print(f"{n:<24} | {d26['med_ret']:>+8.0f}% {d26['real_ret']:>+9.1f}% {d26['dd99']:>8.2f}% {d26['real_dd']:>8.2f}% | {d24['med_ret']:>+8.0f}% {d24['real_ret']:>+9.1f}% {d24['dd99']:>8.2f}% {d24['real_dd']:>8.2f}% | {df['med_ret']:>+8.0f}% {df['dd99']:>8.2f}%")
        results.append({
            "name": n,
            "2526": {"med": d26["med_ret"], "real": d26["real_ret"], "dd99": d26["dd99"], "real_dd": d26["real_dd"], "p_dd20": d26["p_dd20"]},
            "2224": {"med": d24["med_ret"], "real": d24["real_ret"], "dd99": d24["dd99"], "real_dd": d24["real_dd"], "p_dd20": d24["p_dd20"]},
            "2226": {"med": df["med_ret"], "real": df["real_ret"], "dd99": df["dd99"], "real_dd": df["real_dd"], "p_dd20": df["p_dd20"]},
        })

    out_file = os.path.join(os.path.dirname(__file__), "..", "results", "kendall_drop_comparison.json")
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
