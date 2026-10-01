"""Baseline, risk sweep and drop-one, paired on 1,000 paths per window."""
import json
import os
import sys

from sandbox.research import _p99_lab as lab

OUT = os.path.join(os.path.dirname(__file__), "..", "results",
                   os.environ.get("OUT_NAME", "p99_exp1.json"))


def show(window, rows, base):
    print(f"\n=== {window}   (base p99 {base['dd99']:.2f}, median {base['med_ret']:,.0f}%)")
    print(f"{'config':<44}{'real%':>10}{'realDD':>8}{'med%':>10}{'p5%':>9}"
          f"{'dd50':>7}{'dd95':>7}{'dd99':>7}{'P>20':>7}")
    for r in rows:
        print(f"{r['label']:<44}{r['real_ret']:>10,.0f}{r['real_dd']:>8.2f}{r['med_ret']:>10,.0f}"
              f"{r['p5_ret']:>9,.0f}{r['dd50']:>7.2f}{r['dd95']:>7.2f}{r['dd99']:>7.2f}{r['p_dd20']:>6.1f}%",
              flush=True)


if __name__ == "__main__":
    canon = lab.canon_keys()
    configs, labels = [], []
    configs.append((canon, 0.13, None)); labels.append("canon 0.13")
    for risk in ((0.10, 0.08, 0.06, 0.04) if not os.environ.get("NO_RISK") else ()):
        configs.append((canon, risk, None)); labels.append(f"canon {risk}")
    for key in canon:
        configs.append(([k for k in canon if k != key], 0.13, None)); labels.append(f"-{key}")
    result = {}
    for window in sys.argv[1:] or ["2224", "2526"]:
        rows = lab.evaluate(window, configs, paths=int(os.environ.get("PATHS", "1000")))
        for r, label in zip(rows, labels):
            r["label"] = label
        show(window, rows, rows[0])
        result[window] = [{k: v for k, v in r.items() if k not in ("by_sleeve",)} for r in rows]
        with open(OUT, "w") as handle:
            json.dump(result, handle, indent=1)
