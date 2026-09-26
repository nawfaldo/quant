"""Monte Carlo for the FundedNext combined book.

The same machinery as `exness_combined_montecarlo` -- fortnight blocks replayed
at their own dates through the real `replay` engine, chained on one compounding
balance, as a block bootstrap (`boot`) and a permutation (`order`) -- run on the
book `fundednext_combined_strategies` wrote. Read that module's docstring for
what the replay does and `exness_combined_montecarlo`'s for why the paths are
built this way.

Settings are the canon ones every Monte Carlo here uses: no per-sleeve sizing
cap, and under-minimum orders rounded UP to the broker minimum with the extra
risk carried. Those differ from a plain `build` (a $1,500 per-sleeve sizing cap
and skipped under-minimum orders), so the realised line printed here is not the
`build` headline.

EVERY WORKER MUST LOAD THE FUNDEDNEXT PATCHES. Spawned workers re-import the
base modules fresh; `_init_worker` below imports `fundednext_combined_strategies`
first so each worker prices FundedNext, sizes against $6,000 and uses the
FundedNext tables, then hands over to the stock initialiser.

    py -m sandbox.research.fundednext_combined_montecarlo cache
    py -m sandbox.research.fundednext_combined_montecarlo verify
    py -m sandbox.research.fundednext_combined_montecarlo run --paths 1000
"""
import os

from sandbox.research import fundednext_combined_strategies as fn  # noqa: F401  (patches first)
from sandbox.research import exness_combined_montecarlo as mc

mc.CACHE = os.path.join(mc.ecs.CACHE_DIR, "montecarlo_fundednext_inputs.pkl")
mc.OUT_PATH = os.path.join(mc.RESULTS, "fundednext_combined_montecarlo.json")
mc.DD_LEVELS = (5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0)
_stock_init = mc._init_worker


def _init_worker(cache_path, block_days):
    # Importing this module already imported `fundednext_combined_strategies`,
    # which patched the base module in THIS process.
    _stock_init(cache_path, block_days)


mc._init_worker = _init_worker


def main():
    import sys
    argv = sys.argv[1:]
    if "--output" not in argv:
        sys.argv += ["--output", mc.OUT_PATH]
    mc.main()


if __name__ == "__main__":
    main()
