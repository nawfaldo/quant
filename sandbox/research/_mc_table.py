"""Print a Monte Carlo comparison table for mc_books_<name>.json results."""
import json, os, sys
R = os.path.join(os.path.dirname(__file__), "..", "results")
def pct(d, m, q): return d["runs"]["boot"]["metrics"][m]["percentiles"][str(q)]
print(f"{'book':10}{'n':>4}{'realised':>11}{'dd':>7} |{'RET p5':>9}{'p50':>9}{'p95':>10} |"
      f"{'DD p50':>8}{'p95':>7}{'p99':>7}{'worst':>7} |{'dd>20':>7}{'dd>25':>7}{'dd>30':>7}{'CVaR95':>8}")
for n in sys.argv[1:]:
    p = os.path.join(R, f"mc_books_{n}.json")
    if not os.path.exists(p): print(f"{n:10} (pending)"); continue
    d = json.load(open(p)); a = d["actual"]; t = d["runs"]["boot"]["tail"]
    print(f"{n[:10]:10}{len(d['members']):>4}{a['return_pct']:>10,.0f}%{a['mtm_dd_pct']:>6.2f}% |"
          f"{pct(d,'return_pct',5):>8,.0f}%{pct(d,'return_pct',50):>8,.0f}%{pct(d,'return_pct',95):>9,.0f}% |"
          f"{pct(d,'mtm_dd_pct',50):>7.2f}%{pct(d,'mtm_dd_pct',95):>6.2f}%{pct(d,'mtm_dd_pct',99):>6.2f}%"
          f"{t['worst_dd_pct']:>6.2f}% |{t['p_mtm_dd_over_20']:>6.1f}%{t['p_mtm_dd_over_25']:>6.1f}%"
          f"{t['p_mtm_dd_over_30']:>6.1f}%{t['cvar95_mtm_dd']:>7.2f}%")
