set -e
export PYTHONUNBUFFERED=1
echo "=== 1/2 canon build ==="
py -m sandbox.research.exness_combined_strategies build --members canon > sandbox/results/_log/canon_build.log 2>&1
echo "BUILD_EXIT=$?"
echo "=== 2/2 live execution book ==="
py -m sandbox.research.exness_live_execution book > sandbox/results/_log/live_book2.log 2>&1
echo "BOOK_EXIT=$?"
echo ALL_DONE
