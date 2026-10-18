#!/bin/bash
# replay -> filter (t>=1, n>=30) -> equal-dd leverage -> ladder, for the files in new582.txt
cd ~/quant
export CFD_BALANCE=6000 CFD_BROKER=fundednext CFD_SPLIT=2024-2025 CFD_OOS_END=2026-10-15 CFD_COMMISSION="$(cat sandbox/results/logs/ncf/commission.json)" CFD_BROKER_HOURS=1 EXNESS_FILLS_ONLY=1 EXNESS_HOLD_SKIP=1 PYTHONIOENCODING=utf-8
PY=.venv/bin/python; L=sandbox/results/filtered/leveraged_fundednext
export FN_LOG_FILES=$(cat sandbox/results/logs/rerun/new582.txt)
rm -f $L/.screen*.pkl
FN_LEV_SCREEN=1 FN_LEV_SRC=sandbox/results/fundednext $PY -m sandbox.research._leverage_fundednext --workers 8 || exit 1
rm -f $L/.screen*.pkl
$PY - <<'P' || exit 1
import pickle,math,json,shutil
R=json.load(open('sandbox/results/filtered/leveraged_fundednext/screen.json'))
src='sandbox/results/fundednext'; dst='sandbox/results/filtered/fundednext'
S={s['file']:s for s in json.load(open(src+'/SURVIVORS.json'))['survivors']}
idx=json.load(open(dst+'/SURVIVORS.json')); have={s['file'] for s in idx['survivors']}; add=[]
for f,r in R.items():
    if r['n']<30 or r['t']<1 or f in have: continue
    add.append(dict(S[f],oos_return_pct=r['ret'],oos_max_dd_pct=r['dd'],oos_trades=r['n'],oos_pf=r['pf'],oos_net_bp_per_trade=r['bp'],oos_net_t_stat=r['t']))
    shutil.copy(f'{src}/{f}',f'{dst}/{f}')
idx['survivors']+=add; idx['survivors'].sort(key=lambda s:-s['oos_net_t_stat']); idx['count']=len(idx['survivors'])
json.dump(idx,open(dst+'/SURVIVORS.json','w'),indent=2)
open('/tmp/pass_new.txt','w').write(','.join(s['file'] for s in add))
print('screened',len(R),'passed',len(add),'filtered total',idx['count'])
P
export FN_LOG_FILES=$(cat /tmp/pass_new.txt)
cp $L/SURVIVORS.json /tmp/lev_index_backup.json
rm -f $L/.part*.pkl $L/.ladder*.pkl
$PY -m sandbox.research._leverage_fundednext --workers 8 || exit 1
$PY - <<'P'
import json
L='sandbox/results/filtered/leveraged_fundednext/SURVIVORS.json'
old=json.load(open('/tmp/lev_index_backup.json')); new=json.load(open(L))
fs={s['file'] for s in new['survivors']}
old['survivors']=[s for s in old['survivors'] if s['file'] not in fs]+new['survivors']
old['survivors'].sort(key=lambda s:-s['lev_2026_return_pct']); old['count']=len(old['survivors'])
json.dump(old,open(L,'w'),indent=2); print('leveraged index',old['count'])
P
rm -f $L/.part*.pkl
FN_LEV_LADDER=1 $PY -m sandbox.research._leverage_fundednext --workers 8 || exit 1
rm -f $L/.ladder*.pkl
touch NEW_DONE
