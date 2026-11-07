"""Normalized match model shared by the demo parser and the log parser.

Teams use the Source engine numbering: 2 = T, 3 = CT.
Ticks are 64/s. The log parser converts wall-clock seconds to ticks so the
same trade-window logic works for both sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

T, CT = 2, 3
TICKRATE = 64


@dataclass
class Kill:
    tick: int
    attacker: Optional[str]      # steamid64 as str, None for world/bomb/fall
    victim: str
    assister: Optional[str] = None
    headshot: bool = False
    weapon: str = ""
    flash_assister: Optional[str] = None
    # loadout strength 0..1 (starter pistol .. rifle/AWP) at the moment of the kill
    attacker_strength: Optional[float] = None
    victim_strength: Optional[float] = None


@dataclass
class Damage:
    tick: int
    attacker: Optional[str]
    victim: str
    dmg: int
    weapon: str = ""


@dataclass
class Blind:
    tick: int
    attacker: Optional[str]
    victim: str
    duration: float


@dataclass
class Round:
    number: int
    winner: Optional[int]                       # T / CT / None
    start_tick: int
    roster: dict[str, int] = field(default_factory=dict)   # steamid -> side
    kills: list[Kill] = field(default_factory=list)
    damages: list[Damage] = field(default_factory=list)
    blinds: list[Blind] = field(default_factory=list)
    end_tick: Optional[int] = None              # when the round was decided
    loadout: dict[str, float] = field(default_factory=dict)  # strength at freeze end
    plant_tick: Optional[int] = None
    planter: Optional[str] = None
    defuser: Optional[str] = None


@dataclass
class MatchData:
    map_name: str
    source_name: str                      # demo filename / log segment label
    rounds: list[Round]
    names: dict[str, str]                 # steamid -> latest display name
    matchzy_id: Optional[str] = None
    played_at: Optional[str] = None       # ISO timestamp
    # Per-player totals supplied externally when per-hit damage isn't
    # available (log mode). Keys: damage, util_damage, enemies_flashed.
    damage_override: dict[str, dict[str, float]] = field(default_factory=dict)
    # Used to reject retakes / other modes. None = unknown (don't filter on it).
    game_mode: Optional[str] = None       # "competitive", "casual", "scrimcomp2v2", ...
    purchases: Optional[int] = None       # total buys; ~0 in retakes / DM-style modes


def steam3_to_64(account_id: int | str) -> str:
    return str(76561197960265728 + int(account_id))


def clean_steamid(v) -> Optional[str]:
    """demoparser2 gives steamids as int/str/float/None depending on version."""
    if v is None:
        return None
    try:
        if isinstance(v, float):
            if v != v:  # NaN
                return None
            v = int(v)
        s = str(v).strip()
        if s in ("", "0", "None", "nan"):
            return None
        return str(int(float(s))) if "e" in s.lower() else s
    except (ValueError, TypeError):
        return None
