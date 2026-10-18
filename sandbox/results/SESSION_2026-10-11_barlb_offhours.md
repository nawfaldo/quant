# Session 2026-10-11/12: bar-counted lookbacks + off-hours search

Pass rule everywhere: in-sample winner -> holdout 2025-01-01..2026-08-16 ret > 0, PF >= 1.05, n >= 30
-> beat the best of 3 coin-flip seeds ("pass*" = no seed found an in-sample cell).

- family list: `SESSION_2026-10-11_families.md`

## 1. barlb sweep (EXNESS_BAR_LOOKBACKS=1, RTH, 5m/15m/1h/4h)
- `barlb_coinflip_pass.md` -- wave 1 pass/fail table (45 families)
- `wave2_barlb_results.md` -- wave 2 (14 extra families, no swingbar/fastbar)
- `exness/barlb_vs_30m.md` -- every barlb survivor beside the same symbol/family at 30m
- survivors exported to `exness/<sym>_<family>_<bar>m-barlb.json` + `exness/SURVIVORS.json`
  (study_wave `barlb_2026-10-11`, 222 rows: 144 pass, 78 pass*)
- seals: `exness_families_<sym>_<bar>m-barlb_<tag>.json`, nulls `exness_families_null_...`

## 2. Window replay (existing survivors re-run RTH vs off-hours)
- `exness_window_replay_{rth,off}_std.json`, `..._{rth,off}-barlb_hi.json`, `..._{rth,off}-barlb_5m.json`
- result: off-hours median holdout 0% vs +8.7-10.9% in RTH; edge mostly vanishes outside RTH

## 3. Off-hours full search (`--window off`, entries only while RTH is shut)
- `offhours_results.md` -- all PASS/pass* rows, cfd_families (59 families, barlb) + every cfd_tt family
- coverage: 4h/1h/30m/15m complete for both; cfd_tt 5m complete; cfd_families 5m 67/266 jobs (stopped)
- seals: `exness_families_*_win-off.json`, `exness_tt_families_*_win-off.json` (+ `_null_` files)
- job queue / runners: `logs/run_offall.sh`, `logs/run_ttoff.sh`, `logs/super_off.sh`, done lists
  `logs/offall_done.txt`, `logs/ttoff_done.txt` (Mac copies on nawfaldo@100.95.232.126:~/quant)
- caveat: usdjpy 30m passes 14 families with coin flips at +13..+30% -- one trend, not 14 edges
- exported: 299 files `exness/<sym>_<family>_<bar>m-barlb-off.json` (cfd) and `..._<bar>m-off.json` (tt), study_wave `offhours_2026-10-12` in SURVIVORS.json (`research/_offhours_export.py`)
