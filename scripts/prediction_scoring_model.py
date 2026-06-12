#!/usr/bin/env python3
"""Explainable prediction scoring model for World Cup matches.

Computes a data-driven score (60%) combined with a deterministic
Tianji entertainment overlay (40%) for each upcoming match on a given date.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    DATA_WEIGHT,
    DISCLAIMER,
    DIVINATION_WEIGHT,
    edition_data_root,
    iso_now,
    load_json,
    load_match_ledger,
    match_on_date,
    match_started,
    now_datetime,
    parse_datetime,
    write_json,
)

from tianji_oracle import compute_tianji_overlay


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Component weights inside the data_score (must sum to 1.0)
W_RANKING_STRENGTH = 0.30
W_SQUAD_DEPTH = 0.20
W_HISTORICAL_PROXY = 0.20
W_REST_TRAVEL = 0.15
W_EVIDENCE_COMPLETENESS = 0.15


def load_hyperparameters(root: Path, edition: str) -> None:
    """Load hyperparameters from JSON and update global weight values."""
    global W_RANKING_STRENGTH, W_SQUAD_DEPTH, W_HISTORICAL_PROXY, W_REST_TRAVEL, W_EVIDENCE_COMPLETENESS
    global DATA_WEIGHT, DIVINATION_WEIGHT
    from worldcup_core import edition_data_root, load_json
    import worldcup_core

    # First trigger worldcup_core reload
    worldcup_core.load_hyperparameters(root, edition)
    DATA_WEIGHT = worldcup_core.DATA_WEIGHT
    DIVINATION_WEIGHT = worldcup_core.DIVINATION_WEIGHT

    path = edition_data_root(root, edition) / "model-hyperparameters.json"
    if path.exists():
        try:
            data = load_json(path, {})
            comp = data.get("component_weights", {})
            if "ranking_strength" in comp:
                W_RANKING_STRENGTH = float(comp["ranking_strength"])
            if "squad_depth" in comp:
                W_SQUAD_DEPTH = float(comp["squad_depth"])
            if "historical_proxy" in comp:
                W_HISTORICAL_PROXY = float(comp["historical_proxy"])
            if "rest_travel" in comp:
                W_REST_TRAVEL = float(comp["rest_travel"])
            if "evidence_completeness" in comp:
                W_EVIDENCE_COMPLETENESS = float(comp["evidence_completeness"])
        except Exception as e:
            print(f"Warning: Failed to load hyperparameters from {path}: {e}", file=sys.stderr)

# Ranking points range for normalisation (approximate FIFA men's range)
_RANKING_POINTS_MIN = 1200.0
_RANKING_POINTS_MAX = 1900.0

# Maximum data_score before Tianji overlay
_DATA_SCORE_CAP = 100.0

# Maximum divination modifier (absolute value)
_DIVINATION_MODIFIER_MAX = 3.0

# Host nations for 2026 (home advantage bonus)
_HOST_NATIONS_2026 = {"mex", "usa", "can"}

_VENUE_CONTEXTS = {
    "mexico city": {"city": "Mexico City", "country": "Mexico", "lat": 19.4326, "lon": -99.1332, "june_temp_c": 18.0, "altitude_m": 2240, "climate_profile": "high_altitude_mild"},
    "zapopan": {"city": "Zapopan", "country": "Mexico", "lat": 20.6597, "lon": -103.3496, "june_temp_c": 23.0, "altitude_m": 1560, "climate_profile": "warm_highland"},
    "guadalupe": {"city": "Guadalupe", "country": "Mexico", "lat": 25.6866, "lon": -100.3161, "june_temp_c": 28.0, "altitude_m": 540, "climate_profile": "hot_semidry"},
    "toronto": {"city": "Toronto", "country": "Canada", "lat": 43.6532, "lon": -79.3832, "june_temp_c": 20.0, "altitude_m": 76, "climate_profile": "temperate_lakeside"},
    "atlanta": {"city": "Atlanta", "country": "United States", "lat": 33.7490, "lon": -84.3880, "june_temp_c": 26.0, "altitude_m": 320, "climate_profile": "warm_humid"},
    "santa clara": {"city": "Santa Clara", "country": "United States", "lat": 37.3541, "lon": -121.9552, "june_temp_c": 18.0, "altitude_m": 22, "climate_profile": "mild_marine"},
    "inglewood": {"city": "Inglewood", "country": "United States", "lat": 33.9533, "lon": -118.3390, "june_temp_c": 20.0, "altitude_m": 40, "climate_profile": "mild_coastal"},
    "vancouver": {"city": "Vancouver", "country": "Canada", "lat": 49.2827, "lon": -123.1207, "june_temp_c": 17.0, "altitude_m": 70, "climate_profile": "cool_marine"},
    "seattle": {"city": "Seattle", "country": "United States", "lat": 47.6062, "lon": -122.3321, "june_temp_c": 17.0, "altitude_m": 52, "climate_profile": "cool_marine"},
    "east rutherford": {"city": "East Rutherford", "country": "United States", "lat": 40.8339, "lon": -74.0971, "june_temp_c": 22.0, "altitude_m": 20, "climate_profile": "warm_temperate"},
    "foxborough": {"city": "Foxborough", "country": "United States", "lat": 42.0654, "lon": -71.2478, "june_temp_c": 20.0, "altitude_m": 88, "climate_profile": "temperate"},
    "philadelphia": {"city": "Philadelphia", "country": "United States", "lat": 39.9526, "lon": -75.1652, "june_temp_c": 24.0, "altitude_m": 12, "climate_profile": "warm_humid"},
    "miami gardens": {"city": "Miami Gardens", "country": "United States", "lat": 25.9420, "lon": -80.2456, "june_temp_c": 28.5, "altitude_m": 2, "climate_profile": "hot_humid"},
    "houston": {"city": "Houston", "country": "United States", "lat": 29.7604, "lon": -95.3698, "june_temp_c": 29.0, "altitude_m": 13, "climate_profile": "hot_humid"},
    "arlington": {"city": "Arlington", "country": "United States", "lat": 32.7357, "lon": -97.1081, "june_temp_c": 29.0, "altitude_m": 184, "climate_profile": "hot_inland"},
    "kansas city": {"city": "Kansas City", "country": "United States", "lat": 39.0997, "lon": -94.5786, "june_temp_c": 25.0, "altitude_m": 277, "climate_profile": "warm_inland"},
}

_TEAM_HOME_CONTEXTS = {
    "arg": {"city": "Buenos Aires", "lat": -34.6037, "lon": -58.3816, "june_temp_c": 12.0, "altitude_m": 25, "climate_profile": "cool_temperate"},
    "aus": {"city": "Sydney", "lat": -33.8688, "lon": 151.2093, "june_temp_c": 13.0, "altitude_m": 58, "climate_profile": "cool_coastal"},
    "bel": {"city": "Brussels", "lat": 50.8503, "lon": 4.3517, "june_temp_c": 17.0, "altitude_m": 13, "climate_profile": "temperate_marine"},
    "bih": {"city": "Sarajevo", "lat": 43.8563, "lon": 18.4131, "june_temp_c": 19.0, "altitude_m": 518, "climate_profile": "temperate_highland"},
    "bra": {"city": "Rio de Janeiro", "lat": -22.9068, "lon": -43.1729, "june_temp_c": 22.0, "altitude_m": 5, "climate_profile": "warm_coastal"},
    "can": {"city": "Toronto", "lat": 43.6532, "lon": -79.3832, "june_temp_c": 20.0, "altitude_m": 76, "climate_profile": "temperate_lakeside"},
    "col": {"city": "Bogota", "lat": 4.7110, "lon": -74.0721, "june_temp_c": 14.0, "altitude_m": 2640, "climate_profile": "cool_high_altitude"},
    "cze": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "june_temp_c": 18.0, "altitude_m": 200, "climate_profile": "temperate"},
    "ecu": {"city": "Quito", "lat": -0.1807, "lon": -78.4678, "june_temp_c": 14.0, "altitude_m": 2850, "climate_profile": "cool_high_altitude"},
    "egy": {"city": "Cairo", "lat": 30.0444, "lon": 31.2357, "june_temp_c": 28.0, "altitude_m": 23, "climate_profile": "hot_dry"},
    "eng": {"city": "London", "lat": 51.5074, "lon": -0.1278, "june_temp_c": 17.0, "altitude_m": 11, "climate_profile": "temperate_marine"},
    "esp": {"city": "Madrid", "lat": 40.4168, "lon": -3.7038, "june_temp_c": 24.0, "altitude_m": 667, "climate_profile": "warm_highland"},
    "fra": {"city": "Paris", "lat": 48.8566, "lon": 2.3522, "june_temp_c": 19.0, "altitude_m": 35, "climate_profile": "temperate"},
    "ger": {"city": "Berlin", "lat": 52.5200, "lon": 13.4050, "june_temp_c": 18.0, "altitude_m": 34, "climate_profile": "temperate"},
    "gha": {"city": "Accra", "lat": 5.6037, "lon": -0.1870, "june_temp_c": 26.0, "altitude_m": 61, "climate_profile": "hot_humid"},
    "irn": {"city": "Tehran", "lat": 35.6892, "lon": 51.3890, "june_temp_c": 28.0, "altitude_m": 1200, "climate_profile": "hot_highland"},
    "jpn": {"city": "Tokyo", "lat": 35.6762, "lon": 139.6503, "june_temp_c": 22.0, "altitude_m": 40, "climate_profile": "warm_humid"},
    "kor": {"city": "Seoul", "lat": 37.5665, "lon": 126.9780, "june_temp_c": 22.0, "altitude_m": 38, "climate_profile": "warm_humid"},
    "mar": {"city": "Rabat", "lat": 34.0209, "lon": -6.8416, "june_temp_c": 22.0, "altitude_m": 75, "climate_profile": "warm_mediterranean"},
    "mex": {"city": "Mexico City", "lat": 19.4326, "lon": -99.1332, "june_temp_c": 18.0, "altitude_m": 2240, "climate_profile": "high_altitude_mild"},
    "ned": {"city": "Amsterdam", "lat": 52.3676, "lon": 4.9041, "june_temp_c": 16.0, "altitude_m": 2, "climate_profile": "temperate_marine"},
    "por": {"city": "Lisbon", "lat": 38.7223, "lon": -9.1393, "june_temp_c": 21.0, "altitude_m": 2, "climate_profile": "warm_mediterranean"},
    "rsa": {"city": "Johannesburg", "lat": -26.2041, "lon": 28.0473, "june_temp_c": 10.0, "altitude_m": 1753, "climate_profile": "cool_highland"},
    "sui": {"city": "Bern", "lat": 46.9480, "lon": 7.4474, "june_temp_c": 17.0, "altitude_m": 540, "climate_profile": "temperate_highland"},
    "uru": {"city": "Montevideo", "lat": -34.9011, "lon": -56.1645, "june_temp_c": 12.0, "altitude_m": 43, "climate_profile": "cool_temperate"},
    "usa": {"city": "Kansas City", "lat": 39.0997, "lon": -94.5786, "june_temp_c": 25.0, "altitude_m": 277, "climate_profile": "warm_inland"},
}

# 64 hexagrams of the I Ching (Zhouyi) with short interpretations
_HEXAGRAMS = [
    (1, "乾 (The Creative)", "天行健，君子以自强不息"),
    (2, "坤 (The Receptive)", "地势坤，君子以厚德载物"),
    (3, "屯 (Difficulty at the Beginning)", "云雷屯，君子以经纶"),
    (4, "蒙 (Youthful Folly)", "山下出泉，蒙"),
    (5, "需 (Waiting)", "云上于天，需"),
    (6, "讼 (Conflict)", "天与水违行，讼"),
    (7, "师 (The Army)", "地中有水，师"),
    (8, "比 (Holding Together)", "地上有水，比"),
    (9, "小畜 (Small Taming)", "风行天上，小畜"),
    (10, "履 (Treading)", "上天下泽，履"),
    (11, "泰 (Peace)", "天地交，泰"),
    (12, "否 (Standstill)", "天地不交，否"),
    (13, "同人 (Fellowship)", "天与火，同人"),
    (14, "大有 (Great Possession)", "火在天上，大有"),
    (15, "谦 (Modesty)", "地中有山，谦"),
    (16, "豫 (Enthusiasm)", "雷出地奋，豫"),
    (17, "随 (Following)", "泽中有雷，随"),
    (18, "蛊 (Work on the Decayed)", "山下有风，蛊"),
    (19, "临 (Approach)", "泽上有地，临"),
    (20, "观 (Contemplation)", "风行地上，观"),
    (21, "噬嗑 (Biting Through)", "雷电噬嗑"),
    (22, "贲 (Grace)", "山下有火，贲"),
    (23, "剥 (Splitting Apart)", "山附于地，剥"),
    (24, "复 (Return)", "雷在地中，复"),
    (25, "无妄 (Innocence)", "天下雷行，物与无妄"),
    (26, "大畜 (Great Taming)", "天在山中，大畜"),
    (27, "颐 (Nourishment)", "山下有雷，颐"),
    (28, "大过 (Great Excess)", "泽灭木，大过"),
    (29, "坎 (The Abysmal)", "水洊至，习坎"),
    (30, "离 (The Clinging)", "明两作，离"),
    (31, "咸 (Influence)", "山上有泽，咸"),
    (32, "恒 (Duration)", "雷风恒"),
    (33, "遁 (Retreat)", "天下有山，遁"),
    (34, "大壮 (Great Power)", "雷在天上，大壮"),
    (35, "晋 (Progress)", "明出地上，晋"),
    (36, "明夷 (Darkening of the Light)", "明入地中，明夷"),
    (37, "家人 (The Family)", "风自火出，家人"),
    (38, "睽 (Opposition)", "上火下泽，睽"),
    (39, "蹇 (Obstruction)", "山上有水，蹇"),
    (40, "解 (Deliverance)", "雷雨作，解"),
    (41, "损 (Decrease)", "山下有泽，损"),
    (42, "益 (Increase)", "风雷益，上巽下震"),
    (43, "夬 (Breakthrough)", "泽上于天，夬"),
    (44, "姤 (Coming to Meet)", "天下有风，姤"),
    (45, "萃 (Gathering Together)", "泽上于地，萃"),
    (46, "升 (Pushing Upward)", "地中生木，升"),
    (47, "困 (Oppression)", "泽无水，困"),
    (48, "井 (The Well)", "木上有水，井"),
    (49, "革 (Revolution)", "泽中有火，革"),
    (50, "鼎 (The Cauldron)", "木上有火，鼎"),
    (51, "震 (The Arousing)", "洊雷震"),
    (52, "艮 (Keeping Still)", "兼山艮"),
    (53, "渐 (Development)", "山上有木，渐"),
    (54, "归妹 (The Marrying Maiden)", "泽上有雷，归妹"),
    (55, "丰 (Abundance)", "雷电皆至，丰"),
    (56, "旅 (The Wanderer)", "山上有火，旅"),
    (57, "巽 (The Gentle)", "随风巽"),
    (58, "兑 (The Joyous)", "丽泽兑"),
    (59, "涣 (Dispersion)", "风行水上，涣"),
    (60, "节 (Limitation)", "泽上有水，节"),
    (61, "中孚 (Inner Truth)", "泽上有风，中孚"),
    (62, "小过 (Small Excess)", "山上有雷，小过"),
    (63, "既济 (After Completion)", "水在火上，既济"),
    (64, "未济 (Before Completion)", "火在水上，未济"),
]


# ---------------------------------------------------------------------------
# Helpers: data look-ups
# ---------------------------------------------------------------------------


def _build_ranking_index(rankings_data: dict) -> dict[str, dict]:
    """Map upper-cased team_code -> ranking record."""
    index: dict[str, dict] = {}
    for entry in rankings_data.get("rankings", []):
        code = str(entry.get("team_code", "")).upper()
        if code:
            index[code] = entry
    return index


def _build_squad_index(squad_data: dict) -> dict[str, dict]:
    """Map upper-cased team_id -> squad depth record."""
    index: dict[str, dict] = {}
    for team in squad_data.get("teams", []):
        tid = str(team.get("team_id", "")).upper()
        if tid:
            index[tid] = team
    return index


def _build_evidence_index(evidence_plan: dict) -> dict[str, dict]:
    """Map evidence_id -> evidence item."""
    index: dict[str, dict] = {}
    for item in evidence_plan.get("items", []):
        eid = item.get("evidence_id", "")
        if eid:
            index[eid] = item
    return index


def _normalise_team_query(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _match_teams(match: dict, teams: list[str] | None) -> bool:
    if not teams:
        return True
    expected = {_normalise_team_query(team) for team in teams if team.strip()}
    if len(expected) != 2:
        return False
    home = match.get("home_team", {})
    away = match.get("away_team", {})
    actual = {
        _normalise_team_query(str(home.get("name") or "")),
        _normalise_team_query(str(home.get("team_id") or "")),
        _normalise_team_query(str(away.get("name") or "")),
        _normalise_team_query(str(away.get("team_id") or "")),
    }
    return expected.issubset(actual)


def _lookup_team(team_id: str, index: dict[str, dict]) -> dict | None:
    return index.get(team_id.upper())


def _get_last_match_date(team_id: str, matches: list[dict], current_kickoff: datetime) -> datetime | None:
    """Find the most recent prior match for *team_id* before *current_kickoff*."""
    team_upper = team_id.upper()
    latest: datetime | None = None
    for match in matches:
        kickoff = parse_datetime(str(match.get("kickoff_at", "")))
        if not kickoff:
            continue
        if kickoff >= current_kickoff:
            continue
        home = str(match.get("home_team", {}).get("team_id", "")).upper()
        away = str(match.get("away_team", {}).get("team_id", "")).upper()
        if home == team_upper or away == team_upper:
            if latest is None or kickoff > latest:
                latest = kickoff
    return latest


def _match_venue_context(venue: str) -> dict | None:
    haystack = str(venue or "").lower()
    for key, context in _VENUE_CONTEXTS.items():
        if key in haystack:
            result = dict(context)
            result["matched_key"] = key
            return result
    return None


def _haversine_km(origin: dict, destination: dict) -> float | None:
    try:
        lat1 = math.radians(float(origin["lat"]))
        lon1 = math.radians(float(origin["lon"]))
        lat2 = math.radians(float(destination["lat"]))
        lon2 = math.radians(float(destination["lon"]))
    except (KeyError, TypeError, ValueError):
        return None
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return round(6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def _signed_delta(value: float | int | None, *, unit: str = "") -> str:
    if value is None:
        return "unknown"
    number = float(value)
    sign = "+" if number > 0 else ""
    if unit == "km":
        return f"{sign}{number:.0f}km"
    if unit == "m":
        return f"{sign}{number:.0f}m"
    if unit == "c":
        return f"{sign}{number:.1f}C"
    return f"{sign}{number:.1f}"


def _adaptation_risk_score(*, travel_km: float | None, temperature_delta_c: float | None, altitude_delta_m: float | None) -> tuple[int, str]:
    if travel_km is None or temperature_delta_c is None or altitude_delta_m is None:
        return 0, "unknown"
    score = 0
    if travel_km >= 9000:
        score += 2
    elif travel_km >= 5000:
        score += 1
    if abs(temperature_delta_c) >= 10:
        score += 2
    elif abs(temperature_delta_c) >= 6:
        score += 1
    if altitude_delta_m >= 1500:
        score += 2
    elif altitude_delta_m >= 800:
        score += 1
    if score >= 4:
        return score, "high"
    if score >= 2:
        return score, "medium"
    return score, "low"


def _build_team_adaptation_context(team_id: str, venue_context: dict | None) -> dict:
    team_key = str(team_id or "").lower()
    team_context = _TEAM_HOME_CONTEXTS.get(team_key)
    missing_context: list[str] = []
    if not team_context:
        missing_context.append("team_home_context_missing")
    if not venue_context:
        missing_context.append("venue_context_missing")

    base = {
        "team_id": team_key,
        "status": "missing_context" if missing_context else "estimated_static_context",
        "baseline_city": team_context.get("city") if team_context else None,
        "baseline_climate_profile": team_context.get("climate_profile") if team_context else None,
        "missing_context": missing_context,
    }
    if missing_context:
        base.update(
            {
                "travel_km": None,
                "temperature_delta_c": None,
                "altitude_delta_m": None,
                "adaptation_risk": "unknown",
                "adaptation_risk_score": 0,
                "adaptation_notes": ["Static baseline context is incomplete for this side."],
            }
        )
        return base

    travel_km = _haversine_km(team_context, venue_context)
    temperature_delta_c = round(float(venue_context["june_temp_c"]) - float(team_context["june_temp_c"]), 1)
    altitude_delta_m = int(round(float(venue_context["altitude_m"]) - float(team_context["altitude_m"])))
    risk_score, risk_label = _adaptation_risk_score(
        travel_km=travel_km,
        temperature_delta_c=temperature_delta_c,
        altitude_delta_m=altitude_delta_m,
    )

    notes: list[str] = []
    if travel_km is not None:
        if travel_km >= 9000:
            notes.append("long-haul travel load")
        elif travel_km >= 5000:
            notes.append("intercontinental travel load")
        elif travel_km <= 500:
            notes.append("short travel footprint")
        else:
            notes.append("moderate travel footprint")
    if abs(temperature_delta_c) >= 6:
        direction = "hotter" if temperature_delta_c > 0 else "cooler"
        notes.append(f"venue baseline is {abs(temperature_delta_c):.1f}C {direction} than home baseline")
    else:
        notes.append("temperature baseline is broadly familiar")
    if altitude_delta_m >= 1500:
        notes.append("major altitude gain")
    elif altitude_delta_m >= 800:
        notes.append("meaningful altitude gain")
    elif altitude_delta_m <= -800:
        notes.append("large altitude drop")

    base.update(
        {
            "travel_km": travel_km,
            "temperature_delta_c": temperature_delta_c,
            "altitude_delta_m": altitude_delta_m,
            "adaptation_risk": risk_label,
            "adaptation_risk_score": risk_score,
            "adaptation_notes": notes,
        }
    )
    return base


def _build_venue_adaptation_context(match: dict, home_id: str, away_id: str) -> dict:
    venue_context = _match_venue_context(str(match.get("venue", "")))
    home_context = _build_team_adaptation_context(home_id, venue_context)
    away_context = _build_team_adaptation_context(away_id, venue_context)
    missing_context: list[str] = []
    if not venue_context:
        missing_context.append("venue_context_missing")
    missing_context.extend(f"home_{item}" for item in home_context.get("missing_context", []) if item != "venue_context_missing")
    missing_context.extend(f"away_{item}" for item in away_context.get("missing_context", []) if item != "venue_context_missing")
    status = "unavailable" if not venue_context else "partial_static_context" if missing_context else "estimated_static_context"
    return {
        "status": status,
        "source": "static_venue_team_baseline_v1",
        "venue": str(match.get("venue", "")),
        "venue_context": venue_context,
        "home": home_context,
        "away": away_context,
        "missing_context": missing_context,
        "limitations": [
            "Static baseline only; not live kickoff weather.",
            "Travel is great-circle distance from team baseline city to venue city, not actual camp or flight routing.",
            "Does not yet include humidity, wind, pitch state, player club climate, or acclimatization camp data.",
        ],
    }


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------


def _normalise_ranking_points(points: float) -> float:
    """Scale FIFA ranking points to a 0-100 range."""
    span = _RANKING_POINTS_MAX - _RANKING_POINTS_MIN
    if span <= 0:
        return 50.0
    clamped = max(_RANKING_POINTS_MIN, min(_RANKING_POINTS_MAX, points))
    return round(((clamped - _RANKING_POINTS_MIN) / span) * 100.0, 2)


def score_ranking_strength(team_ranking: dict | None) -> float:
    """Component 1: ranking strength (0-100)."""
    if not team_ranking:
        return 30.0  # neutral baseline when ranking unknown
    return _normalise_ranking_points(float(team_ranking.get("points", 0)))


def score_squad_depth(team_squad: dict | None, global_summary: dict | None) -> float:
    """Component 2: squad depth / position balance (0-100).

    Evaluates GK/DF/MF/FW balance relative to global averages, plus
    average age and height proximity to global norms.
    """
    if not team_squad:
        return 40.0  # neutral when squad data missing

    pos = team_squad.get("position_counts", {})
    gk = pos.get("GK", 0)
    df = pos.get("DF", 0)
    mf = pos.get("MF", 0)
    fw = pos.get("FW", 0)
    total = gk + df + mf + fw
    if total == 0:
        return 40.0

    # Ideal ratio (approximate global averages): GK ~11.5%, DF ~33.5%, MF ~30%, FW ~25%
    ideal = {"GK": 0.115, "DF": 0.335, "MF": 0.300, "FW": 0.250}
    actual = {"GK": gk / total, "DF": df / total, "MF": mf / total, "FW": fw / total}
    balance_penalty = sum(abs(actual[k] - ideal[k]) for k in ideal)  # 0 = perfect, max ~1.0
    balance_score = max(0.0, 100.0 - balance_penalty * 100.0)

    # Age proximity (ideal ~27.5-28.5 for international squads)
    avg_age = float(team_squad.get("avg_age_years", 27.5))
    age_diff = abs(avg_age - 28.0)
    age_score = max(0.0, 100.0 - age_diff * 10.0)

    # Height proximity (ideal ~183cm for international squads)
    avg_height = float(team_squad.get("avg_height_cm", 183.0))
    height_diff = abs(avg_height - 183.0)
    height_score = max(0.0, 100.0 - height_diff * 5.0)

    # Combine: balance 50%, age 25%, height 25%
    combined = balance_score * 0.50 + age_score * 0.25 + height_score * 0.25
    return round(min(100.0, max(0.0, combined)), 2)


def score_historical_proxy(team_ranking: dict | None) -> float:
    """Component 3: historical performance proxy (0-100).

    Uses FIFA ranking as a proxy — higher-ranked teams generally have
    stronger World Cup pedigrees.  This is a placeholder until actual
    historical results data is ingested.
    """
    if not team_ranking:
        return 30.0
    points = float(team_ranking.get("points", 0))
    return _normalise_ranking_points(points)


def score_rest_travel(
    *,
    team_id: str,
    is_home: bool,
    current_kickoff: datetime,
    all_matches: list[dict],
    edition: str,
) -> float:
    """Component 4: rest / travel factor (0-100).

    - Base score 70 (neutral).
    - Bonus for more days rest (up to +20 for 5+ days).
    - Penalty for short rest (-15 for <=2 days).
    - Home-nation bonus (+10 for host countries).
    """
    base = 70.0

    last_match = _get_last_match_date(team_id, all_matches, current_kickoff)
    if last_match:
        days_rest = (current_kickoff - last_match).total_seconds() / 86400.0
        if days_rest >= 5:
            base += 20.0
        elif days_rest >= 4:
            base += 10.0
        elif days_rest >= 3:
            base += 5.0
        elif days_rest <= 2:
            base -= 15.0
    else:
        # No prior match found — assume tournament opener, full rest
        base += 15.0

    # Host nation bonus
    if is_home and team_id.lower() in _HOST_NATIONS_2026:
        base += 10.0

    return round(min(100.0, max(0.0, base)), 2)


def score_evidence_completeness(evidence_index: dict[str, dict]) -> float:
    """Component 5: evidence completeness modifier (-15 to +15).

    Positive when evidence is complete, negative when blocked/missing.
    This acts as a small additive modifier rather than a 0-100 score so
    that missing evidence visibly drags the final number.
    """
    # Key evidence families and their impact weight
    families = {
        "official_fixtures": 2.0,
        "official_rosters": 2.0,
        "fifa_rankings": 2.0,
        "historical_worldcup_results": 1.5,
        "recent_form_results": 2.0,
        "squad_depth_position_balance": 1.5,
        "injury_availability": 2.0,
        "venue_rest_travel": 1.0,
    }
    total_modifier = 0.0
    max_possible = sum(families.values())  # 14.0

    for evidence_id, weight in families.items():
        item = evidence_index.get(evidence_id)
        if not item:
            total_modifier -= weight
            continue
        status = item.get("status", "blocked")
        if status == "complete":
            total_modifier += weight
        elif status == "partial":
            total_modifier += weight * 0.4
        else:
            # blocked or missing
            total_modifier -= weight * 0.5

    # Scale to -15 .. +15 range
    normalised = (total_modifier / max_possible) * 15.0
    return round(max(-15.0, min(15.0, normalised)), 2)


# ---------------------------------------------------------------------------
# Divination overlay (Zhouyi / I Ching)
# ---------------------------------------------------------------------------


def _hexagram_hash(date: str, match_id: str) -> int:
    """Deterministic hash -> hexagram number 1-64."""
    seed = f"{date}|{match_id}|zhouyi"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 64) + 1


def _modifier_from_hexagram(number: int) -> tuple[float, float]:
    """Map hexagram number to (home_modifier, away_modifier) in [-3, +3].

    The modifiers are small and deterministic.  Positive hexagrams
    favour the home side; negative hexagrams favour the away side.
    Some are balanced (near zero for both).
    """
    # Use a simple mapping: hexagram number -> offset on a sine-like curve
    # This ensures a spread of positive/negative values across 1-64.
    import math

    # Home modifier: based on position in the cycle
    angle_home = (number - 1) * (2 * math.pi / 64)
    home_raw = math.sin(angle_home) * _DIVINATION_MODIFIER_MAX
    home_mod = round(home_raw, 1)

    # Away modifier: phase-shifted
    angle_away = ((number - 1) + 16) * (2 * math.pi / 64)
    away_raw = math.sin(angle_away) * _DIVINATION_MODIFIER_MAX
    away_mod = round(away_raw, 1)

    return home_mod, away_mod


def compute_divination_overlay(date: str, match_id: str) -> dict:
    """Compute the entertainment divination overlay for a match."""
    number = _hexagram_hash(date, match_id)
    hex_num, hex_name, hex_interp = _HEXAGRAMS[number - 1]
    home_mod, away_mod = _modifier_from_hexagram(number)
    return {
        "hexagram_number": hex_num,
        "hexagram_name": hex_name,
        "interpretation": hex_interp,
        "home_modifier": home_mod,
        "away_modifier": away_mod,
    }


# ---------------------------------------------------------------------------
# Confidence determination
# ---------------------------------------------------------------------------


def _collect_evidence_gaps(evidence_index: dict[str, dict]) -> list[str]:
    """Return a list of evidence families that are partial or blocked."""
    gaps: list[str] = []
    required_families = [
        "official_fixtures",
        "official_rosters",
        "fifa_rankings",
        "historical_worldcup_results",
        "recent_form_results",
        "squad_depth_position_balance",
        "injury_availability",
        "venue_rest_travel",
    ]
    for eid in required_families:
        item = evidence_index.get(eid)
        if not item:
            gaps.append(f"{eid}_missing")
            continue
        status = item.get("status", "blocked")
        if status == "blocked":
            gaps.append(f"{eid}_blocked")
        elif status == "partial":
            gaps.append(f"{eid}_partial")
    return gaps


def _determine_confidence(data_score: float, evidence_gaps: list[str]) -> tuple[str, str]:
    """Return (confidence_level, confidence_label).

    - high: data_score > 75 AND no blocked evidence
    - medium: data_score 50-75, or partial evidence present
    - low: data_score < 50, or any blocked evidence
    """
    has_blocked = any(g.endswith("_blocked") for g in evidence_gaps)
    has_partial = any(g.endswith("_partial") for g in evidence_gaps)

    if has_blocked:
        level = "low"
    elif data_score > 75 and not has_partial:
        level = "high"
    elif data_score >= 50:
        level = "medium"
    else:
        level = "low"

    labels = {"high": "高信心", "medium": "中等信心", "low": "低信心"}
    return level, labels[level]


def _estimate_scoreline(home_final: float, away_final: float, predicted_outcome: str) -> dict:
    """Translate model scores into a conservative football scoreline."""
    gap = abs(home_final - away_final)
    avg_strength = (home_final + away_final) / 2.0

    if predicted_outcome == "draw":
        goals = 1 if avg_strength >= 42 else 0
        return {"home": goals, "away": goals}

    if gap >= 18:
        winner_goals = 2
        loser_goals = 0 if min(home_final, away_final) < 43 else 1
    elif gap >= 8:
        winner_goals = 2
        loser_goals = 1
    else:
        winner_goals = 1
        loser_goals = 0

    if predicted_outcome == "home_win":
        return {"home": winner_goals, "away": loser_goals}
    return {"home": loser_goals, "away": winner_goals}


def _goal_expectation(score: float, opponent_score: float) -> float:
    edge = score - opponent_score
    value = 0.85 + score / 80.0 + edge / 55.0
    return round(max(0.25, min(3.2, value)), 2)


def _clean_sheet_probability(team_final: float, opponent_final: float, opponent_expected_goals: float) -> float:
    edge = team_final - opponent_final
    probability = 0.34 - opponent_expected_goals * 0.09 + edge * 0.004
    return round(max(0.08, min(0.62, probability)), 2)


def _score_probability(candidate: dict, home_xg: float, away_xg: float, predicted_outcome: str) -> float:
    home_goals = int(candidate["home"])
    away_goals = int(candidate["away"])
    distance = abs(home_goals - home_xg) + abs(away_goals - away_xg)
    probability = max(0.05, 0.38 - distance * 0.075)
    outcome = "draw" if home_goals == away_goals else "home_win" if home_goals > away_goals else "away_win"
    if outcome == predicted_outcome:
        probability += 0.08
    return probability


def _scoreline_reason(candidate: dict, base_score: dict, clean_sheet: dict, predicted_outcome: str) -> str:
    score_text = f"{candidate['home']}-{candidate['away']}"
    base_text = f"{base_score['home']}-{base_score['away']}"
    if candidate == base_score:
        return f"Primary point estimate from the current model edge ({base_text})."
    if candidate["away"] == 0 and predicted_outcome == "home_win":
        return f"Clean-sheet branch; home clean-sheet chance {clean_sheet['home']:.0%} keeps {score_text} live."
    if candidate["home"] == 0 and predicted_outcome == "away_win":
        return f"Clean-sheet branch; away clean-sheet chance {clean_sheet['away']:.0%} keeps {score_text} live."
    if candidate["home"] + candidate["away"] > base_score["home"] + base_score["away"]:
        return "Higher-event branch if set pieces, substitutions, cards, or chase-game state lift the total."
    return "Lower-event branch if tempo stays controlled and the trailing side lacks final-third quality."


def _build_scoreline_distribution(
    *,
    home_final: float,
    away_final: float,
    predicted_outcome: str,
    base_score: dict,
) -> tuple[list[dict], dict, float, float]:
    home_xg = _goal_expectation(home_final, away_final)
    away_xg = _goal_expectation(away_final, home_final)
    clean_sheet = {
        "home": _clean_sheet_probability(home_final, away_final, away_xg),
        "away": _clean_sheet_probability(away_final, home_final, home_xg),
    }
    candidates = {(int(base_score["home"]), int(base_score["away"]))}
    if predicted_outcome == "home_win":
        candidates.update({(2, 0), (2, 1), (1, 0), (3, 1)})
    elif predicted_outcome == "away_win":
        candidates.update({(0, 2), (1, 2), (0, 1), (1, 3)})
    else:
        candidates.update({(0, 0), (1, 1), (2, 2), (1, 0), (0, 1)})

    scored = []
    for home_goals, away_goals in candidates:
        candidate = {"home": home_goals, "away": away_goals}
        scored.append(
            {
                "score": candidate,
                "raw_probability": _score_probability(candidate, home_xg, away_xg, predicted_outcome),
            }
        )
    scored.sort(key=lambda item: item["raw_probability"], reverse=True)
    top = scored[:4]
    total = sum(item["raw_probability"] for item in top) or 1.0
    distribution = []
    for item in top:
        candidate = item["score"]
        distribution.append(
            {
                "score": candidate,
                "probability": round(item["raw_probability"] / total, 3),
                "reason": _scoreline_reason(candidate, base_score, clean_sheet, predicted_outcome),
            }
        )
    return distribution, clean_sheet, home_xg, away_xg


def _split_confidence_fields(
    *,
    result_confidence: str,
    scoreline_distribution: list[dict],
    clean_sheet_probability: dict,
    evidence_gaps: list[str],
) -> dict:
    top_probability = float(scoreline_distribution[0]["probability"]) if scoreline_distribution else 0.0
    clean_gap = abs(float(clean_sheet_probability.get("home", 0)) - float(clean_sheet_probability.get("away", 0)))
    score_confidence = "medium" if top_probability >= 0.35 and not evidence_gaps else "low"
    if result_confidence == "high" and clean_gap >= 0.12 and not evidence_gaps:
        total_goals_confidence = "medium"
    elif result_confidence == "low" or evidence_gaps:
        total_goals_confidence = "low"
    else:
        total_goals_confidence = "medium"
    return {
        "result_confidence": result_confidence,
        "score_confidence": score_confidence,
        "total_goals_confidence": total_goals_confidence,
        "confidence_note": "Result direction confidence is not exact-score confidence.",
    }


def _tianji_score(base_score: float, modifier: float) -> float:
    """Convert Tianji modifier into a 0-100 side score for 60/40 blending."""
    return round(max(0.0, min(100.0, base_score + modifier * 8.0)), 1)


# ---------------------------------------------------------------------------
# Play card builder
# ---------------------------------------------------------------------------


def _build_play_card(
    *,
    match: dict,
    home_name: str,
    away_name: str,
    home_final: float,
    away_final: float,
    predicted_outcome: str,
    predicted_score: dict,
    total_goals: int,
    confidence: str,
    confidence_label: str,
    evidence_gaps: list[str],
    hexagram_name: str,
    data_weight: float,
    divination_weight: float,
) -> dict:
    outcome_labels = {
        "home_win": f"{home_name} 倾向胜出",
        "away_win": f"{away_name} 倾向胜出",
        "draw": "平局拉扯",
    }
    # Context-aware hook
    venue = match.get("venue", "")
    phase = match.get("phase", "group")
    group = match.get("group", "")
    if phase == "group" and group:
        hook = f"{group}组对决，{venue or '赛场待定'}。"
    elif phase != "group":
        hook = f"淘汰赛阶段，{venue or '赛场待定'}。"
    else:
        hook = f"{venue or '赛场待定'}。"

    # Watch points: derive from score gap and context
    watch_points: list[str] = []
    gap = abs(home_final - away_final)
    if gap > 20:
        watch_points.append(f"{home_name if home_final > away_final else away_name}排名优势明显")
        watch_points.append(f"{'弱' if home_final < away_final else '强'}方能否以冲击力弥补差距")
    else:
        watch_points.append("双方实力接近，临场发挥将决定走向")
        watch_points.append("中场控制权和定位球效率是关键")

    if phase == "group":
        watch_points.append("小组赛积分策略可能影响比赛节奏")

    # Risk flags from evidence gaps
    risk_flags: list[str] = []
    for gap_id in evidence_gaps:
        if "injury" in gap_id:
            risk_flags.append("伤停信息不完整")
        elif "recent_form" in gap_id:
            risk_flags.append("近期战绩数据缺失")
        elif "historical" in gap_id:
            risk_flags.append("历史战绩数据不足")
        elif "venue_rest" in gap_id:
            risk_flags.append("休息和旅行因素未充分计算")
    if not risk_flags:
        risk_flags.append("当前证据链完整度较好")

    # Poster angle (English for image generation)
    poster_angle = f"{home_name} vs {away_name}, {venue or 'World Cup stadium'}, vibrant crowd atmosphere, {hexagram_name} aesthetic overlay"

    # Confidence meter
    data_pct = round(data_weight * 100)
    div_pct = round(divination_weight * 100)
    confidence_meter = f"数据 {data_pct}% | 玄学 {div_pct}% | 信心: {confidence_label}"

    score_text = f"{predicted_score['home']}-{predicted_score['away']}"
    if predicted_outcome == "home_win":
        poster_caption = f"AI预测比分 {score_text}，{home_name}主线占优，胜负趋势指向主队。"
    elif predicted_outcome == "away_win":
        poster_caption = f"AI预测比分 {score_text}，{away_name}主线占优，胜负趋势指向客队。"
    else:
        poster_caption = f"AI预测比分 {score_text}，双方拉扯成局，平局剧本需要重点防范。"

    return {
        "share_title": f"{home_name} vs {away_name} | 娱乐预测 {score_text}",
        "match_hook": f"{outcome_labels[predicted_outcome]}，总进球参考 {total_goals} 球。{hook}",
        "poster_caption": poster_caption,
        "watch_points": watch_points,
        "risk_flags": risk_flags,
        "poster_angle": f"{poster_angle}, predicted score {score_text}, total goals {total_goals}",
        "confidence_meter": confidence_meter,
        "gameplay_tags": ["胜平负", "比分", "总进球", "看点"],
    }


def _outcome_label(outcome: str, home_name: str, away_name: str) -> str:
    labels = {
        "home_win": f"{home_name} 倾向不败或取胜",
        "away_win": f"{away_name} 倾向不败或取胜",
        "draw": "平局拉扯",
    }
    return labels.get(outcome, outcome)


def _winner_name(outcome: str | None, home_name: str, away_name: str) -> str:
    if outcome == "home_win":
        return home_name
    if outcome == "away_win":
        return away_name
    return "平局"


def _format_delta(delta: float) -> str:
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}"


def _edge_verdict(delta: float, *, threshold: float = 3.0) -> str:
    if delta > threshold:
        return "home_edge"
    if delta < -threshold:
        return "away_edge"
    return "balanced"


def _team_shape(team_squad: dict | None) -> str:
    if not team_squad:
        return "roster unavailable"
    pos = team_squad.get("position_counts", {}) or {}
    parts = [
        f"GK{pos.get('GK', 0)}",
        f"DF{pos.get('DF', 0)}",
        f"MF{pos.get('MF', 0)}",
        f"FW{pos.get('FW', 0)}",
    ]
    age = team_squad.get("avg_age_years")
    height = team_squad.get("avg_height_cm")
    extras = []
    if age is not None:
        extras.append(f"avg age {float(age):.1f}")
    if height is not None:
        extras.append(f"avg height {float(height):.1f}cm")
    suffix = f"; {', '.join(extras)}" if extras else ""
    return "/".join(parts) + suffix


def _layer(
    *,
    layer_id: str,
    title: str,
    verdict: str,
    confidence: str,
    summary: str,
    key_drivers: list[str] | None = None,
    counter_signals: list[str] | None = None,
    missing_context: list[str] | None = None,
    watch_triggers: list[str] | None = None,
) -> dict:
    return {
        "layer_id": layer_id,
        "title": title,
        "verdict": verdict,
        "confidence": confidence,
        "summary": summary,
        "key_drivers": key_drivers or [],
        "counter_signals": counter_signals or [],
        "missing_context": missing_context or [],
        "watch_triggers": watch_triggers or [],
    }


def _component_drivers(ctx: dict) -> tuple[list[str], list[str]]:
    home = ctx["home_name"]
    away = ctx["away_name"]
    checks = [
        ("FIFA ranking strength", ctx["rs_home"] - ctx["rs_away"], 8.0),
        ("Squad depth and balance", ctx["sd_home"] - ctx["sd_away"], 5.0),
        ("Historical proxy", ctx["hp_home"] - ctx["hp_away"], 8.0),
        ("Rest/travel context", ctx["rt_home"] - ctx["rt_away"], 6.0),
    ]
    drivers: list[str] = []
    counters: list[str] = []
    for label, delta, threshold in checks:
        if abs(delta) >= threshold:
            leader = home if delta > 0 else away
            drivers.append(f"{label}: {leader} edge {_format_delta(abs(delta))}")
        else:
            counters.append(f"{label}: near-even delta {_format_delta(delta)}")
    if ctx["ec_modifier"] < 0:
        counters.append(f"Evidence completeness drags both sides ({ctx['ec_modifier']})")
    elif ctx["ec_modifier"] > 0:
        drivers.append(f"Evidence completeness supports model confidence (+{ctx['ec_modifier']})")
    return drivers, counters


def _venue_adaptation_verdict(venue_adaptation: dict) -> str:
    if not venue_adaptation or venue_adaptation.get("status") == "unavailable":
        return "untracked"
    home = venue_adaptation.get("home", {}) or {}
    away = venue_adaptation.get("away", {}) or {}
    if home.get("adaptation_risk") == "unknown" or away.get("adaptation_risk") == "unknown":
        return "partial_static_context"
    home_score = int(home.get("adaptation_risk_score", 0))
    away_score = int(away.get("adaptation_risk_score", 0))
    if away_score - home_score >= 2:
        return "home_adaptation_edge"
    if home_score - away_score >= 2:
        return "away_adaptation_edge"
    return "balanced_static_context"


def _venue_adaptation_drivers(ctx: dict) -> tuple[list[str], list[str]]:
    venue_adaptation = ctx.get("venue_adaptation_context", {}) or {}
    venue_context = venue_adaptation.get("venue_context") or {}
    home = venue_adaptation.get("home", {}) or {}
    away = venue_adaptation.get("away", {}) or {}
    drivers: list[str] = []
    if venue_context:
        drivers.append(
            "Venue baseline: "
            f"{venue_context.get('city', 'unknown')} "
            f"{venue_context.get('june_temp_c', 'unknown')}C, "
            f"{venue_context.get('altitude_m', 'unknown')}m, "
            f"{venue_context.get('climate_profile', 'unknown')}"
        )
    else:
        drivers.append("Venue baseline is not mapped yet.")
    for label, team_name, item in (("Home", ctx["home_name"], home), ("Away", ctx["away_name"], away)):
        if item.get("status") == "missing_context":
            drivers.append(f"{label} {team_name}: static adaptation context missing.")
            continue
        drivers.append(
            f"{label} {team_name}: travel {item.get('travel_km', 'unknown')}km, "
            f"temperature delta {_signed_delta(item.get('temperature_delta_c'), unit='c')}, "
            f"altitude delta {_signed_delta(item.get('altitude_delta_m'), unit='m')}, "
            f"risk={item.get('adaptation_risk', 'unknown')}."
        )
    limitations = venue_adaptation.get("limitations", []) or []
    return drivers, limitations[:2]


def _build_scenario_analysis(ctx: dict) -> dict:
    home = ctx["home_name"]
    away = ctx["away_name"]
    predicted_outcome = ctx["predicted_outcome"]
    predicted_score = ctx["predicted_score"]
    final_delta = ctx["home_final"] - ctx["away_final"]
    leader = _winner_name(predicted_outcome, home, away)
    trailer = away if leader == home else home if leader == away else "任一方"
    score_text = f"{predicted_score['home']}-{predicted_score['away']}"
    is_close = abs(final_delta) <= 8.0

    base_case = (
        f"Base case: {_outcome_label(predicted_outcome, home, away)}, reference score {score_text}. "
        f"The model edge is {_format_delta(final_delta)} after fundamentals and Tianji overlay."
    )
    if predicted_outcome == "draw":
        upset_case = (
            f"Breakout case: either {home} or {away} can flip the draw if early pressing creates a first-half goal."
        )
    else:
        upset_case = (
            f"Counter case: {trailer} changes the read if lineup news improves, set pieces land, or the favorite is forced into a slow first half."
        )

    draw_case = (
        "Draw case: becomes live if the first 30 minutes stay low-event and both sides protect transition space."
        if not is_close
        else "Draw case: already material because the model gap is narrow; game state and finishing variance matter."
    )

    triggers = [
        "confirmed starting XI differs from roster-depth baseline",
        "late injury or suspension changes the strongest positional unit",
        "market probability moves against the model by more than 8 percentage points",
    ]
    if ctx.get("referee"):
        triggers.append("referee strictness turns physical duels into card/penalty risk")
    if ctx.get("dual_track_alignment") == "divergent":
        triggers.append("market and fundamentals remain divergent close to kickoff")

    return {
        "base_case": base_case,
        "upset_case": upset_case,
        "draw_case": draw_case,
        "watch_triggers": triggers,
    }


def _build_decision_audit(ctx: dict) -> dict:
    home = ctx["home_name"]
    away = ctx["away_name"]
    final_delta = ctx["home_final"] - ctx["away_final"]
    predicted_outcome = ctx["predicted_outcome"]
    evidence_gaps = ctx.get("evidence_gaps", [])
    non_resolved_gaps = [gap for gap in evidence_gaps if not str(gap).endswith("_resolved")]

    why = [
        f"Final model edge {_format_delta(final_delta)} points toward {_outcome_label(predicted_outcome, home, away)}.",
    ]
    if ctx.get("dual_track_alignment") == "aligned":
        why.append("Market expectation and fundamentals point in the same direction.")
    elif ctx.get("dual_track_alignment") == "divergent":
        why.append("Fundamentals and market disagree, so the pick is kept with explicit upset risk.")
    if ctx.get("confidence") == "high":
        why.append("Evidence coverage is strong enough to avoid the usual confidence cap.")

    change_triggers = [
        "new injury/lineup evidence changes the strongest positional edge",
        "fresh odds imply a different market favorite",
        "post-match calibration shows this confidence bucket underperforming",
    ]
    if abs(final_delta) <= 8.0:
        change_triggers.append("small model gap means a single major lineup change can flip the pick")
    if non_resolved_gaps:
        change_triggers.append("blocked or partial evidence must be resolved before raising confidence")

    if ctx.get("confidence") == "low" or len(non_resolved_gaps) >= 3:
        risk_level = "high"
    elif ctx.get("dual_track_alignment") == "divergent" or abs(final_delta) <= 8.0:
        risk_level = "medium"
    else:
        risk_level = "controlled"

    return {
        "risk_level": risk_level,
        "why_this_pick": why,
        "what_would_change_the_pick": change_triggers,
        "thin_evidence_warnings": non_resolved_gaps,
    }


def _build_analysis_layers(ctx: dict) -> list[dict]:
    home = ctx["home_name"]
    away = ctx["away_name"]
    final_delta = ctx["home_final"] - ctx["away_final"]
    evidence_gaps = ctx.get("evidence_gaps", [])
    non_resolved_gaps = [gap for gap in evidence_gaps if not str(gap).endswith("_resolved")]
    local_gaps = ctx.get("local_gaps", [])

    layers: list[dict] = []

    evidence_drivers = []
    if ctx.get("daily_evidence"):
        evidence_drivers.append("matchday evidence file available")
    if ctx.get("odds"):
        evidence_drivers.append("market odds available")
    if ctx.get("referee"):
        evidence_drivers.append("referee profile available")
    if ctx.get("late_news"):
        evidence_drivers.append(f"{len(ctx['late_news'])} late-news items scanned")

    evidence_missing = non_resolved_gaps + local_gaps
    evidence_verdict = "thin_evidence" if evidence_missing else "usable_evidence"
    layers.append(
        _layer(
            layer_id="evidence_integrity",
            title="证据完整度层",
            verdict=evidence_verdict,
            confidence=ctx["confidence"],
            summary=(
                "Evidence is strong enough for a richer read."
                if not evidence_missing
                else "Evidence has gaps; the model keeps uncertainty visible instead of overclaiming."
            ),
            key_drivers=evidence_drivers or ["baseline edition sources loaded"],
            missing_context=evidence_missing,
            watch_triggers=["refresh daily evidence before kickoff", "mark mock sources separately from live sources"],
        )
    )

    fundamentals_drivers, fundamentals_counters = _component_drivers(ctx)
    layers.append(
        _layer(
            layer_id="fundamentals",
            title="基本面强弱层",
            verdict=_edge_verdict(final_delta),
            confidence=ctx["confidence"],
            summary=(
                f"Fundamentals plus capped overlay lean {_outcome_label(ctx['predicted_outcome'], home, away)} "
                f"with final delta {_format_delta(final_delta)}."
            ),
            key_drivers=fundamentals_drivers,
            counter_signals=fundamentals_counters,
            watch_triggers=["ranking and roster updates", "rest-day recalculation after previous matches"],
        )
    )

    home_shape = _team_shape(ctx.get("home_squad"))
    away_shape = _team_shape(ctx.get("away_squad"))
    matchup_drivers = [
        f"{home} shape: {home_shape}",
        f"{away} shape: {away_shape}",
    ]
    if abs(ctx["sd_home"] - ctx["sd_away"]) >= 5:
        squad_leader = home if ctx["sd_home"] > ctx["sd_away"] else away
        matchup_drivers.append(f"{squad_leader} has the cleaner depth/balance score.")
    else:
        matchup_drivers.append("Squad-balance score is close; tactical execution matters more than raw depth.")
    layers.append(
        _layer(
            layer_id="matchup",
            title="阵容对位层",
            verdict=_edge_verdict(ctx["sd_home"] - ctx["sd_away"], threshold=5.0),
            confidence=ctx["confidence"],
            summary="This layer turns roster shape into concrete matchup pressure rather than only a total score.",
            key_drivers=matchup_drivers,
            missing_context=[] if ctx.get("home_squad") and ctx.get("away_squad") else ["official roster/depth data incomplete"],
            watch_triggers=["starting XI", "formation change", "set-piece personnel"],
        )
    )

    live_drivers = [
        f"Rest/travel delta: {_format_delta(ctx['rt_home'] - ctx['rt_away'])}",
        f"News sentiment delta: {_format_delta(ctx['home_news_sentiment'] - ctx['away_news_sentiment'])}",
    ]
    if ctx.get("referee"):
        live_drivers.append(
            f"Referee {ctx['referee'].get('name', 'Unknown')} strictness={ctx['referee'].get('strictness', 'medium')}, yellow-card line {ctx['yellow_cards_pred']}"
        )
    layers.append(
        _layer(
            layer_id="live_context",
            title="临场变量层",
            verdict=_edge_verdict(
                (ctx["rt_home"] - ctx["rt_away"]) + (ctx["home_news_sentiment"] - ctx["away_news_sentiment"]),
                threshold=4.0,
            ),
            confidence=ctx["confidence"],
            summary="Late news, rest, travel and referee profile are handled as separate pressure rather than hidden inside one score.",
            key_drivers=live_drivers,
            missing_context=[] if ctx.get("referee") else ["referee profile missing"],
            watch_triggers=["team news within 24h", "referee assignment correction", "late travel/weather disruption"],
        )
    )

    venue_drivers, venue_limitations = _venue_adaptation_drivers(ctx)
    venue_adaptation = ctx.get("venue_adaptation_context", {}) or {}
    layers.append(
        _layer(
            layer_id="venue_adaptation",
            title="Venue Adaptation",
            verdict=_venue_adaptation_verdict(venue_adaptation),
            confidence="medium" if venue_adaptation.get("status") == "estimated_static_context" else "low",
            summary=(
                "Static venue, travel, temperature and altitude baselines are exposed as evidence. "
                "This is not live kickoff weather and does not override verified lineup or injury news."
            ),
            key_drivers=venue_drivers,
            counter_signals=venue_limitations,
            missing_context=venue_adaptation.get("missing_context", []),
            watch_triggers=[
                "replace static baseline with live venue weather",
                "confirm team camp/base location",
                "add humidity, wind, pitch and player acclimatization notes",
            ],
        )
    )

    if ctx.get("odds"):
        implied = ctx.get("implied_probs") or {}
        market_drivers = [
            f"Market favorite: {_winner_name(ctx.get('market_outcome'), home, away)}",
            f"Implied probabilities home/draw/away: {implied.get('home')}/{implied.get('draw')}/{implied.get('away')}",
        ]
        market_summary = ctx.get("divergence_analysis") or "Market signal is available but no narrative was generated."
        market_missing: list[str] = []
    else:
        market_drivers = ["No market odds attached to this match evidence."]
        market_summary = "Market track is unavailable, so the dual-track read cannot confirm or challenge fundamentals."
        market_missing = ["odds_missing"]
    layers.append(
        _layer(
            layer_id="market_track",
            title="市场背离层",
            verdict=ctx.get("dual_track_alignment") or "untracked",
            confidence=ctx["confidence"],
            summary=market_summary,
            key_drivers=market_drivers,
            missing_context=market_missing,
            watch_triggers=["odds refresh", "large implied-probability movement", "market/fundamental divergence near kickoff"],
        )
    )

    scenario = ctx["scenario_analysis"]
    layers.append(
        _layer(
            layer_id="scenario_tree",
            title="比赛剧本层",
            verdict=ctx["predicted_outcome"],
            confidence=ctx["confidence"],
            summary=scenario["base_case"],
            key_drivers=[scenario["base_case"], scenario["upset_case"], scenario["draw_case"]],
            watch_triggers=scenario["watch_triggers"],
        )
    )

    scoreline_distribution = ctx.get("scoreline_distribution", []) or []
    clean_sheet = ctx.get("clean_sheet_probability", {}) or {}
    top_scores = []
    for item in scoreline_distribution[:3]:
        score = item.get("score", {}) or {}
        top_scores.append(
            f"{score.get('home', '-')}-{score.get('away', '-')} ({float(item.get('probability', 0)):.0%})"
        )
    layers.append(
        _layer(
            layer_id="score_distribution",
            title="Scoreline Distribution",
            verdict=ctx["confidence_split"]["score_confidence"],
            confidence=ctx["confidence_split"]["score_confidence"],
            summary=(
                "Exact score is modeled as a distribution, not a single certainty. "
                f"Top branches: {', '.join(top_scores) if top_scores else 'unavailable'}."
            ),
            key_drivers=[
                f"Expected goals proxy: {ctx.get('home_expected_goals')}-{ctx.get('away_expected_goals')}",
                f"Clean-sheet probability home/away: {float(clean_sheet.get('home', 0)):.0%}/{float(clean_sheet.get('away', 0)):.0%}",
            ],
            counter_signals=[
                "Cards, penalties, set pieces, and chase-game states can move the total-goals branch quickly."
            ],
            missing_context=[gap for gap in ctx.get("evidence_gaps", []) if not str(gap).endswith("_resolved")],
            watch_triggers=["confirmed lineup", "set-piece mismatch", "red card or early goal", "second-half substitution state"],
        )
    )

    audit = ctx["decision_audit"]
    layers.append(
        _layer(
            layer_id="adversarial_review",
            title="反方审稿层",
            verdict=audit["risk_level"],
            confidence=ctx["confidence"],
            summary="This layer records why the pick could be wrong before publishing the final entertainment call.",
            key_drivers=audit["why_this_pick"],
            counter_signals=audit["what_would_change_the_pick"],
            missing_context=audit["thin_evidence_warnings"],
            watch_triggers=audit["what_would_change_the_pick"],
        )
    )

    return layers


# ---------------------------------------------------------------------------
# Main prediction pipeline
# ---------------------------------------------------------------------------


def predict_match(
    *,
    match: dict,
    edition: str,
    date: str,
    all_matches: list[dict],
    ranking_index: dict[str, dict],
    squad_index: dict[str, dict],
    evidence_index: dict[str, dict],
    global_summary: dict | None,
    daily_evidence: dict | None = None,
) -> dict:
    """Compute the full prediction record for a single match."""
    home_team = match.get("home_team", {})
    away_team = match.get("away_team", {})
    home_id = str(home_team.get("team_id", ""))
    away_id = str(away_team.get("team_id", ""))
    home_name = str(home_team.get("name") or home_id)
    away_name = str(away_team.get("name") or away_id)

    # --- Data look-ups ---
    home_ranking = _lookup_team(home_id, ranking_index)
    away_ranking = _lookup_team(away_id, ranking_index)
    home_squad = _lookup_team(home_id, squad_index)
    away_squad = _lookup_team(away_id, squad_index)

    kickoff = parse_datetime(str(match.get("kickoff_at", "")))
    kickoff_dt = kickoff or datetime.now(timezone.utc)

    # --- Component scores (each 0-100 except evidence which is -15..+15) ---
    rs_home = score_ranking_strength(home_ranking)
    rs_away = score_ranking_strength(away_ranking)

    sd_home = score_squad_depth(home_squad, global_summary)
    sd_away = score_squad_depth(away_squad, global_summary)

    hp_home = score_historical_proxy(home_ranking)
    hp_away = score_historical_proxy(away_ranking)

    rt_home = score_rest_travel(
        team_id=home_id,
        is_home=True,
        current_kickoff=kickoff_dt,
        all_matches=all_matches,
        edition=edition,
    )
    rt_away = score_rest_travel(
        team_id=away_id,
        is_home=False,
        current_kickoff=kickoff_dt,
        all_matches=all_matches,
        edition=edition,
    )
    venue_adaptation_context = _build_venue_adaptation_context(match, home_id, away_id)

    ec_modifier = score_evidence_completeness(evidence_index)

    # --- Daily Evidence Parsing (Referee, News, Odds) ---
    referee = None
    odds = None
    late_news = []

    if daily_evidence:
        late_news = daily_evidence.get("late_news", [])
        for m in daily_evidence.get("matches", []):
            if m.get("match_id") == match.get("match_id"):
                referee = m.get("referee")
                odds = m.get("odds")
                break

    # 1. Referee Rigor Modifier
    referee_home_mod = 0.0
    referee_away_mod = 0.0
    yellow_cards_pred = 3.5
    red_cards_pred = 0.1
    penalties_pred = 0.2

    if referee:
        strictness = referee.get("strictness", "medium")
        if strictness == "high":
            # Protects technical/stronger teams, penalizes physical defensive teams
            if rs_home > rs_away:
                referee_home_mod += 2.0
                referee_away_mod -= 1.0
            else:
                referee_away_mod += 2.0
                referee_home_mod -= 1.0
            if sd_home > sd_away:
                referee_home_mod += 1.0
            elif sd_away > sd_home:
                referee_away_mod += 1.0

            yellow_cards_pred = referee.get("yellow_cards_per_match") or 5.5
            red_cards_pred = referee.get("red_cards_per_match") or 0.25
            penalties_pred = referee.get("penalties_per_match") or 0.35
        elif strictness == "low":
            # Favors physical underdog defense
            if rs_home < rs_away:
                referee_home_mod += 2.0
            elif rs_away < rs_home:
                referee_away_mod += 2.0

            yellow_cards_pred = referee.get("yellow_cards_per_match") or 2.0
            red_cards_pred = referee.get("red_cards_per_match") or 0.05
            penalties_pred = referee.get("penalties_per_match") or 0.10
        else:
            yellow_cards_pred = referee.get("yellow_cards_per_match") or 3.5
            red_cards_pred = referee.get("red_cards_per_match") or 0.10
            penalties_pred = referee.get("penalties_per_match") or 0.20

    # 2. News Sentiment Modifier
    home_news_sentiment = 0.0
    away_news_sentiment = 0.0
    for news in late_news:
        news_team = news.get("team_code", "")
        if news_team:
            sentiment = news.get("sentiment", "neutral")
            impact = news.get("impact", "medium")
            factor = 2.0 if impact == "high" else 1.0 if impact == "medium" else 0.5

            if sentiment == "positive":
                if news_team == home_id:
                    home_news_sentiment += factor
                elif news_team == away_id:
                    away_news_sentiment += factor
            elif sentiment == "negative":
                if news_team == home_id:
                    home_news_sentiment -= factor
                elif news_team == away_id:
                    away_news_sentiment -= factor

    # Cap news sentiment modifiers to [-3.0, 3.0]
    home_news_sentiment = max(-3.0, min(3.0, home_news_sentiment))
    away_news_sentiment = max(-3.0, min(3.0, away_news_sentiment))

    # --- Weighted data_score (per team, 0-100 before cap) ---
    raw_home = (
        rs_home * W_RANKING_STRENGTH
        + sd_home * W_SQUAD_DEPTH
        + hp_home * W_HISTORICAL_PROXY
        + rt_home * W_REST_TRAVEL
        + ec_modifier * W_EVIDENCE_COMPLETENESS
        + referee_home_mod
        + home_news_sentiment
    )
    raw_away = (
        rs_away * W_RANKING_STRENGTH
        + sd_away * W_SQUAD_DEPTH
        + hp_away * W_HISTORICAL_PROXY
        + rt_away * W_REST_TRAVEL
        + ec_modifier * W_EVIDENCE_COMPLETENESS
        + referee_away_mod
        + away_news_sentiment
    )

    data_home = round(min(_DATA_SCORE_CAP, max(0.0, raw_home)), 1)
    data_away = round(min(_DATA_SCORE_CAP, max(0.0, raw_away)), 1)

    # --- Divination overlay (Tianji Purple Star Astrology) ---
    divination = compute_tianji_overlay(
        match.get("kickoff_at", ""),
        match.get("match_id", ""),
        venue=str(match.get("venue", "")),
    )
    # Compatibility mapping for original keys
    divination["hexagram_name"] = divination["shichen"]
    divination["hexagram_number"] = 0
    divination["hexagram"] = divination["shichen"]
    divination["weight"] = DIVINATION_WEIGHT

    # --- Final scores: 60% data model + 40% Tianji entertainment overlay ---
    tianji_home_score = _tianji_score(data_home, float(divination["home_modifier"]))
    tianji_away_score = _tianji_score(data_away, float(divination["away_modifier"]))
    home_final = round((data_home * DATA_WEIGHT) + (tianji_home_score * DIVINATION_WEIGHT), 1)
    away_final = round((data_away * DATA_WEIGHT) + (tianji_away_score * DIVINATION_WEIGHT), 1)

    # --- Predicted outcome (Fundamentals Track) ---
    gap = home_final - away_final
    if abs(gap) <= 3.0:
        predicted_outcome = "draw"
    elif gap > 0:
        predicted_outcome = "home_win"
    else:
        predicted_outcome = "away_win"
    predicted_score = _estimate_scoreline(home_final, away_final, predicted_outcome)
    total_goals = int(predicted_score["home"]) + int(predicted_score["away"])
    goals_line_2_5 = "over" if total_goals >= 3 else "under"
    scoreline_distribution, clean_sheet_probability, home_expected_goals, away_expected_goals = _build_scoreline_distribution(
        home_final=home_final,
        away_final=away_final,
        predicted_outcome=predicted_outcome,
        base_score=predicted_score,
    )

    # --- Odds & Market Track & Divergence Analysis ---
    implied_probs = None
    market_outcome = None
    dual_track_alignment = "untracked"
    divergence_analysis = ""

    if odds:
        o_home = float(odds.get("home_win", 1.0))
        o_draw = float(odds.get("draw", 1.0))
        o_away = float(odds.get("away_win", 1.0))

        raw_p_home = 1.0 / o_home
        raw_p_draw = 1.0 / o_draw
        raw_p_away = 1.0 / o_away
        sum_p = raw_p_home + raw_p_draw + raw_p_away

        implied_probs = {
            "home": round(raw_p_home / sum_p, 3),
            "draw": round(raw_p_draw / sum_p, 3),
            "away": round(raw_p_away / sum_p, 3)
        }

        max_p = max(implied_probs["home"], implied_probs["draw"], implied_probs["away"])
        if max_p == implied_probs["home"]:
            market_outcome = "home_win"
        elif max_p == implied_probs["away"]:
            market_outcome = "away_win"
        else:
            market_outcome = "draw"

        if predicted_outcome == market_outcome:
            dual_track_alignment = "aligned"
            winner_lbl = home_name if predicted_outcome == "home_win" else away_name if predicted_outcome == "away_win" else "双方平局"
            divergence_analysis = f"【双轨共振】数据基本面与市场赔率一致倾向【{winner_lbl}】。章鱼哥触手坚定，玄学气运与盘口期望形成合力。"
        else:
            dual_track_alignment = "divergent"
            fund_winner_lbl = home_name if predicted_outcome == "home_win" else away_name if predicted_outcome == "away_win" else "平局拉扯"
            mkt_winner_lbl = home_name if market_outcome == "home_win" else away_name if market_outcome == "away_win" else "平局拉扯"
            divergence_analysis = f"【双轨背离】硬实力基本面支持【{fund_winner_lbl}】，但市场赔率反向看好【{mkt_winner_lbl}】。章鱼哥在箱子前摇摆游离，谨防诱盘或爆冷！"

    # --- Confidence Override ---
    local_gaps = []
    if not home_squad or not away_squad:
        local_gaps.append("rosters_missing")
    if not referee:
        local_gaps.append("referee_missing")
    if not odds:
        local_gaps.append("odds_missing")

    avg_data = (data_home + data_away) / 2.0
    if daily_evidence and not local_gaps:
        # All local evidence is complete! Bypassing global gaps caps
        if avg_data > 65.0:
            confidence = "high"
            confidence_label = "高信心"
        elif avg_data >= 50.0:
            confidence = "medium"
            confidence_label = "中等信心"
        else:
            confidence = "low"
            confidence_label = "低信心"
        evidence_gaps = [g + "_resolved" for g in local_gaps]
    else:
        # Fallback to the original global gaps logic
        evidence_gaps = _collect_evidence_gaps(evidence_index)
        confidence, confidence_label = _determine_confidence(avg_data, evidence_gaps)
    confidence_split = _split_confidence_fields(
        result_confidence=confidence,
        scoreline_distribution=scoreline_distribution,
        clean_sheet_probability=clean_sheet_probability,
        evidence_gaps=evidence_gaps,
    )

    analysis_context = {
        "home_name": home_name,
        "away_name": away_name,
        "home_squad": home_squad,
        "away_squad": away_squad,
        "rs_home": rs_home,
        "rs_away": rs_away,
        "sd_home": sd_home,
        "sd_away": sd_away,
        "hp_home": hp_home,
        "hp_away": hp_away,
        "rt_home": rt_home,
        "rt_away": rt_away,
        "ec_modifier": ec_modifier,
        "home_final": home_final,
        "away_final": away_final,
        "predicted_outcome": predicted_outcome,
        "predicted_score": predicted_score,
        "scoreline_distribution": scoreline_distribution,
        "clean_sheet_probability": clean_sheet_probability,
        "home_expected_goals": home_expected_goals,
        "away_expected_goals": away_expected_goals,
        "venue_adaptation_context": venue_adaptation_context,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "confidence_split": confidence_split,
        "evidence_gaps": evidence_gaps,
        "local_gaps": local_gaps,
        "daily_evidence": daily_evidence,
        "late_news": late_news,
        "referee": referee,
        "yellow_cards_pred": yellow_cards_pred,
        "home_news_sentiment": home_news_sentiment,
        "away_news_sentiment": away_news_sentiment,
        "odds": odds,
        "implied_probs": implied_probs,
        "market_outcome": market_outcome,
        "dual_track_alignment": dual_track_alignment,
        "divergence_analysis": divergence_analysis,
    }
    scenario_analysis = _build_scenario_analysis(analysis_context)
    analysis_context["scenario_analysis"] = scenario_analysis
    decision_audit = _build_decision_audit(analysis_context)
    analysis_context["decision_audit"] = decision_audit
    analysis_layers = _build_analysis_layers(analysis_context)

    # --- Ranking info for output ---
    home_rank_info = {
        "team_id": home_id,
        "name": home_name,
        "ranking": home_ranking.get("rank", 0) if home_ranking else 0,
        "points": home_ranking.get("points", 0.0) if home_ranking else 0.0,
    }
    away_rank_info = {
        "team_id": away_id,
        "name": away_name,
        "ranking": away_ranking.get("rank", 0) if away_ranking else 0,
        "points": away_ranking.get("points", 0.0) if away_ranking else 0.0,
    }

    # --- Play card ---
    play_card = _build_play_card(
        match=match,
        home_name=home_name,
        away_name=away_name,
        home_final=home_final,
        away_final=away_final,
        predicted_outcome=predicted_outcome,
        predicted_score=predicted_score,
        total_goals=total_goals,
        confidence=confidence,
        confidence_label=confidence_label,
        evidence_gaps=evidence_gaps,
        hexagram_name=divination["shichen"],
        data_weight=DATA_WEIGHT,
        divination_weight=DIVINATION_WEIGHT,
    )

    # Enrich play card with agent reasoning
    if divergence_analysis:
        play_card["watch_points"].insert(0, divergence_analysis)
        play_card["share_title"] = f"章鱼哥神算 | " + play_card["share_title"]

    if decision_audit.get("why_this_pick"):
        play_card["watch_points"].insert(0, "多层分析主线：" + decision_audit["why_this_pick"][0])

    if referee:
        play_card["risk_flags"].append(f"裁判执法：{referee['name']} (尺度：{referee['strictness'].upper()})，场均黄牌预估：{yellow_cards_pred}")

    if divination.get("has_physical_conflict"):
        play_card["risk_flags"].append("天纪警示：星盘羊陀照会，物理对抗升级，注意红黄牌及伤病风险。")

    return {
        "match_id": match.get("match_id", ""),
        "kickoff_at": match.get("kickoff_at", ""),
        "venue": match.get("venue", ""),
        "group": match.get("group", ""),
        "phase": match.get("phase", "group"),
        "home_team": home_rank_info,
        "away_team": away_rank_info,
        "data_score": {
            "home": data_home,
            "away": data_away,
            "components": {
                "ranking_strength": {
                    "home": rs_home,
                    "away": rs_away,
                    "weight": W_RANKING_STRENGTH,
                },
                "squad_depth": {
                    "home": sd_home,
                    "away": sd_away,
                    "weight": W_SQUAD_DEPTH,
                },
                "historical_proxy": {
                    "home": hp_home,
                    "away": hp_away,
                    "weight": W_HISTORICAL_PROXY,
                },
                "rest_travel": {
                    "home": rt_home,
                    "away": rt_away,
                    "weight": W_REST_TRAVEL,
                },
                "evidence_completeness": {
                    "home": ec_modifier,
                    "away": ec_modifier,
                    "weight": W_EVIDENCE_COMPLETENESS,
                },
            },
        },
        "venue_adaptation_context": venue_adaptation_context,
        "divination_overlay": divination,
        "prediction": {
            "home_final": home_final,
            "away_final": away_final,
            "result": predicted_outcome,
            "predicted_outcome": predicted_outcome,
            "home_final": home_final,
            "away_final": away_final,
            "result": predicted_outcome,
            "predicted_outcome": predicted_outcome,
            "score": predicted_score,
            "total_goals": total_goals,
            "goals_line_2_5": goals_line_2_5,
            "confidence": confidence,
            "confidence_label": confidence_label,
            "result_confidence": confidence_split["result_confidence"],
            "score_confidence": confidence_split["score_confidence"],
            "total_goals_confidence": confidence_split["total_goals_confidence"],
            "confidence_note": confidence_split["confidence_note"],
            "scoreline_distribution": scoreline_distribution,
            "clean_sheet_probability": clean_sheet_probability,
            "expected_goals_proxy": {
                "home": home_expected_goals,
                "away": away_expected_goals,
            },
            "venue_adaptation_context": venue_adaptation_context,
            "evidence_gaps": evidence_gaps,
        },
        "referee_analysis": {
            "name": referee["name"] if referee else "Unknown",
            "strictness": referee["strictness"] if referee else "medium",
            "predicted_yellow_cards": yellow_cards_pred,
            "predicted_red_cards": red_cards_pred,
            "predicted_penalties": penalties_pred
        } if referee else None,
        "market_odds": {
            "odds": odds,
            "implied_probabilities": implied_probs,
            "market_outcome": market_outcome
        } if odds else None,
        "dual_track": {
            "alignment": dual_track_alignment,
            "divergence_analysis": divergence_analysis
        } if odds else None,
        "analysis_layers": analysis_layers,
        "scenario_analysis": scenario_analysis,
        "decision_audit": decision_audit,
        "analysis_summary": {
            "layer_count": len(analysis_layers),
            "risk_level": decision_audit.get("risk_level"),
            "primary_edge": _edge_verdict(home_final - away_final),
            "storage_note": "JSON report remains the audit artifact; SQLite is an optional query/index layer.",
        },
        "play_card": play_card,
        "disclaimer": DISCLAIMER,
    }


def run_scoring_model(
    *,
    root: Path,
    edition: str,
    date: str | None = None,
    match_id: str | None = None,
    teams: list[str] | None = None,
    now: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Run the prediction scoring model for all matches on *date*."""
    load_hyperparameters(root, edition)
    generated_at = iso_now(now)
    now_dt = now_datetime(now)
    ed_root = edition_data_root(root, edition)

    # --- Load data sources ---
    ledger = load_match_ledger(root, edition)
    rankings_data = load_json(ed_root / "rankings" / "fifa-men-ranking.json", {"rankings": []})
    squad_data = load_json(ed_root / "squad-depth-features.json", {"teams": [], "global_summary": {}})
    evidence_plan = load_json(ed_root / "prediction-evidence-plan.json", {"items": []})

    ranking_index = _build_ranking_index(rankings_data)
    squad_index = _build_squad_index(squad_data)
    evidence_index = _build_evidence_index(evidence_plan)
    global_summary = squad_data.get("global_summary")
    all_matches = ledger.get("matches", [])

    # --- Find matches for this date that haven't kicked off ---
    predictions: list[dict] = []
    skipped_started = 0
    skipped_no_kickoff = 0

    for match in all_matches:
        if match_id and match.get("match_id") != match_id:
            continue
        if teams and not _match_teams(match, teams):
            continue
        if date and not match_on_date(match, date):
            continue
        if match_started(match, now_dt):
            skipped_started += 1
            continue
        kickoff = parse_datetime(str(match.get("kickoff_at", "")))
        if not kickoff:
            skipped_no_kickoff += 1
            continue

        target_date = date or (kickoff.date().isoformat() if kickoff else None)
        daily_evidence = {}
        if target_date:
            evidence_path = ed_root / "daily-evidence" / f"{target_date}.json"
            daily_evidence = load_json(evidence_path, {})

        prediction = predict_match(
            match=match,
            edition=edition,
            date=target_date or "undated",
            all_matches=all_matches,
            ranking_index=ranking_index,
            squad_index=squad_index,
            evidence_index=evidence_index,
            global_summary=global_summary,
            daily_evidence=daily_evidence,
        )
        predictions.append(prediction)

    # --- Build report ---
    report_date = date
    if not report_date and predictions:
        first_kickoff = parse_datetime(str(predictions[0].get("kickoff_at", "")))
        report_date = first_kickoff.date().isoformat() if first_kickoff else "undated"
    report_date = report_date or "undated"

    report = {
        "version": 1,
        "edition": edition,
        "date": report_date,
        "generated_at": generated_at,
        "mode": "worldcup-prediction-scoring-model",
        "filters": {
            "match_id": match_id or "",
            "teams": teams or [],
        },
        "model_weights": {
            "data_model": DATA_WEIGHT,
            "divination_overlay": DIVINATION_WEIGHT,
            "component_weights": {
                "ranking_strength": W_RANKING_STRENGTH,
                "squad_depth": W_SQUAD_DEPTH,
                "historical_proxy": W_HISTORICAL_PROXY,
                "rest_travel": W_REST_TRAVEL,
                "evidence_completeness": W_EVIDENCE_COMPLETENESS,
            },
        },
        "status": "dry_run" if dry_run else "created",
        "summary": {
            "predictions_created": len(predictions),
            "matches_skipped_started": skipped_started,
            "matches_skipped_missing_kickoff": skipped_no_kickoff,
        },
        "predictions": predictions,
        "disclaimer": DISCLAIMER,
        "safety_invariants": [
            "data_model_weight_is_0_60",
            "tianji_overlay_weight_is_0_40",
            "tianji_calculated_from_venue_local_time_when_known",
            "no_betting_language_in_output",
            "missing_evidence_downgrades_confidence",
            "disclaimer_included_in_every_report",
        ],
    }

    # --- Write report (unless dry_run) ---
    if not dry_run and predictions:
        suffix = f"-{match_id}" if match_id else ""
        if teams and not match_id:
            suffix = "-" + "-vs-".join(_normalise_team_query(team) for team in teams)
        out_path = ed_root / "reports" / f"{report_date}{suffix}-prediction-report.json"
        write_json(out_path, report)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    predict = sub.add_parser("predict", help="Run the prediction scoring model")
    predict.add_argument("--edition", required=True, help="Edition identifier (e.g. 2026)")
    predict.add_argument("--root", default=".", help="Project root directory")
    predict.add_argument("--date", help="Target date in YYYY-MM-DD format")
    predict.add_argument("--match-id", help="Predict one match by stable match_id")
    predict.add_argument("--teams", help='Predict one match by team names or IDs, e.g. "Mexico,South Africa"')
    predict.add_argument("--now", default=None, help="Override current time (ISO-8601)")
    predict.add_argument(
        "--dry-run",
        action="store_true",
        help="Print predictions without writing files",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "predict":
        result = run_scoring_model(
            root=Path(args.root).resolve(),
            edition=args.edition,
            date=args.date,
            match_id=args.match_id,
            teams=[item.strip() for item in args.teams.split(",")] if args.teams else None,
            now=args.now,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
