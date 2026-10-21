#!/bin/bash
# The 11 re-signalled symbols (Dukascopy signals, FundedNext fills) through the
# filtered/ pipeline: screen (2026 at 1x) -> keep t>=1, n>=30 into
# filtered/fundednext -> equal-dd leverage -> ladder into leveraged_fundednext.
# PC + Mac via twohost. Pools are staged as directories (FN_LEV_SRC), because a
# 2,479-name FN_LOG_FILES is past Windows' 32k environment limit.
cd /c/Users/JawirGaming66/quant
export CFD_BALANCE=6000 CFD_BROKER=fundednext CFD_SPLIT=2024-2025 CFD_OOS_END=2026-10-15 CFD_COMMISSION="$(cat sandbox/results/logs/ncf/commission.json)" CFD_BROKER_HOURS=1 EXNESS_FILLS_ONLY=1 EXNESS_HOLD_SKIP=1 PYTHONIOENCODING=utf-8
R=sandbox/results; L=$R/filtered/leveraged_fundednext; A=$R/.redo11_src; B=$R/.redo11_pass
LOG=sandbox/results/logs/rerun/redo11.log; PW=${PW:-16}; MW=${MW:-8}
echo "$(date '+%F %T') start" >> $LOG
py - <<'P' || exit 1
import json, os, shutil
R = "sandbox/results"; A = R + "/.redo11_src"
names = open(R + "/logs/rerun/redo11.txt").read().split(",")
shutil.rmtree(A, ignore_errors=True); os.makedirs(A)
ix = json.load(open(R + "/fundednext/SURVIVORS.json", encoding="utf-8"))
keep = [s for s in ix["survivors"] if s["file"] in set(names)]
for s in keep:
    shutil.copy(f"{R}/fundednext/{s['file']}", A)
json.dump({"count": len(keep), "survivors": keep}, open(A + "/SURVIVORS.json", "w"), indent=1)
print("staged", len(keep))
P
rm -f $L/.screen*.pkl
FN_LEV_SCREEN=1 FN_LEV_SRC=$A TWOHOST_PUSH="$A" bash sandbox/research/twohost_run.sh redo11_screen $PW $MW sandbox.research._leverage_fundednext >> $LOG 2>&1 || exit 1
py - <<'P' >> $LOG 2>&1 || exit 1
import json, os, shutil
R = "sandbox/results"; A = R + "/.redo11_src"; B = R + "/.redo11_pass"
scr = json.load(open(R + "/filtered/leveraged_fundednext/screen.json"))
dst = R + "/filtered/fundednext"
S = {s["file"]: s for s in json.load(open(A + "/SURVIVORS.json"))["survivors"]}
idx = json.load(open(dst + "/SURVIVORS.json")); have = {s["file"] for s in idx["survivors"]}
add = []
for f, r in scr.items():
    if r["n"] < 30 or r["t"] < 1 or f in have:
        continue
    add.append(dict(S[f], oos_return_pct=r["ret"], oos_max_dd_pct=r["dd"], oos_trades=r["n"],
                    oos_pf=r["pf"], oos_net_bp_per_trade=r["bp"], oos_net_t_stat=r["t"]))
    shutil.copy(f"{A}/{f}", f"{dst}/{f}")
idx["survivors"] += add; idx["survivors"].sort(key=lambda s: -s["oos_net_t_stat"]); idx["count"] = len(idx["survivors"])
json.dump(idx, open(dst + "/SURVIVORS.json", "w"), indent=2)
shutil.rmtree(B, ignore_errors=True); os.makedirs(B)
for s in add:
    shutil.copy(f"{A}/{s['file']}", B)
json.dump({"count": len(add), "survivors": add}, open(B + "/SURVIVORS.json", "w"), indent=1)
print("screened", len(scr), "passed", len(add), "filtered total", idx["count"])
P
cp $L/SURVIVORS.json $R/logs/rerun/redo11_lev_backup.json
rm -f $L/.part*.pkl $L/.lev*.pkl
FN_LEV_SRC=$B TWOHOST_PUSH="$B" bash sandbox/research/twohost_run.sh redo11_lev $PW $MW sandbox.research._leverage_fundednext >> $LOG 2>&1 || exit 1
py - <<'P' >> $LOG 2>&1
import json
L = "sandbox/results/filtered/leveraged_fundednext/SURVIVORS.json"
old = json.load(open("sandbox/results/logs/rerun/redo11_lev_backup.json")); new = json.load(open(L))
fs = {s["file"] for s in new["survivors"]}
old["survivors"] = [s for s in old["survivors"] if s["file"] not in fs] + new["survivors"]
old["survivors"].sort(key=lambda s: -s["lev_2026_return_pct"]); old["count"] = len(old["survivors"])
json.dump(old, open(L, "w"), indent=2); print("leveraged index", old["count"])
P
rm -f $L/.ladder*.pkl
FN_LEV_LADDER=1 FN_LEV_SRC=$B TWOHOST_PUSH="$B" bash sandbox/research/twohost_run.sh redo11_ladder $PW $MW sandbox.research._leverage_fundednext >> $LOG 2>&1 || exit 1
rm -rf $A $B
echo "$(date '+%F %T') REDO11 FILTER DONE" >> $LOG
touch sandbox/results/logs/rerun/REDO11_DONE
