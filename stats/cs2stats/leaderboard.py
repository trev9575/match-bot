"""Leaderboard stat registry + Leetify-style category scores.

Category scores are 0-100 percentiles *within your group*, so 50 means
"middle of the friend group", not "average CS2 player".
"""
from __future__ import annotations

# key: (label, format, higher_is_better)
STATS = {
    "rating":          ("Rating 3.0",       "{:.2f}", True),
    "swing_pr":        ("Round Swing %",    "{:+.2f}", True),
    "kd":              ("K/D",              "{:.2f}", True),
    "adr":             ("ADR",              "{:.1f}", True),
    "kast":            ("KAST %",           "{:.1f}", True),
    "hs_pct":          ("Headshot %",       "{:.1f}", True),
    "win_pct":         ("Win %",            "{:.0f}", True),
    "entry_win_pct":   ("Opening duel win %", "{:.0f}", True),
    "trade_kpr":       ("Trade kills / 100 rds", "{:.1f}", True),
    "traded_pct":      ("Deaths traded %",  "{:.0f}", True),
    "clutch_wins":     ("Clutches won",     "{:.0f}", True),
    "clutch_win_pct":  ("Clutch win %",     "{:.0f}", True),
    "multikill_pct":   ("Multi-kill rounds %", "{:.1f}", True),
    "util_adr":        ("Utility dmg / round", "{:.1f}", True),
    "flashed_pr":      ("Enemies flashed / round", "{:.2f}", True),
    "flash_assists":   ("Flash assists",    "{:.0f}", True),
    "team_flashed":    ("Teammates flashed 🙃", "{:.0f}", True),
    "aces":            ("Aces",             "{:.0f}", True),
}

CATEGORIES = {
    "Firepower": ["kpr", "adr", "multikill_pct"],
    "Entrying":  ["entry_rate", "entry_win_pct"],
    "Trading":   ["trade_kpr", "traded_pct"],
    "Clutching": ["clutch_win_pct", "clutch_wins"],
    "Utility":   ["util_adr", "flashed_pr", "flash_assists_pr"],
}


def _percentiles(rows, key):
    vals = sorted(r[key] or 0 for r in rows)
    n = len(vals)
    out = {}
    for r in rows:
        v = r[key] or 0
        below = sum(1 for x in vals if x < v)
        equal = sum(1 for x in vals if x == v)
        out[r["steamid"]] = 100 * (below + 0.5 * (equal - 1)) / max(n - 1, 1)
    return out


def with_categories(rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    for cat, keys in CATEGORIES.items():
        pcts = [_percentiles(rows, k) for k in keys]
        for r in rows:
            r[cat.lower()] = round(sum(p[r["steamid"]] for p in pcts) / len(pcts))
    return rows


def ranked(rows, stat, min_matches=1, limit=15):
    rows = [r for r in rows if r["matches"] >= min_matches]
    rows = with_categories(rows)
    hib = STATS.get(stat, (None, None, True))[2]
    return sorted(rows, key=lambda r: (r.get(stat) or 0), reverse=hib)[:limit]
