# Daily futures importer

`fetch_futures_daily.py` imports the Yahoo universe declared in
`futures_universe.json`.

**The registry currently holds only `es` and `nq`, and both are marked
`"source": "external"` — so every command below is a no-op today.** The 29 Yahoo
(`=F`) symbols were dropped because they are front-month continuous and not
back-adjusted; see `web/python/CLAUDE.md`. The importer is kept for the day a
ratio-adjusted feed is added.

Initial load, preserving every table that already exists:

```bash
python3 fetch_futures_daily.py sync --missing-only
```

Incremental refresh with a seven-day correction overlap:

```bash
python3 fetch_futures_daily.py sync
```

Validate table schemas without downloading or writing market data:

```bash
python3 fetch_futures_daily.py validate
```

Tables are `<prefix>_1d`, WAL-enabled, partitioned daily, and deduplicated on
their designated timestamp. Import runs are recorded in `data_collection_runs`.
Yahoo continuous futures may contain roll discontinuities and are not a
replacement for licensed ratio-adjusted continuous-contract data.

Symbols marked `"source": "external"` in `futures_universe.json` are owned by
another feed and are never written by this importer — currently `es` and `nq`,
whose tables hold back-adjusted CFD bars. Tables are `DEDUP UPSERT KEYS(timestamp)`,
so without that guard a plain `sync` would overwrite them with front-month Yahoo
bars at a different price level, silently and with an `ok` status.
