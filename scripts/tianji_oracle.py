#!/usr/bin/env python3
"""Ziwei Doushu 'Tianji' (天纪) Astrological Engine for World Cup predictions.

Plots a simplified Chinese lunar horoscope for the match kickoff hour
to determine metaphysical support for host (Self Palace) and guest (Travel Palace).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

# 12 earthly branches
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

SHICHEN_NAMES = [
    "子时 (23:00-00:59)",
    "丑时 (01:00-02:59)",
    "寅时 (03:00-04:59)",
    "卯时 (05:00-06:59)",
    "辰时 (07:00-08:59)",
    "巳时 (09:00-10:59)",
    "午时 (11:00-12:59)",
    "未时 (13:00-14:59)",
    "申时 (15:00-16:59)",
    "酉时 (17:00-18:59)",
    "戌时 (19:00-20:59)",
    "亥时 (21:00-22:59)",
]


VENUE_TIMEZONES = {
    "mexico city": "America/Mexico_City",
    "zapopan": "America/Mexico_City",
    "guadalupe": "America/Monterrey",
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",
    "seattle": "America/Los_Angeles",
    "santa clara": "America/Los_Angeles",
    "inglewood": "America/Los_Angeles",
    "atlanta": "America/New_York",
    "east rutherford": "America/New_York",
    "foxborough": "America/New_York",
    "philadelphia": "America/New_York",
    "miami gardens": "America/New_York",
    "houston": "America/Chicago",
    "arlington": "America/Chicago",
    "kansas city": "America/Chicago",
}


def timezone_for_venue(venue: str | None) -> str:
    haystack = str(venue or "").lower()
    for key, tz_name in VENUE_TIMEZONES.items():
        if key in haystack:
            return tz_name
    return "Asia/Shanghai"


def infer_timezone_from_venue(venue: str | None) -> str:
    return timezone_for_venue(venue)


def get_lunar_date_2026(dt: datetime) -> tuple[int, int]:
    """Convert solar datetime in June/July 2026 to lunar (month, day)."""
    d = dt.date()

    # 2026-06-01 is Month 4, Day 16 (四月十六)
    ref = date(2026, 6, 1)
    diff = (d - ref).days

    # Starting from Month 4 Day 16
    day_in_month_4 = 16 + diff
    if day_in_month_4 <= 29:  # Month 4 has 29 days
        return 4, day_in_month_4

    day_in_month_5 = day_in_month_4 - 29
    if day_in_month_5 <= 29:  # Month 5 has 29 days
        return 5, day_in_month_5

    day_in_month_6 = day_in_month_5 - 29
    return 6, day_in_month_6


def get_shichen_idx(hour: int) -> int:
    """Map 24h hour to earthly branch index (0-11)."""
    return (hour + 1) // 2 % 12


def compute_tianji_overlay(kickoff_str: str, match_id: str, venue: str | None = None) -> dict:
    """Compute the Tianji astrology overlay for a match.

    Args:
        kickoff_str: ISO-8601 kickoff datetime string.
        match_id: Unique identifier of the match.
    """
    # Parse kickoff datetime
    try:
        if kickoff_str.endswith("Z"):
            kickoff_str = kickoff_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(kickoff_str)
    except Exception:
        dt = datetime.now(ZoneInfo("Asia/Shanghai"))

    calculation_timezone = timezone_for_venue(venue)
    dt_local = dt.astimezone(ZoneInfo(calculation_timezone))
    lunar_month, lunar_day = get_lunar_date_2026(dt_local)
    shichen_idx = get_shichen_idx(dt_local.hour)
    shichen_name = SHICHEN_NAMES[shichen_idx]

    # --- Simplified Ziwei Doushu Palace Mapping ---
    # Self Palace (命宫) represents Home team (Host)
    # Travel Palace (迁移宫) represents Away team (Guest/Traveler)
    self_idx = (lunar_month - 1 - shichen_idx + 12) % 12
    travel_idx = (self_idx + 6) % 12

    # --- Star Positions Mapping (Deterministic hashes based on lunar date) ---
    stars_position = {
        "紫微 (Ziwei)": (lunar_day * 3 + lunar_month) % 12,
        "天府 (Tianfu)": (12 - ((lunar_day * 3 + lunar_month) % 12)) % 12,
        "太阳 (Taiyang)": (shichen_idx + 3) % 12,
        "太阴 (Taiyin)": (12 - ((shichen_idx + 3) % 12)) % 12,
        "左辅 (Zuofu)": (lunar_month + 3) % 12,
        "右弼 (Youbi)": (12 - lunar_month) % 12,
        "擎羊 (Qingyang)": (lunar_day * 7) % 12,
        "陀罗 (Tuoluo)": (((lunar_day * 7) % 12) + 2) % 12,
        "火星 (Huoxing)": (shichen_idx * 5 + lunar_month) % 12,
        "化忌 (Huaji)": (lunar_day * 5) % 12,
    }

    # Identify which stars land in Self (Home) and Travel (Away) palaces
    home_stars = []
    away_stars = []

    for star_name, star_idx in stars_position.items():
        if star_idx == self_idx:
            home_stars.append(star_name)
        if star_idx == travel_idx:
            away_stars.append(star_name)

    # --- Score Modifiers ---
    # Auspicious stars (紫微, 天府, 太阳, 太阴, 左辅, 右弼) add points.
    # Inauspicious stars (擎羊, 陀罗, 火星, 化忌) subtract points.
    def get_score_modifier(stars: list[str]) -> float:
        score = 0.0
        for star in stars:
            if "紫微" in star or "天府" in star:
                score += 1.0
            elif any(g in star for g in ["太阳", "太阴", "左辅", "右弼"]):
                score += 0.5
            elif "化忌" in star:
                score -= 1.0
            elif any(b in star for b in ["擎羊", "陀罗", "火星"]):
                score -= 0.5
        return score

    home_mod = round(max(-3.0, min(3.0, get_score_modifier(home_stars))), 1)
    away_mod = round(max(-3.0, min(3.0, get_score_modifier(away_stars))), 1)

    # --- Narrative Interpretations ---
    narratives = []
    has_conflict = False

    # High-physical conflict warning (Qingyang/Tuoluo present in active palaces)
    if "擎羊 (Qingyang)" in home_stars or "擎羊 (Qingyang)" in away_stars or "陀罗 (Tuoluo)" in home_stars or "陀罗 (Tuoluo)" in away_stars:
        has_conflict = True
        narratives.append("星盘羊陀会照，物理对抗剧烈，黄牌数目可能偏多。")

    # Mistake warning (Huaji present in active palaces)
    if "化忌 (Huaji)" in home_stars:
        narratives.append("主队命宫逢化忌，防守端须防重大失误或点球送礼。")
    if "化忌 (Huaji)" in away_stars:
        narratives.append("客队迁移宫遇化忌，异地奔波运势受阻，防守反击恐受挫。")

    # Auspicious summary
    if "紫微 (Ziwei)" in home_stars or "天府 (Tianfu)" in home_stars:
        narratives.append("主队命宫得帝星/府星高照，占据天时地利，发挥较稳健。")
    if "紫微 (Ziwei)" in away_stars or "天府 (Tianfu)" in away_stars:
        narratives.append("客队迁移宫吉曜临门，客战气势如虹，不容小觑。")

    if not narratives:
        narratives.append("双方星盘吉凶平稳，气运相持，最终走向更依赖战术和硬实力硬战。")

    interpretation = " ；".join(narratives)

    # Add a zodiac animal based on Year (2026 is Year of the Horse 丙午马)
    zodiac_year = "丙午马年"
    lunar_date_str = f"农历：{zodiac_year} {lunar_month}月{lunar_day}日 {shichen_name}"

    return {
        "lunar_date": lunar_date_str,
        "shichen": shichen_name,
        "host_palace_branch": BRANCHES[self_idx],
        "guest_palace_branch": BRANCHES[travel_idx],
        "home_stars": home_stars,
        "away_stars": away_stars,
        "home_modifier": home_mod,
        "away_modifier": away_mod,
        "interpretation": interpretation,
        "has_physical_conflict": has_conflict,
        "calculation_timezone": calculation_timezone,
        "local_kickoff_at": dt_local.isoformat(),
    }


if __name__ == "__main__":
    # Test execution
    res = compute_tianji_overlay("2026-06-11T19:00:00Z", "2026-GA-01")
    import json
    print(json.dumps(res, ensure_ascii=False, indent=2))
