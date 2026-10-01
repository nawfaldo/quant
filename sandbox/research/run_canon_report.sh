# Canon reference report: 2022-26, 2022-24, 2025-26, 2026 (realised + MC) and
# every-Monday cold $500 opening weeks. Set BOOK_NAME prefix via TAG to keep
# caches apart from the reference run:  TAG=cand bash sandbox/research/run_canon_report.sh
set -e
cd /c/Users/JawirGaming66/quant
# Caches are keyed on the name, not the membership: clear them so a changed
# BOOK is never scored off the previous book's trade logs.
rm -f sandbox/.cache/mc_books_${TAG:-canon}_*.pkl
export BOOK_KEYS=${BOOK_KEYS:-$(py -c "import platform;platform._wmi=None;from sandbox.research import exness_combined_strategies as e;print(','.join(e.BOOK))")}
run() { # name start end
  export BOOK_NAME=$1 MC_START=$2 MC_END=$3
  echo "######## $1 ($2 .. $3)"
  py -m sandbox.research._mc_books run --method boot --paths 1000 --workers ${MC_WORKERS:-12}
  py -m sandbox.research._canon_full_report realised
}
run ${TAG:-canon}_2226 2022-01-01 ""
run ${TAG:-canon}_2224 2022-01-01 2025-01-01
run ${TAG:-canon}_2526 "" ""
run ${TAG:-canon}_26 2026-01-01 ""
export BOOK_NAME=${TAG:-canon}_2226 MC_START=2022-01-01 MC_END=""
py -m sandbox.research._canon_full_report cold
py -m sandbox.research._canon_full_report markdown ${TAG:-canon}
echo ALLDONE
