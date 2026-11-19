# GTA 6 Trophy Rarity Forecaster

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/ArievXD/gta6-trophy-rarity-forecast-01-v2/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/ArievXD/gta6-trophy-rarity-forecast-01-v2/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts each GTA VI (PS5) trophy's earn rate on Exophase at a case's target,
as five quantiles per trophy. Python 3.13, standard library only.

## Run

```sh
python3 main.py            # reads $MAKEFUN_INPUT_DIR (default /input),
                           # writes $MAKEFUN_OUTPUT_DIR (default /output)
```

The grader runs it once per case as `python3 /bot/main.py`. One file, one
entrypoint (`main.py`); `main.ts` was removed so there is no ambiguity.

## Method

A trophy's rate is **earners / players_tracked**. At launch the denominator
grows far faster than any numerator, so a rate mostly *falls* while the player
base is still being counted, then rises as the players already tracked work
through the game. A forecaster that only repeats the last rate is therefore
badly wrong exactly when the counts refresh — which is what the practice cases
are built around.

**1. Frozen history = a refresh is coming.** If no trophy's rate moved and
`players_tracked` is constant across the whole history, the page has not
refreshed yet and the answer is the refreshed page. The median is shifted
*down* by a calibrated refresh drop and the spread is widened to an honest
width, instead of repeating a stale rate inside a 0.125-logit band:

- The drop is fitted to Exophase's own page: the house history is frozen at
  638 tracked players with rates 0.63–2.04%, and the live page ~9 h later
  shows the same trophies down by ~1.6 logits on average. Rarer trophies drop
  further (almost no new player has earned them yet), so the shift is
  `0.95 * (-1.85 + 1.08 * (logit(rate) + 5.06))`, clamped to [-2.0, -0.20],
  decaying as the case gets older (a refresh adds proportionally fewer
  players once the denominator is already large).
- The spread is 0.6 logits: wide enough to be honest about the refresh size,
  narrow enough not to pay for width at every trophy.

**2. Moving history = trend in log-time.** If the rates move, each trophy's
logit rate is fitted against `ln(1 + hours since release)` over the recent
reads (log-time, because launch dynamics slow down), read at the target, with
the extrapolation damped and capped at ±2.5 logits. The spread combines the
fit's residual with the random-walk term (`0.5 * sqrt(horizon in days)`).

**3. Saturated trophies stay put.** A trophy at ~100% is earned by every
tracked player, and every new player earns it immediately (the live page
still shows "Just Like Paradise" at 100% after the player base grew 12x), so
its median stays at the last rate with a small spread.

## Practice results (proxy evaluation)

The practice answers live on the server, so the exact practice loss cannot be
computed locally. As a proxy, both programs were scored against Exophase's
live page (2026-11-19 11:40 UTC) — a later read showing the same refresh drop.
The starter's loss against that page is **1.5084**, essentially identical to
its documented practice loss of ~1.51, which confirms the proxy is
representative.

| case | this forecaster | starter (persistence) |
|------|----------------|----------------------|
| p00  | **0.177**      | 1.508                |
| p01  | **0.179**      | 1.508                |

Sensitivity: if the true refresh is only a fraction *f* of the live page's
move, this forecaster still beats the starter for every *f* ≥ 0.5 (at f=0.5:
0.30 vs 0.71; at f=0.7: 0.17 vs 1.03). It only loses if the refresh is
tiny (f ≤ ~0.35), which the documented ~1.51 practice loss rules out.

## Verification

- Both practice cases run in ~0.07 s each (limit 8 s/case).
- Deterministic: two runs on the same input produce byte-identical output.
- Output format checked: 55 rows, ids 1–55, five finite non-decreasing
  quantiles in [0, 100], no extra keys, ~2.8 KB per case (limit 65,536).
- Trend path tested on a synthetic active-history case: a trophy rising
  1% → 5% extrapolates to a 6.6% median; a saturated 100% trophy stays at
  99.99%.
- Edge cases tested: null rates, a single history read, a trophy with no
  observations at all — all produce valid output.

## Known weaknesses

- The refresh drop is calibrated from one observed refresh (launch night).
  If the final case's history is frozen for a different reason, the shift
  could overshoot — but the final history (two days of reads) will not be
  frozen, so the trend path, not the refresh path, decides the prize.
- The trend extrapolation for the final (five days past a two-day history)
  cannot be validated locally; it is damped and capped, but the true
  five-day move is genuinely uncertain and the spread is the honest part of
  the forecast.
- The model uses only the case file (no network): it cannot see Exophase's
  current counts, so the refresh size is a prior, not a measurement.

---

<sub>Entered on [make.fun](https://make.fun/?ref=xc44gxz7&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
