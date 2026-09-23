set -e
until grep -q "wrote .*frontier_pool" sandbox/results/_log/cache2.log 2>/dev/null; do
  if grep -q Traceback sandbox/results/_log/cache2.log 2>/dev/null; then echo CACHE_FAILED; exit 1; fi
  sleep 30
done
echo CACHE_READY
export MC_LIVE_START=2022-01-01 PYTHONUNBUFFERED=1
py -m sandbox.research._seventh_frontier --paths 1000 --workers 10 --era full --only 13,16 --risks "0.10,0.13,0.16,0.19,0.22" > sandbox/results/_log/frontier2_full.log 2>&1
cp sandbox/results/_seventh_frontier.json sandbox/results/_seventh_frontier_full_livecoil.json
echo FULL_DONE
py -m sandbox.research._seventh_frontier --paths 1000 --workers 10 --era oos --only 13,16 --risks "0.10,0.13,0.16,0.19,0.22" > sandbox/results/_log/frontier2_oos.log 2>&1
cp sandbox/results/_seventh_frontier.json sandbox/results/_seventh_frontier_oos_livecoil.json
echo OOS_DONE
