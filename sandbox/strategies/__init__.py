"""Strategy replicas. Importing this package registers all of them."""
from sandbox.strategies.base import REGISTRY, Strategy, get, register  # noqa: F401

from sandbox.strategies import absorption_reversal  # noqa: F401,E402  (registers on import)
from sandbox.strategies import air_pocket_fade  # noqa: F401,E402  (registers on import)
from sandbox.strategies import book_slope_asymmetry  # noqa: F401,E402  (registers on import)
from sandbox.strategies import creamer_orderflow  # noqa: F401,E402  (registers on import)
from sandbox.strategies import fabervaale_orderflow  # noqa: F401,E402  (registers on import)
from sandbox.strategies import hourly_delta_reversal  # noqa: F401,E402  (registers on import)
from sandbox.strategies import kyle_lambda_continuation  # noqa: F401,E402
from sandbox.strategies import large_print_continuation  # noqa: F401,E402
from sandbox.strategies import liquidity_withdrawal  # noqa: F401,E402  (registers on import)
from sandbox.strategies import l2_opening_range_breakout  # noqa: F401,E402
from sandbox.strategies import microprice_divergence  # noqa: F401,E402  (registers on import)
from sandbox.strategies import ofi_momentum  # noqa: F401,E402  (registers on import)
from sandbox.strategies import stacked_imbalance  # noqa: F401,E402  (registers on import)
from sandbox.strategies import stop_run_reversal  # noqa: F401,E402  (registers on import)
from sandbox.strategies import unfinished_auction  # noqa: F401,E402  (registers on import)
from sandbox.strategies import volume_accumulation  # noqa: F401,E402  (registers on import)
from sandbox.strategies import sweep_trade_through  # noqa: F401,E402  (registers on import)
from sandbox.strategies import s6_iceberg_exhaustion  # noqa: F401,E402
from sandbox.strategies import whale_digestion  # noqa: F401,E402
