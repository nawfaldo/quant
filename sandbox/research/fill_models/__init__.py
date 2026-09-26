"""Broker fill models: how a decision is priced the way one account fills it.

One module per broker, each exposing the same readers (`minute_table`,
`spread_by_bar_1m`, `price_by_bar_1m`, `exit_delay_seconds`,
`release_minutes`, `load_maps`). `cfd_families.FILL_MODEL` picks which one a
run uses, from the `EXNESS_FILL_MODEL` environment variable, default `exness`.
"""
