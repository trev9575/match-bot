"""Achievement badges: original SVG artwork (no emoji, nothing copied from Valve).

Every badge is a hexagon in its tier's colors with a white line-art glyph on top.
`svg(achievement_id)` returns a standalone SVG; PNG copies for Discord thumbnails are
pre-rendered into web/ach/ by tools/render_icons.py.
"""
from __future__ import annotations

from .community import ACH

TIERS = {           # (light, dark, rim)
    "bronze": ("#d08a52", "#7a4422", "#f1b98a"),
    "silver": ("#aeb7c1", "#4d5661", "#e3e8ec"),
    "gold": ("#f4c84a", "#a8730b", "#ffe9a3"),
    "elite": ("#b77bff", "#4b1fa8", "#e2c8ff"),
    "shame": ("#8a9a8c", "#3b463d", "#c3d1c4"),
}

S = 'fill="none" stroke="#fff" stroke-linecap="round" stroke-linejoin="round"'
F = 'fill="#fff"'


def _star(cx, cy, r, points=5, inner=0.45):
    import math
    pts = []
    for i in range(points * 2):
        rr = r if i % 2 == 0 else r * inner
        a = -math.pi / 2 + i * math.pi / points
        pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
    return " ".join(pts)


def _glyph(name: str, dark: str) -> str:
    g = {
        "skull": f'<path {F} d="M32 15c-9.4 0-16 6.6-16 15 0 5.4 2.7 9.2 6 11.3V47h20v-5.7c3.3-2.1 6-5.9 6-11.3 0-8.4-6.6-15-16-15z"/>'
                 f'<circle cx="25.5" cy="31" r="4.2" fill="{dark}"/><circle cx="38.5" cy="31" r="4.2" fill="{dark}"/>'
                 f'<path d="M32 36l-2.4 4h4.8z" fill="{dark}"/>'
                 f'<path d="M27 47v-4M32 47v-4M37 47v-4" stroke="{dark}" stroke-width="2"/>',
        "four": f'<path {S} stroke-width="3.6" d="M37 47V17L19 38h26"/>',
        "hourglass": f'<path {S} stroke-width="3.6" d="M22 17h20M22 47h20M24 17c0 10 16 12 16 30M40 17c0 10-16 12-16 30"/>'
                     f'<path {F} d="M27 44c1-4 9-4 10 0z"/>',
        "snowflake": f'<path {S} stroke-width="3.6" d="M32 15v34M17.3 23.5l29.4 17M17.3 40.5l29.4-17'
                     f'M27 17.5l5 4 5-4M27 46.5l5-4 5 4M16.5 29l6.3 1-2 6M47.5 35l-6.3-1 2-6"/>',
        "keyhole": f'<circle cx="32" cy="27" r="8" {F}/><path {F} d="M28 31h8l3 16H25z"/>',
        "flame": f'<path {F} d="M32 14c2 7 10 11 10 21a10 10 0 0 1-20 0c0-5 3-8 5-10 0 4 2 6 4 6-1-6 1-12 1-17z"/>',
        "burst": f'<polygon {F} points="{_star(32, 32, 17, 8, 0.48)}"/>'
                 f'<circle cx="32" cy="32" r="4.5" fill="{dark}"/>',
        "crosshair": f'<circle cx="32" cy="32" r="12" {S} stroke-width="3.6"/><path {S} stroke-width="3.6" d="M32 14v9M32 41v9M14 32h9M41 32h9"/>'
                     f'<circle cx="32" cy="32" r="2.6" {F}/>',
        "door": f'<path {S} stroke-width="3.6" d="M21 47V17h16v30M17 47h30"/><path {F} d="M37 17l9 4v26l-9-2z"/>'
                f'<circle cx="40.5" cy="33" r="1.6" fill="{dark}"/>',
        "bolt": f'<path {F} d="M36 13L19 36h11l-3 15 18-24H34z"/>',
        "grenade": f'<circle cx="30" cy="37" r="11" {F}/><path {S} stroke-width="3.6" d="M27 25v-4h7v4M34 22l8-4 2 8"/>'
                   f'<circle cx="23" cy="19" r="3.5" {S} stroke-width="2.6"/>'
                   f'<path d="M24 37h12M30 31v12" stroke="{dark}" stroke-width="2"/>',
        "people": f'<circle cx="24" cy="25" r="5.5" {F}/><circle cx="40" cy="25" r="5.5" {F}/>'
                  f'<path {F} d="M14 45c0-7 4.5-11 10-11s10 4 10 11zM30 45c0-7 4.5-11 10-11s10 4 10 11z"/>',
        "knife": f'<path {F} d="M45 15c-4 1-17 15-21 21l4 4c6-4 19-17 20-21z"/>'
                 f'<path {S} stroke-width="3.6" d="M22 38l-6 6M20 34l10 10"/>',
        "scope": f'<circle cx="32" cy="32" r="15" {S} stroke-width="3.6"/><circle cx="32" cy="32" r="8" {S} stroke-width="2.4"/>'
                 f'<path {S} stroke-width="2.4" d="M32 13v10M32 41v10M13 32h10M41 32h10"/>',
        "shield": f'<path {S} stroke-width="3.6" d="M32 14l14 5v10c0 9-6 15-14 19-8-4-14-10-14-19V19z"/>'
                  f'<path {S} stroke-width="3.6" d="M25 31l5 5 9-10"/>',
        "dumbbell": f'<path {S} stroke-width="3.6" d="M22 32h20"/><rect x="14" y="23" width="7" height="18" rx="2" {F}/>'
                    f'<rect x="43" y="23" width="7" height="18" rx="2" {F}/>'
                    f'<path {S} stroke-width="3.6" d="M32 13v6M27 17l5-4 5 4"/>',
        "star": f'<polygon {F} points="{_star(32, 33, 17)}"/>',
        "stars": f'<polygon {F} points="{_star(32, 25, 11)}"/><polygon {F} points="{_star(20, 41, 8)}"/>'
                 f'<polygon {F} points="{_star(44, 41, 8)}"/>',
        "roller": f'<circle cx="22" cy="39" r="8" {S} stroke-width="3.6"/><path {F} d="M32 25h12l3 9v9H32z"/>'
                  f'<path {S} stroke-width="3.6" d="M30 39h17M22 31V22h10"/>',
        "arrowup": f'<path {S} stroke-width="3.6" d="M17 45c4-1 7-6 9-11s6-15 15-17"/><path {S} stroke-width="3.6" d="M33 15l9 2-3 9"/>'
                   f'<path {S} stroke-width="2.4" d="M17 47h30"/>',
        "clock": f'<circle cx="32" cy="33" r="15" {S} stroke-width="3.6"/><path {S} stroke-width="3.6" d="M32 25v9l6 4M27 14h10"/>',
        "sling": f'<path {S} stroke-width="3.6" d="M32 49V33M32 33l-10-15M32 33l10-15"/><path {S} stroke-width="2.4" d="M22 18q10 14 20 0"/>'
                 f'<circle cx="32" cy="24.5" r="3.4" {F}/>',
        "scissors": f'<circle cx="21" cy="40" r="5.5" {S} stroke-width="3.6"/><circle cx="43" cy="40" r="5.5" {S} stroke-width="3.6"/>'
                    f'<path {S} stroke-width="3.6" d="M25 36l17-21M39 36L22 15"/>',
        "bomb": f'<rect x="16" y="24" width="32" height="20" rx="3" {F}/>'
                f'<rect x="20" y="28" width="14" height="6" fill="{dark}"/>'
                f'<path d="M38 29h6M38 33h6M38 38h6M20 39h14" stroke="{dark}" stroke-width="2"/>'
                f'<path {S} stroke-width="2.6" d="M24 24c0-5 4-8 9-8s7 3 9 0"/>',
        "chevron": f'<path {S} stroke-width="4.4" d="M19 31l13-10 13 10M19 44l13-10 13 10"/>',
        "medal": f'<path {F} d="M22 13h7l5 11h-7zM42 13h-7l-5 11h7z"/><circle cx="32" cy="37" r="11" {F}/>'
                 f'<polygon fill="{dark}" points="{_star(32, 37.5, 6.5)}"/>',
        "tally": f'<path {S} stroke-width="3.6" d="M20 18v28M27 18v28M34 18v28M41 18v28M15 40l32-16"/>',
        "streak": f'<path {S} stroke-width="4.2" d="M14 21l10 11-10 11M27 21l10 11-10 11M40 21l10 11-10 11"/>',
        "globe": f'<circle cx="32" cy="32" r="15" {S} stroke-width="3.6"/><path {S} stroke-width="2.6" d="M17 32h30M32 17c-7 8-7 22 0 30M32 17c7 8 7 22 0 30M20 24h24M20 40h24"/>',
        "wing1": f'<polygon {F} points="{_star(32, 31, 9)}"/><path {F} d="M22 33c-5-1-8-4-9-8 4 2 7 2 10 2zM42 33c5-1 8-4 9-8-4 2-7 2-10 2z"/>'
                 f'<path {S} stroke-width="2.6" d="M23 45h18"/>',
        "wing2": f'<polygon {F} points="{_star(32, 30, 9)}"/><path {F} d="M23 31c-7 0-11-5-12-11 4 3 8 4 12 4l2 4zM41 31c7 0 11-5 12-11-4 3-8 4-12 4l-2 4z"/>'
                 f'<path {S} stroke-width="2.6" d="M20 43l12 5 12-5"/>',
        "eagle": f'<path {F} d="M32 22c-3 0-5 2-5 5l-14-6c2 9 8 14 15 15l-3 9h14l-3-9c7-1 13-6 15-15l-14 6c0-3-2-5-5-5z"/>'
                 f'<path fill="{dark}" d="M33 25.5l4 1.5-4 1z"/>',
        "globe2": f'<circle cx="32" cy="30" r="10.5" {S} stroke-width="3"/>'
                  f'<path {S} stroke-width="2.2" d="M21.5 30h21M32 19.5c-5 6-5 15 0 21M32 19.5c5 6 5 15 0 21"/>'
                  f'<path {S} stroke-width="2.6" d="M18 22c-3 9 0 18 8 23M46 22c3 9 0 18-8 23"/>'
                  f'<path {F} d="M27 46h10l-5 4z"/>',
        "crown": f'<path {F} d="M15 24l9 7 8-13 8 13 9-7-4 20H19z"/><path {S} stroke-width="2.6" d="M19 48h26"/>',
        "podium": f'<path {F} d="M25 26h14v22H25zM12 35h12v13H12zM40 31h12v17H40z"/>'
                  f'<polygon {F} points="{_star(32, 17, 5.5)}"/>',
        "flag": f'<path {S} stroke-width="3.6" d="M21 49V14"/><path {F} d="M23 16c6-3 10 3 16 1s5-2 7-3v16c-2 1-3 2-7 3s-10-4-16-1z"/>',
        "sunglasses": f'<path {F} d="M14 27h15v2c0 6-3 10-7.5 10S14 35 14 29zM35 27h15v2c0 6-3 10-7.5 10S35 35 35 29z"/>'
                      f'<path {S} stroke-width="2.8" d="M11 26h42M29 29.5q3-2 6 0"/>',
    }
    return g.get(name, g["star"])


def svg(aid: str, size: int = 64, locked: bool = False) -> str:
    a = ACH.get(aid)
    if not a:
        return ""
    light, dark, rim = TIERS["silver" if locked else a["tier"]]
    if locked:
        light, dark, rim = "#4a4f57", "#2a2d33", "#5d636c"
    gid = f"g{aid}{'L' if locked else ''}"
    hexagon = "32,3 57,17.5 57,46.5 32,61 7,46.5 7,17.5"
    inner = "32,8 52.7,20 52.7,44 32,56 11.3,44 11.3,20"
    glyph = _glyph(a["icon"], dark)
    fade = ' opacity=".45"' if locked else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{size}" height="{size}">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{light}"/><stop offset="1" stop-color="{dark}"/></linearGradient></defs>'
            f'<polygon points="{hexagon}" fill="{rim}"/>'
            f'<polygon points="{inner}" fill="url(#{gid})"/>'
            f'<g transform="translate(32 32) scale(0.86) translate(-32 -32)"'
            f'{fade}>{glyph}</g></svg>')
