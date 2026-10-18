# @itsjjsimon (JJ Simon) — strategy notes (2026-10-04)

TikTok grid would not load (TikTok throttled profile grids that afternoon); his bio points to his
YouTube. YouTube /videos: ~30 videos, prop-firm payouts / mindset / student calls, plus one strategy
video: "The ONE Setup That Made Me $400,000 in 90 Days (6 & 8PM Session, Step by Step)" (BLvsYJ4sqn8),
read from captions.

`jjs_fair_price` (`sandbox/research/tt_itsjjsimon.py`), NQ futures, 1-5m:
- The session open is "fair price": 18:00 Globex reopen (trade 2 h) and the 20:00 Asia open (90 min).
- One CONTINUATION in the opening candle's direction, only if the higher-timeframe bias agrees (that
  morning's 09:30 open on the same side) and not away from a gap.
- REVERSIONS: after price moves >= ~30 points away from fair, a displacement candle (body larger than
  the previous body, close beyond the previous wick) or a break of structure back toward fair -> enter;
  1-2 per session. Fair price switches to the Asian open if Asia's first move confirms it (not coded).
- Fixed 38-point target / 25-point stop (1.5R); break-even exit when the next session opens.
Coded: open 18:00/20:00, bias 1/0, away 1/2 ATR, rev 1/2, stop 1/1.5 ATR; exits rr_1.5 / level
(fair price) / time_12. Runs on the true NY minute, so it fires on the Asian indices (Tokyo open =
20:00 NY); US/FX grids here hold no evening bars (0 events there).
Gap filter and fair-price switching not coded.
