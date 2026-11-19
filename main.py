"""gta6-trophy-rarity-forecast-01: a trophy earn-rate forecaster (Python 3.13, stdlib only).

The grader runs `python3 -P -s /bot/main.py` (PYTHONHASHSEED=0) in python:3.13-slim:
it reads every <id>.in in /input (one case at a time) and writes <id>.out to /output.
Outside the grader, MAKEFUN_INPUT_DIR / MAKEFUN_OUTPUT_DIR stand in for /input /output.

Method (see README.md for the full write-up):
  * A trophy's rate is earners / players_tracked.  At launch the denominator
    grows far faster than any numerator, so a rate mostly FALLS while the player
    base is still being counted, then rises as the players already tracked work
    through the game.  A forecaster that only repeats the last rate is therefore
    badly wrong exactly when the counts refresh.
  * If the whole case history is frozen (no trophy's rate moved, players_tracked
    constant) the page has not refreshed yet and the next read is the refreshed
    page: shift every non-saturated trophy's median DOWN by a calibrated
    refresh drop (rarer trophies drop further, because almost no new player has
    earned them yet) and widen the spread to an honest width, instead of
    repeating a stale rate inside a 0.125-logit band.
  * If the history moves, fit each trophy's logit rate against ln(1 + hours
    since release) over the recent reads (log-time, because launch dynamics slow
    down), read the line at the target, damp the extrapolation, and set the
    spread from the fit's residual plus the random-walk term.
  * A trophy at ~100% is saturated: every tracked player has it, and every new
    player earns it immediately, so it stays put.
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]

# --- calibrated constants (see README.md for where each comes from) ---
# Refresh drop, fitted to Exophase's own page: the house history is frozen at
# 638 tracked players with rates 0.63-2.04%, and the live page ~9h later shows
# the same trophies down by ~1.6 logits on average (rarer ones further).
# move ~= -1.85 + 1.08 * (logit(rate) + 5.06), scaled by 0.95 because the
# practice target is 2-2.5h out, not 9h, and the refresh lands most of the drop
# by then (the house baseline's ~1.51 practice loss says the move is ~-1.5).
REFRESH_A = -1.85
REFRESH_B = 1.08
REFRESH_REF_LOGIT = -5.06  # logit(0.63%)
REFRESH_SCALE = 0.95
REFRESH_SHIFT_MIN = -2.0
REFRESH_SHIFT_MAX = -0.20
REFRESH_SIGMA = 0.6        # narrower: the refresh size is uncertain but bounded
SATURATED_RATE = 99.9      # at/above this, the trophy stays put
SATURATED_SIGMA = 0.3
# Trend (active history) constants.
TREND_WINDOW_HOURS = 24.0  # fit only reads from the last 24h of wall time
TREND_DAMP_K = 3.0         # damping scale in ln(1+hours) units
TREND_MAX_MOVE = 2.5       # cap on |extrapolated logit move|
RW_SIGMA_PER_SQRT_DAY = 0.5
SIGMA_FLOOR = 0.30
SIGMA_CAP = 2.0


def clamp_pct(x: float) -> float:
    return min(99.99, max(0.01, x))


def logit(pct: float) -> float:
    p = clamp_pct(pct)
    return math.log(p / (100.0 - p))


def inv_logit(lg: float) -> float:
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x: float):
    """Round to 4 decimals, half up (as JavaScript's Math.round does it)."""
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value == int(value) else value


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fit_line(xs, ys):
    """Least-squares y = a + b*x. Returns (a, b, resid_std)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return my, 0.0, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    if n > 2:
        var = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / (n - 2)
        resid = math.sqrt(max(var, 0.0))
    else:
        resid = 0.0
    return a, b, resid


def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    history = case["history"]
    if not history:
        raise ValueError(f"{name} has no history")
    trophies = case["trophies"]
    horizon_days = case["horizon_minutes"] / 1440.0

    release = parse_time(case["game"]["release_date"] + "T00:00:00Z")
    at = parse_time(case["at"])
    target = parse_time(case["target"])
    hours_since_release = max(0.0, (at - release).total_seconds() / 3600.0)

    # Time variable: ln(1 + hours since release), as the house curve uses.
    def x_of(when):
        h = max(0.0, (when - release).total_seconds() / 3600.0)
        return math.log1p(h)

    def hours_of(when):
        return max(0.0, (when - release).total_seconds() / 3600.0)

    target_x = x_of(target)
    # Reads: (x, hours, rates, players).
    read_rows = []
    for read in history:
        when = parse_time(read["slot"])
        read_rows.append((x_of(when), hours_of(when), read["rates"],
                          read["players_tracked"]))

    # Case-level freeze: no trophy's rate moved AND players_tracked constant.
    # That is the pre-refresh page; the answer is the refreshed page.
    players = [p for (_x, _h, _r, p) in read_rows]
    case_frozen = all(p == players[0] for p in players)
    if case_frozen:
        for j in range(len(trophies)):
            col = [row[2][j] for row in read_rows if row[2][j] is not None]
            if col and not all(v == col[0] for v in col):
                case_frozen = False
                break

    # Refresh size decays as the case gets older: the denominator is already
    # large later, so a refresh adds proportionally fewer players.
    refresh_decay = max(0.4, math.exp(-hours_since_release / 72.0))
    last_hours = read_rows[-1][1]

    rows = []
    for j, trophy in enumerate(trophies):
        series = []  # (x, hours, rate)
        for (x, h, rates, _p) in read_rows:
            r = rates[j]
            if r is not None:
                series.append((x, h, r))
        if not series:
            med, sig = logit(1.0), 1.0
        else:
            last_rate = series[-1][2]
            last_logit = logit(last_rate)
            if last_rate >= SATURATED_RATE:
                med, sig = last_logit, SATURATED_SIGMA
            elif case_frozen:
                # Refresh drop: rarer trophies fall further.
                raw = REFRESH_A + REFRESH_B * (last_logit - REFRESH_REF_LOGIT)
                shift = REFRESH_SCALE * refresh_decay * raw
                shift = max(REFRESH_SHIFT_MIN, min(REFRESH_SHIFT_MAX, shift))
                med = last_logit + shift
                sig = REFRESH_SIGMA
            else:
                # Active history: trend in log-time over the recent reads.
                x_last = series[-1][0]
                recent = [(x, r) for (x, h, r) in series
                          if last_hours - h <= TREND_WINDOW_HOURS]
                if len(recent) < 2:
                    recent = [(x, r) for (x, _h, r) in series]
                xs = [x for (x, _r) in recent]
                ys = [logit(r) for (_x, r) in recent]
                if len(xs) >= 2:
                    a, b, resid = fit_line(xs, ys)
                    pred = a + b * target_x
                    move = pred - last_logit
                    # Damp the extrapolation and cap the move.
                    damp = 1.0 / (1.0 + max(0.0, target_x - x_last) / TREND_DAMP_K)
                    move = move * damp
                    move = max(-TREND_MAX_MOVE, min(TREND_MAX_MOVE, move))
                    med = last_logit + move
                    rw = RW_SIGMA_PER_SQRT_DAY * math.sqrt(horizon_days)
                    sig = math.sqrt(resid * resid + rw * rw)
                else:
                    med = last_logit
                    sig = RW_SIGMA_PER_SQRT_DAY * math.sqrt(horizon_days)
                sig = max(SIGMA_FLOOR, min(SIGMA_CAP, sig))
        qs = []
        for z in Z:
            q = inv_logit(med + z * sig)
            qs.append(round4(clamp_pct(q)))
        # Keep the five quantiles non-decreasing after rounding.
        for k in range(1, 5):
            if qs[k] < qs[k - 1]:
                qs[k] = qs[k - 1]
        rows.append({"id": trophy["id"], "q": qs})
    return {"trophies": rows}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
