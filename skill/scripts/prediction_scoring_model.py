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
    canonical_matches,
    edition_data_root,
    raw_edition_root,
    iso_now,
    load_edition_data_json,
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

# Scoreline tuning defaults. These are intentionally conservative and can be
# nudged by the post-match tuning loop through model-hyperparameters.json.
SCORELINE_PAIRED_SCORE_BIAS = 0.84
SCORELINE_MODE_COLLAPSE_PENALTY = 0.88
SCORELINE_CLEAN_SHEET_BIAS = 1.24
SCORELINE_DRAW_NIL_BIAS = 1.10
SCORELINE_LOSER_XG_SUPPRESSION = {
    "coinflip": 0.96,
    "slight": 0.86,
    "clear": 0.74,
    "strong": 0.58,
}


def _reset_scoreline_tuning_defaults() -> None:
    """Reset scoreline tuning globals before loading edition overrides."""
    global SCORELINE_PAIRED_SCORE_BIAS, SCORELINE_MODE_COLLAPSE_PENALTY
    global SCORELINE_CLEAN_SHEET_BIAS, SCORELINE_DRAW_NIL_BIAS
    global SCORELINE_LOSER_XG_SUPPRESSION

    SCORELINE_PAIRED_SCORE_BIAS = 0.84
    SCORELINE_MODE_COLLAPSE_PENALTY = 0.88
    SCORELINE_CLEAN_SHEET_BIAS = 1.24
    SCORELINE_DRAW_NIL_BIAS = 1.10
    SCORELINE_LOSER_XG_SUPPRESSION = {
        "coinflip": 0.96,
        "slight": 0.86,
        "clear": 0.74,
        "strong": 0.58,
    }


def load_hyperparameters(root: Path, edition: str) -> None:
    """Load hyperparameters from JSON and update global weight values."""
    global W_RANKING_STRENGTH, W_SQUAD_DEPTH, W_HISTORICAL_PROXY, W_REST_TRAVEL, W_EVIDENCE_COMPLETENESS
    global DATA_WEIGHT, DIVINATION_WEIGHT
    global SCORELINE_PAIRED_SCORE_BIAS, SCORELINE_MODE_COLLAPSE_PENALTY
    global SCORELINE_CLEAN_SHEET_BIAS, SCORELINE_DRAW_NIL_BIAS
    global SCORELINE_LOSER_XG_SUPPRESSION
    from worldcup_core import edition_data_root, load_json
    import worldcup_core

    _reset_scoreline_tuning_defaults()

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
            scoreline = data.get("scoreline_tuning", {})
            if "paired_score_bias" in scoreline:
                SCORELINE_PAIRED_SCORE_BIAS = float(scoreline["paired_score_bias"])
            if "mode_collapse_penalty" in scoreline:
                SCORELINE_MODE_COLLAPSE_PENALTY = float(scoreline["mode_collapse_penalty"])
            if "clean_sheet_bias" in scoreline:
                SCORELINE_CLEAN_SHEET_BIAS = float(scoreline["clean_sheet_bias"])
            if "draw_nil_bias" in scoreline:
                SCORELINE_DRAW_NIL_BIAS = float(scoreline["draw_nil_bias"])
            loser_xg = scoreline.get("loser_xg_suppression", {})
            if isinstance(loser_xg, dict):
                for tier in ("coinflip", "slight", "clear", "strong"):
                    if tier in loser_xg:
                        SCORELINE_LOSER_XG_SUPPRESSION[tier] = float(loser_xg[tier])
        except Exception as e:
            print(f"Warning: Failed to load hyperparameters from {path}: {e}", file=sys.stderr)

# Ranking points range for normalisation (approximate FIFA men's range)
_RANKING_POINTS_MIN = 1200.0
_RANKING_POINTS_MAX = 1900.0

# Maximum data_score before Tianji overlay
_DATA_SCORE_CAP = 100.0

# Stage-dependent divination weights (阶段自适应天纪权重)
# Group stage: more upsets → divination leads (65% div, 35% data)
# Knockout R32/R16: moderate → balanced (55%/45% div)
# QF+: data reliability increases → data leads (30% div)
_STAGE_WEIGHT_TABLE: dict[str, tuple[float, float]] = {
    # Stage prefix → (data_weight, divination_weight)
    "G":   (0.35, 0.65),   # Group stage: 天纪主导
    "R32": (0.45, 0.55),   # Round of 32: 天纪略多
    "R16": (0.55, 0.45),   # Round of 16: 数据略多
    "QF":  (0.70, 0.30),   # Quarter-final: 数据主导
    "SF":  (0.75, 0.25),   # Semi-final
    "F":   (0.80, 0.20),   # Final
    "TP":  (0.80, 0.20),   # Third place
}


def _stage_weights(match_id: str) -> tuple[float, float]:
    """Return (data_weight, divination_weight) based on match stage.

    小组赛天纪 65%，淘汰赛 45%，八强 30%。
    """
    for prefix, weights in _STAGE_WEIGHT_TABLE.items():
        if f"-{prefix}" in match_id:
            return weights
    return (0.60, 0.40)  # Default fallback


_KNOCKOUT_PHASES = {
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "third_place",
    "final",
}


def _is_knockout_phase(phase: str) -> bool:
    return str(phase or "").strip().lower() in _KNOCKOUT_PHASES


def _score_copy(score: dict | None) -> dict[str, int | None]:
    score = score or {}
    return {
        "home": score.get("home"),
        "away": score.get("away"),
    }


def _aggregate_score(base_score: dict | None, delta_score: dict | None) -> dict[str, int | None]:
    base = _score_copy(base_score)
    delta = _score_copy(delta_score)
    if base["home"] is None or base["away"] is None:
        return {"home": None, "away": None}
    return {
        "home": int(base["home"]) + int(delta["home"] or 0),
        "away": int(base["away"]) + int(delta["away"] or 0),
    }


def _winner_side_from_result(result: str) -> str:
    if result == "home_win":
        return "home"
    if result == "away_win":
        return "away"
    return ""


def _winner_name_from_side(side: str, home_name: str, away_name: str) -> str:
    if side == "home":
        return home_name
    if side == "away":
        return away_name
    return ""


def _advance_result_from_side(side: str) -> str:
    if side == "home":
        return "home_advance"
    if side == "away":
        return "away_advance"
    return ""


def _build_knockout_prediction(
    *,
    phase: str,
    predicted_outcome: str,
    predicted_score: dict,
    home_name: str,
    away_name: str,
    home_final: float,
    away_final: float,
    edge_tier: str,
    game_script: str,
    confidence: str,
) -> dict | None:
    if not _is_knockout_phase(phase):
        return None

    regular_time_score = _score_copy(predicted_score)
    regular_time_result = predicted_outcome
    favorite_side = "home" if home_final >= away_final else "away"

    extra_time_played = regular_time_result == "draw"
    extra_time_period_score = {"home": None, "away": None}
    extra_time_score = {"home": None, "away": None}
    extra_time_result = ""

    penalties_played = False
    penalties_score = {"home": None, "away": None}
    penalties_winner = ""

    advance_side = _winner_side_from_result(regular_time_result)

    if extra_time_played:
        use_penalties = (
            edge_tier in {"coinflip", "slight"}
            and abs(home_final - away_final) < 7.0
            and game_script != "open-game"
        )
        advance_side = favorite_side
        if use_penalties:
            penalties_played = True
            extra_time_period_score = {"home": 0, "away": 0}
            extra_time_score = _score_copy(regular_time_score)
            extra_time_result = "draw"
            if advance_side == "home":
                penalties_score = {"home": 5, "away": 4 if confidence != "high" else 3}
            else:
                penalties_score = {"home": 4 if confidence != "high" else 3, "away": 5}
            penalties_winner = advance_side
        else:
            if advance_side == "home":
                extra_time_period_score = {"home": 1, "away": 0}
                extra_time_result = "home_win"
            else:
                extra_time_period_score = {"home": 0, "away": 1}
                extra_time_result = "away_win"
            extra_time_score = _aggregate_score(regular_time_score, extra_time_period_score)

    advance_name = _winner_name_from_side(advance_side, home_name, away_name)
    return {
        "is_knockout": True,
        "phase": phase,
        "regular_time": {
            "result": regular_time_result,
            "score": regular_time_score,
        },
        "extra_time": {
            "played": extra_time_played,
            "result": extra_time_result,
            "score": extra_time_score,
            "period_score": extra_time_period_score,
        },
        "penalties": {
            "played": penalties_played,
            "winner": penalties_winner,
            "winner_name": _winner_name_from_side(penalties_winner, home_name, away_name),
            "score": penalties_score,
        },
        "advance": {
            "winner": advance_side,
            "winner_result": _advance_result_from_side(advance_side),
            "winner_name": advance_name,
        },
    }


def _is_past_date(date_str: str) -> bool:
    """Check if the given date is in the past (比赛已结束).

    Dates that are today or before are considered past.
    Uses UTC date for consistency.
    """
    try:
        from datetime import datetime, timezone, timedelta
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
        # Consider dates before today as past (allow today for live predictions)
        today = datetime.now(timezone.utc).date()
        return target < today
    except (ValueError, Exception):
        return False

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
    (1, "乾", "天行健，君子以自强不息"),
    (2, "坤", "地势坤，君子以厚德载物"),
    (3, "屯", "云雷屯，君子以经纶"),
    (4, "蒙", "山下出泉，蒙"),
    (5, "需", "云上于天，需"),
    (6, "讼", "天与水违行，讼"),
    (7, "师", "地中有水，师"),
    (8, "比", "地上有水，比"),
    (9, "小畜", "风行天上，小畜"),
    (10, "履", "上天下泽，履"),
    (11, "泰", "天地交，泰"),
    (12, "否", "天地不交，否"),
    (13, "同人", "天与火，同人"),
    (14, "大有", "火在天上，大有"),
    (15, "谦", "地中有山，谦"),
    (16, "豫", "雷出地奋，豫"),
    (17, "随", "泽中有雷，随"),
    (18, "蛊", "山下有风，蛊"),
    (19, "临", "泽上有地，临"),
    (20, "观", "风行地上，观"),
    (21, "噬嗑", "雷电噬嗑"),
    (22, "贲", "山下有火，贲"),
    (23, "剥", "山附于地，剥"),
    (24, "复", "雷在地中，复"),
    (25, "无妄", "天下雷行，物与无妄"),
    (26, "大畜", "天在山中，大畜"),
    (27, "颐", "山下有雷，颐"),
    (28, "大过", "泽灭木，大过"),
    (29, "坎", "水洊至，习坎"),
    (30, "离", "明两作，离"),
    (31, "咸", "山上有泽，咸"),
    (32, "恒", "雷风恒"),
    (33, "遁", "天下有山，遁"),
    (34, "大壮", "雷在天上，大壮"),
    (35, "晋", "明出地上，晋"),
    (36, "明夷", "明入地中，明夷"),
    (37, "家人", "风自火出，家人"),
    (38, "睽", "上火下泽，睽"),
    (39, "蹇", "山上有水，蹇"),
    (40, "解", "雷雨作，解"),
    (41, "损", "山下有泽，损"),
    (42, "益", "风雷益，上巽下震"),
    (43, "夬", "泽上于天，夬"),
    (44, "姤", "天下有风，姤"),
    (45, "萃", "泽上于地，萃"),
    (46, "升", "地中生木，升"),
    (47, "困", "泽无水，困"),
    (48, "井", "木上有水，井"),
    (49, "革", "泽中有火，革"),
    (50, "鼎", "木上有火，鼎"),
    (51, "震", "洊雷震"),
    (52, "艮", "兼山艮"),
    (53, "渐", "山上有木，渐"),
    (54, "归妹", "泽上有雷，归妹"),
    (55, "丰", "雷电皆至，丰"),
    (56, "旅", "山上有火，旅"),
    (57, "巽", "随风巽"),
    (58, "兑", "丽泽兑"),
    (59, "涣", "风行水上，涣"),
    (60, "节", "泽上有水，节"),
    (61, "中孚", "泽上有风，中孚"),
    (62, "小过", "山上有雷，小过"),
    (63, "既济", "水在火上，既济"),
    (64, "未济", "火在水上，未济"),
]


# ---------------------------------------------------------------------------
# 天纪比分推演 (Tianji Score Divination) — divination-driven score oracle
# ---------------------------------------------------------------------------

# Hexagram match pattern categories (卦象比赛格局)
# Each hexagram maps to a match pattern that determines the style of play
# and the goal-scoring tendency of the match.
_HEX_PATTERN: dict[int, str] = {
    # 刚健型 (Dominant) — decisive, forceful victories, high scoring for the dominant side
    1: "dominant", 14: "dominant", 26: "dominant", 28: "dominant", 34: "dominant", 43: "dominant",
    # 柔顺型 (Defensive) — patient, defensive games, low total goals
    2: "defensive", 5: "defensive", 15: "defensive", 19: "defensive", 23: "defensive",
    33: "defensive", 36: "defensive", 39: "defensive", 52: "defensive", 60: "defensive",
    # 争斗型 (Conflict) — chaotic, controversial, unpredictable totals
    6: "conflict", 21: "conflict", 38: "conflict", 47: "conflict", 49: "conflict",
    # 和合型 (Harmonic) — balanced, close games, draws likely
    8: "harmonic", 11: "harmonic", 13: "harmonic", 20: "harmonic",
    31: "harmonic", 37: "harmonic", 45: "harmonic", 58: "harmonic",
    # 变动型 (Volatile) — unpredictable, dramatic swings, late drama
    3: "volatile", 16: "volatile", 17: "volatile", 24: "volatile", 30: "volatile",
    40: "volatile", 42: "volatile", 50: "volatile", 51: "volatile", 54: "volatile",
    55: "volatile", 56: "volatile", 62: "volatile", 63: "volatile", 64: "volatile",
    # 阻塞型 (Stagnant) — low scoring, blocked play, few clear chances
    4: "stagnant", 7: "stagnant", 9: "stagnant", 10: "stagnant", 12: "stagnant",
    18: "stagnant", 22: "stagnant", 25: "stagnant", 27: "stagnant", 29: "stagnant",
    32: "stagnant", 35: "stagnant", 41: "stagnant", 44: "stagnant", 46: "stagnant",
    48: "stagnant", 53: "stagnant", 57: "stagnant", 59: "stagnant", 61: "stagnant",
}

# Fortune level → goal qi (进球气场) — the mystical energy of goal-scoring
# derived from the combined Tianji + Hexagram fortune level
# v2: raised base to produce wider score spread (avg raw ~1.5 instead of ~1.0)
_FORTUNE_GOAL_QI: dict[str, float] = {
    "大吉": 3.2,   # Dominant offense → can score 3-4 goals
    "吉":   2.4,   # Strong offense → 2-3 goals likely
    "小吉": 1.6,   # Moderate offense → 1-2 goals
    "平":   1.0,   # Average → ~1 goal
    "小凶": 0.6,   # Weak offense → ~0-1 goal
    "凶":   0.35,  # Very weak → likely 0 goals
    "大凶": 0.2,   # Cursed → almost certainly 0
}

# Match pattern → rhythm modifier (比赛节奏系数)
# Determines whether the total goals in the match tend to be higher or lower
# v2: widened range to create more extreme match types
_PATTERN_RHYTHM: dict[str, float] = {
    "dominant":  1.30,  # Forceful play → significantly more total goals
    "defensive": 0.50,  # Patient defense → much fewer total goals
    "conflict":  1.05,  # Chaotic → average, but unpredictable
    "harmonic":  0.80,  # Balanced → below average, draws likely
    "volatile":  1.15,  # Dramatic → above average, late swings
    "stagnant":  0.40,  # Blocked → very few goals
}

# Palace branch (地支) goal influence — each branch carries different qi
# v2: doubled values for more impact
_BRANCH_GOAL_ENERGY: dict[str, float] = {
    "子": 0.0,  "丑": -0.2, "寅": 0.4,  "卯": 0.2,
    "辰": 0.0,  "巳": 0.3,  "午": 0.5,  "未": 0.15,
    "申": 0.1,  "酉": -0.1, "戌": -0.2, "亥": 0.0,
}

# Star goal influence (星曜进球影响)
# Auspicious stars boost goal-scoring; inauspicious stars hinder
# v2: boosted values for more star-driven variance
_STAR_GOAL_INFLUENCE: dict[str, float] = {
    "紫微": 0.70,   # Emperor star — massive offensive boost
    "天府": 0.50,   # Treasury star — strong boost
    "太阳": 0.35,   # Sun star — bright, open play
    "太阴": 0.25,   # Moon star — subtle, tactical
    "左辅": 0.15,   # Assistant — slight help
    "右弼": 0.15,   # Support — slight help
    "化忌": -0.50,  # Jealousy star — mistakes, own goals, weakened offense
    "擎羊": 0.25,   # Blade star — physical → set pieces → goals
    "陀罗": 0.15,   # Halberd star — physical → possible penalties
    "火星": 0.35,   # Fire star — explosive, volatile
}

# Hexagram destiny variance (卦象天命散度)
# Each hexagram carries a "散度" that determines how much the score
# can deviate from the baseline — some hexagrams produce wild results,
# others produce stable, predictable outcomes.
# Computed deterministically from hex number.
def _hex_destiny_variance(hex_num: int) -> float:
    """Compute the destiny variance for a hexagram (天命散度).

    Returns a value in [0.0, 1.0]:
    - 0.0 = perfectly stable, no deviation
    - 1.0 = maximum chaos, wild scores possible
    """
    # Use prime-number mixing for deterministic but well-distributed values
    v = ((hex_num * 31 + 17) % 97) / 97.0
    return round(v, 3)

# Fortune level order for shifting
_FORTUNE_ORDER = ["大凶", "凶", "小凶", "平", "小吉", "吉", "大吉"]


def _categorize_hexagram(hex_num: int) -> str:
    """Return the match pattern category for a hexagram number."""
    return _HEX_PATTERN.get(hex_num, "harmonic")


def _combined_fortune_level(combined_mod: float) -> str:
    """Map combined Tianji+Hexagram modifier to fortune level (大吉…大凶)."""
    if combined_mod >= 2.5:
        return "大吉"
    elif combined_mod >= 1.2:
        return "吉"
    elif combined_mod >= 0.3:
        return "小吉"
    elif combined_mod > -0.3:
        return "平"
    elif combined_mod > -1.2:
        return "小凶"
    elif combined_mod > -2.5:
        return "凶"
    else:
        return "大凶"


def _compute_star_goal_influence(stars: list[str]) -> float:
    """Compute total goal influence from stars in a palace."""
    total = 0.0
    for star in stars:
        # Extract Chinese name from star string (e.g., "紫微 (Ziwei)" → "紫微")
        cn = star.split(" ")[0] if " " in star else star
        total += _STAR_GOAL_INFLUENCE.get(cn, 0.0)
    return round(total, 2)


def _tianji_score_oracle(
    *,
    hex_num: int,
    tianji_home_modifier: float,
    tianji_away_modifier: float,
    hexagram_home_modifier: float,
    hexagram_away_modifier: float,
    home_stars: list[str],
    away_stars: list[str],
    host_palace_branch: str,
    guest_palace_branch: str,
    has_physical_conflict: bool,
    home_final: float,
    away_final: float,
    predicted_outcome: str,
) -> dict:
    """天纪比分推演 v2 — Divination-driven score oracle.

    The score is decided by the hexagram pattern, star positions, and
    fortune levels — NOT by mathematical probability.  The data model
    score acts only as a reality boundary, never as the primary driver.

    Decision chain (决策链):
      1. 卦象定格局 — hexagram determines match pattern
      2. 运势定进球 — combined fortune determines goal qi
      3. 星曜定细节 — stars determine fine-grained modifiers
      4. 宫位定气场 — palace branches contribute energy
      5. 格局定分势 — pattern-specific score shaping (dominant→big wins, harmonic→draws)
      6. 卦象定散度 — hexagram destiny variance determines score volatility
      7. 数据做校验 — data model provides sanity boundary
    """
    # Step 1: 卦象定格局 — hexagram determines match pattern
    pattern = _categorize_hexagram(hex_num)

    # Step 2: 运势定进球 — combined fortune determines goal qi
    combined_home_mod = tianji_home_modifier + hexagram_home_modifier
    combined_away_mod = tianji_away_modifier + hexagram_away_modifier
    home_fortune = _combined_fortune_level(combined_home_mod)
    away_fortune = _combined_fortune_level(combined_away_mod)

    home_goal_qi = _FORTUNE_GOAL_QI.get(home_fortune, 1.0)
    away_goal_qi = _FORTUNE_GOAL_QI.get(away_fortune, 1.0)

    # Step 3: 星曜定细节 — stars determine fine-grained modifiers
    home_star_bonus = _compute_star_goal_influence(home_stars)
    away_star_bonus = _compute_star_goal_influence(away_stars)

    # Step 3.5: 预警星防守折扣 (Warning Star Defensive Discount)
    # When 化忌 (Huaji) or 陀罗 (Tuoluo) appears on the favored side,
    # apply a defensive discount simulating errors, own goals, or bad luck.
    warning_star_active = False
    _fav_is_home = combined_home_mod > combined_away_mod
    if _fav_is_home:
        if any("化忌" in s or "陀罗" in s for s in home_stars):
            home_star_bonus -= 0.35
            warning_star_active = True
    else:
        if any("化忌" in s or "陀罗" in s for s in away_stars):
            away_star_bonus -= 0.35
            warning_star_active = True

    # Step 4: 宫位定气场 — palace branches contribute energy
    home_branch_energy = _BRANCH_GOAL_ENERGY.get(host_palace_branch, 0.0)
    away_branch_energy = _BRANCH_GOAL_ENERGY.get(guest_palace_branch, 0.0)

    # Step 5: 比赛节奏 — pattern determines goal rhythm
    rhythm = _PATTERN_RHYTHM.get(pattern, 0.85)

    # Compute raw goal values from divination
    home_goals_raw = home_goal_qi * rhythm + home_star_bonus + home_branch_energy
    away_goals_raw = away_goal_qi * rhythm + away_star_bonus + away_branch_energy

    # Step 5.5: 格局定分势 — pattern-specific score shaping
    fortune_gap = combined_home_mod - combined_away_mod

    if pattern == "dominant":
        # 刚健格局: amplify the stronger side, suppress the weaker
        if fortune_gap > 0:
            home_goals_raw *= 1.35  # Strong side gets 35% boost
            away_goals_raw *= 0.65  # Weak side gets 35% penalty
        else:
            away_goals_raw *= 1.35
            home_goals_raw *= 0.65
    elif pattern == "harmonic":
        # 和合格局: pull both sides toward the average → draws
        avg_qi = (home_goals_raw + away_goals_raw) / 2.0
        blend_harmonic = 0.55  # Strong pull toward average
        home_goals_raw = home_goals_raw * (1 - blend_harmonic) + avg_qi * blend_harmonic
        away_goals_raw = away_goals_raw * (1 - blend_harmonic) + avg_qi * blend_harmonic
    elif pattern == "defensive":
        # 柔顺格局: reduce both sides, favor low scores
        home_goals_raw *= 0.80
        away_goals_raw *= 0.80
    elif pattern == "volatile":
        # 变动格局: amplify the gap, add chaos
        if fortune_gap > 0:
            home_goals_raw += 0.5
            away_goals_raw -= 0.2
        else:
            away_goals_raw += 0.5
            home_goals_raw -= 0.2
    elif pattern == "conflict":
        # 争斗格局: both sides elevated but close → many goals, tight score
        home_goals_raw += 0.4
        away_goals_raw += 0.4
    elif pattern == "stagnant":
        # 阻塞格局: both sides heavily suppressed
        home_goals_raw *= 0.70
        away_goals_raw *= 0.70

    # Hexagram-specific deterministic macro-adjustment (卦象天命调整)
    # v2: much larger fingerprint → can shift scores by 1-2 goals
    hex_fingerprint = ((hex_num * 7 + 3) % 11 - 5) * 0.25
    if combined_home_mod > combined_away_mod:
        home_goals_raw += abs(hex_fingerprint)
        away_goals_raw -= abs(hex_fingerprint) * 0.4
    else:
        away_goals_raw += abs(hex_fingerprint)
        home_goals_raw -= abs(hex_fingerprint) * 0.4

    # Step 6: 卦象定散度 — hexagram destiny variance determines volatility
    # The destiny variance controls how much the score can "swing" from the
    # baseline — some hexagrams produce wild results, others are stable.
    destiny_var = _hex_destiny_variance(hex_num)
    # Apply deterministic variance shift based on hex_num parity
    # This ensures the same match always produces the same score
    var_shift = ((hex_num * 13 + 7) % 19 - 9) * 0.08 * destiny_var
    home_goals_raw += var_shift
    away_goals_raw -= var_shift * 0.6

    # Physical conflict modifier (争斗系数)
    if has_physical_conflict:
        home_goals_raw += 0.25
        away_goals_raw += 0.25

    # 天命收敛 (Destiny Convergence) — soft cap for extreme raw values
    # In real football, scoring 5+ goals is very rare. We dampen raw values
    # above the cap to keep scores realistic while still allowing occasional
    # high-scoring anomalies (天命异象).
    # Three-tier cap: wider openings for large mismatches to allow blowouts.
    _oracle_data_gap = abs(home_final - away_final)
    if _oracle_data_gap > 25:
        _oracle_cap, _oracle_overflow = 7.0, 0.55
    elif _oracle_data_gap > 20:
        _oracle_cap, _oracle_overflow = 5.0, 0.45
    else:
        _oracle_cap, _oracle_overflow = 4.0, 0.35
    if home_goals_raw > _oracle_cap:
        home_goals_raw = _oracle_cap + (home_goals_raw - _oracle_cap) * _oracle_overflow
    if away_goals_raw > _oracle_cap:
        away_goals_raw = _oracle_cap + (away_goals_raw - _oracle_cap) * _oracle_overflow

    # Step 7: 数据做校验 — reality boundary from data model
    data_gap = home_final - away_final
    divine_gap = home_goals_raw - away_goals_raw

    # If divination wildly contradicts the data model, apply gentle dampening
    # (e.g., divination says home scores 3+ but data says away is much stronger)
    if (divine_gap > 3.0 and data_gap < -8.0) or (divine_gap < -3.0 and data_gap > 8.0):
        blend = 0.30  # Pull 30% toward data model
        home_goals_raw = home_goals_raw * (1 - blend) + (home_final / 45.0) * blend
        away_goals_raw = away_goals_raw * (1 - blend) + (away_final / 45.0) * blend

    # Round to integer goals
    home_goals = max(0, round(home_goals_raw))
    away_goals = max(0, round(away_goals_raw))

    # Enforce outcome consistency — score must match predicted outcome
    if predicted_outcome == "home_win" and home_goals <= away_goals:
        if home_goals_raw >= away_goals_raw:
            home_goals = max(1, away_goals + 1)
        else:
            away_goals = max(0, away_goals - 1)
            if home_goals <= away_goals:
                home_goals = away_goals + 1
    elif predicted_outcome == "away_win" and away_goals <= home_goals:
        if away_goals_raw >= home_goals_raw:
            away_goals = max(1, home_goals + 1)
        else:
            home_goals = max(0, home_goals - 1)
            if away_goals <= home_goals:
                away_goals = home_goals + 1
    elif predicted_outcome == "draw" and home_goals != away_goals:
        avg = max(0, round((home_goals_raw + away_goals_raw) / 2))
        home_goals = avg
        away_goals = avg

    return {
        "home": home_goals,
        "away": away_goals,
        "divination_trace": {
            "method": "tianji_score_oracle_v2",
            "pattern": pattern,
            "home_fortune": home_fortune,
            "away_fortune": away_fortune,
            "combined_home_mod": round(combined_home_mod, 2),
            "combined_away_mod": round(combined_away_mod, 2),
            "home_goal_qi": round(home_goal_qi, 2),
            "away_goal_qi": round(away_goal_qi, 2),
            "rhythm": rhythm,
            "home_star_bonus": round(home_star_bonus, 2),
            "away_star_bonus": round(away_star_bonus, 2),
            "home_branch_energy": home_branch_energy,
            "away_branch_energy": away_branch_energy,
            "hex_fingerprint": round(hex_fingerprint, 3),
            "destiny_variance": destiny_var,
            "var_shift": round(var_shift, 3),
            "home_goals_raw": round(home_goals_raw, 2),
            "away_goals_raw": round(away_goals_raw, 2),
            "warning_star_active": warning_star_active,
        },
    }


def _tianji_clean_sheet(home_fortune: str, away_fortune: str, pattern: str) -> dict:
    """Compute clean-sheet probability from fortune levels and match pattern.

    Higher fortune → less likely to concede; defensive/stagnant patterns
    → higher clean-sheet probability for both sides.
    """
    qi_map = _FORTUNE_GOAL_QI
    home_qi = qi_map.get(home_fortune, 1.0)
    away_qi = qi_map.get(away_fortune, 1.0)

    # Base clean-sheet probability: lower opponent qi → higher chance
    rhythm = _PATTERN_RHYTHM.get(pattern, 0.85)

    # Clean sheet ≈ probability that opponent scores 0 goals
    # Approximate as exp(-opponent_goal_qi * rhythm)
    import math as _m
    home_cs = round(min(0.65, max(0.06, _m.exp(-away_qi * rhythm))), 2)
    away_cs = round(min(0.65, max(0.06, _m.exp(-home_qi * rhythm))), 2)

    return {"home": home_cs, "away": away_cs}


def _tianji_scoreline_reason(candidate: dict, base_score: dict, branch_name: str) -> str:
    """Generate divination-flavored reason for a scoreline candidate."""
    h, a = candidate["home"], candidate["away"]
    bh, ba = base_score["home"], base_score["away"]
    score_text = f"{h}-{a}"

    if h == bh and a == ba:
        return f"天纪主运：卦象运势直指 {score_text}，此为天命正轨。"
    if branch_name == "fortune_up":
        return f"运势上浮分支：若吉运加持，进球气场增强，比分趋向 {score_text}。"
    if branch_name == "fortune_down":
        return f"运势下沉分支：若凶运侵扰，进攻受制，比分收缩至 {score_text}。"
    if branch_name == "counter_fortune":
        return f"逆运分支：若客队运势反转，比赛走向变异，{score_text} 成为可能。"
    if branch_name == "pattern_shift":
        return f"格局变轨：若卦象暗藏变数，比赛节奏切换，{score_text} 浮现。"
    if branch_name == "blowout":
        return f"大比分分支：实力碾压，运势大开门，强队进攻火力全开，{score_text} 屠杀剧本。"
    if branch_name == "upset_stalemate":
        return f"爆冷闷平分支：弱队铁桶阵死守，强队久攻不下，{score_text} 冷门伏击。"
    if h + a > bh + ba:
        return f"高进球分支：星曜助推，比赛开放度提升，{score_text} 有迹可循。"
    return f"低进球分支：若双方趋于保守，进球收敛，{score_text} 为防守剧本。"


def _tianji_scoreline_distribution(
    *,
    hex_num: int,
    tianji_home_modifier: float,
    tianji_away_modifier: float,
    hexagram_home_modifier: float,
    hexagram_away_modifier: float,
    home_stars: list[str],
    away_stars: list[str],
    host_palace_branch: str,
    guest_palace_branch: str,
    has_physical_conflict: bool,
    base_score: dict,
    predicted_outcome: str,
    home_final: float,
    away_final: float,
    is_opener: bool = False,
) -> tuple[list[dict], dict, float, float]:
    """天纪比分分布 — Divination-based scoreline distribution.

    Instead of mathematical probability, each alternative score represents
    a "fate branch" (命运分支) based on fortune shifts and pattern changes.
    The weights represent how strongly the divination supports each branch
    (命理权重), NOT statistical probability.
    """
    pattern = _categorize_hexagram(hex_num)
    combined_home_mod = tianji_home_modifier + hexagram_home_modifier
    combined_away_mod = tianji_away_modifier + hexagram_away_modifier
    home_fortune = _combined_fortune_level(combined_home_mod)
    away_fortune = _combined_fortune_level(combined_away_mod)

    rhythm = _PATTERN_RHYTHM.get(pattern, 0.85)
    home_star_bonus = _compute_star_goal_influence(home_stars)
    away_star_bonus = _compute_star_goal_influence(away_stars)

    # 预警星检测 (Warning Star Detection) — boost upset probability
    warning_star_active = False
    _fav_is_home_sd = combined_home_mod > combined_away_mod
    if _fav_is_home_sd:
        if any("化忌" in s or "陀罗" in s for s in home_stars):
            warning_star_active = True
    else:
        if any("化忌" in s or "陀罗" in s for s in away_stars):
            warning_star_active = True

    home_branch_energy = _BRANCH_GOAL_ENERGY.get(host_palace_branch, 0.0)
    away_branch_energy = _BRANCH_GOAL_ENERGY.get(guest_palace_branch, 0.0)

    # Expected goals from divination (goal qi values)
    home_expected_goals = round(_FORTUNE_GOAL_QI.get(home_fortune, 1.0) * rhythm + home_star_bonus, 2)
    away_expected_goals = round(_FORTUNE_GOAL_QI.get(away_fortune, 1.0) * rhythm + away_star_bonus, 2)

    # Opener dampening: reduce expected goals by 15% for tournament openers
    # (first match for both teams), reflecting more cautious play
    if is_opener:
        home_expected_goals = round(home_expected_goals * 0.85, 2)
        away_expected_goals = round(away_expected_goals * 0.85, 2)

    # Clean sheet from divination
    clean_sheet = _tianji_clean_sheet(home_fortune, away_fortune, pattern)

    # Pattern-specific shaping parameters (same logic as _tianji_score_oracle v2)
    fortune_gap = combined_home_mod - combined_away_mod

    # Helper: compute score from a given fortune pair (v2 with pattern shaping)
    def _score_from_fortune(hf: str, af: str) -> dict | None:
        h_qi = _FORTUNE_GOAL_QI.get(hf, 1.0)
        a_qi = _FORTUNE_GOAL_QI.get(af, 1.0)
        h_raw = h_qi * rhythm + home_star_bonus + home_branch_energy
        a_raw = a_qi * rhythm + away_star_bonus + away_branch_energy

        # Pattern-specific shaping (same as oracle v2)
        fg = combined_home_mod - combined_away_mod
        if pattern == "dominant":
            if fg > 0:
                h_raw *= 1.35
                a_raw *= 0.65
            else:
                a_raw *= 1.35
                h_raw *= 0.65
        elif pattern == "harmonic":
            avg_qi = (h_raw + a_raw) / 2.0
            h_raw = h_raw * 0.45 + avg_qi * 0.55
            a_raw = a_raw * 0.45 + avg_qi * 0.55
        elif pattern == "defensive":
            h_raw *= 0.80
            a_raw *= 0.80
        elif pattern == "volatile":
            if fg > 0:
                h_raw += 0.5
                a_raw -= 0.2
            else:
                a_raw += 0.5
                h_raw -= 0.2
        elif pattern == "conflict":
            h_raw += 0.4
            a_raw += 0.4
        elif pattern == "stagnant":
            h_raw *= 0.70
            a_raw *= 0.70

        if has_physical_conflict:
            h_raw += 0.25
            a_raw += 0.25
        # Apply hex fingerprint (v2: larger)
        fp = ((hex_num * 7 + 3) % 11 - 5) * 0.25
        if combined_home_mod > combined_away_mod:
            h_raw += abs(fp)
            a_raw -= abs(fp) * 0.4
        else:
            a_raw += abs(fp)
            h_raw -= abs(fp) * 0.4
        # Apply destiny variance
        dvar = _hex_destiny_variance(hex_num)
        vshift = ((hex_num * 13 + 7) % 19 - 9) * 0.08 * dvar
        h_raw += vshift
        a_raw -= vshift * 0.6

        # 天命收敛 — soft cap for extreme raw values (dynamic for mismatches)
        _sf_data_gap = abs(home_final - away_final)
        if _sf_data_gap > 25:
            _sf_cap, _sf_overflow = 7.0, 0.55
        elif _sf_data_gap > 20:
            _sf_cap, _sf_overflow = 5.0, 0.45
        else:
            _sf_cap, _sf_overflow = 4.0, 0.35
        if h_raw > _sf_cap:
            h_raw = _sf_cap + (h_raw - _sf_cap) * _sf_overflow
        if a_raw > _sf_cap:
            a_raw = _sf_cap + (a_raw - _sf_cap) * _sf_overflow

        h = max(0, round(h_raw))
        a = max(0, round(a_raw))
        # Enforce outcome
        if predicted_outcome == "home_win" and h <= a:
            h = max(1, a + 1)
        elif predicted_outcome == "away_win" and a <= h:
            a = max(1, h + 1)
        elif predicted_outcome == "draw" and h != a:
            avg = max(0, round((h_raw + a_raw) / 2))
            h = a = avg
        return {"home": h, "away": a}

    # Generate fate branches (命运分支)
    home_idx = _FORTUNE_ORDER.index(home_fortune) if home_fortune in _FORTUNE_ORDER else 3
    away_idx = _FORTUNE_ORDER.index(away_fortune) if away_fortune in _FORTUNE_ORDER else 3

    branches: list[tuple[dict, str, float]] = []

    # Primary branch (天命正轨)
    branches.append((base_score, "primary", 0.42))

    # Fortune up branch (运势上浮)
    if home_idx < 6:
        alt_hf = _FORTUNE_ORDER[home_idx + 1]
        alt = _score_from_fortune(alt_hf, away_fortune)
        if alt and alt != base_score:
            branches.append((alt, "fortune_up", 0.20))

    # Fortune down branch (运势下沉)
    if home_idx > 0:
        alt_hf = _FORTUNE_ORDER[home_idx - 1]
        alt = _score_from_fortune(alt_hf, away_fortune)
        if alt and alt != base_score:
            branches.append((alt, "fortune_down", 0.15))

    # Counter fortune branch (逆运分支)
    if away_idx < 6:
        alt_af = _FORTUNE_ORDER[away_idx + 1]
        alt = _score_from_fortune(home_fortune, alt_af)
        if alt and alt != base_score:
            branches.append((alt, "counter_fortune", 0.12))

    # Pattern shift branch (格局变轨)
    # Try shifting to an adjacent pattern
    pattern_alt_map = {
        "dominant": "volatile", "defensive": "stagnant", "conflict": "volatile",
        "harmonic": "conflict", "volatile": "dominant", "stagnant": "defensive",
    }
    alt_pattern = pattern_alt_map.get(pattern, "harmonic")
    alt_rhythm = _PATTERN_RHYTHM.get(alt_pattern, 0.85)
    h_qi = _FORTUNE_GOAL_QI.get(home_fortune, 1.0)
    a_qi = _FORTUNE_GOAL_QI.get(away_fortune, 1.0)
    alt_h_raw = h_qi * alt_rhythm + home_star_bonus + home_branch_energy
    alt_a_raw = a_qi * alt_rhythm + away_star_bonus + away_branch_energy

    # Apply alt-pattern shaping
    fg = combined_home_mod - combined_away_mod
    if alt_pattern == "dominant":
        if fg > 0:
            alt_h_raw *= 1.35
            alt_a_raw *= 0.65
        else:
            alt_a_raw *= 1.35
            alt_h_raw *= 0.65
    elif alt_pattern == "harmonic":
        avg_qi = (alt_h_raw + alt_a_raw) / 2.0
        alt_h_raw = alt_h_raw * 0.45 + avg_qi * 0.55
        alt_a_raw = alt_a_raw * 0.45 + avg_qi * 0.55
    elif alt_pattern == "defensive":
        alt_h_raw *= 0.80
        alt_a_raw *= 0.80
    elif alt_pattern == "volatile":
        if fg > 0:
            alt_h_raw += 0.5
            alt_a_raw -= 0.2
        else:
            alt_a_raw += 0.5
            alt_h_raw -= 0.2
    elif alt_pattern == "conflict":
        alt_h_raw += 0.4
        alt_a_raw += 0.4
    elif alt_pattern == "stagnant":
        alt_h_raw *= 0.70
        alt_a_raw *= 0.70

    if has_physical_conflict:
        alt_h_raw += 0.25
        alt_a_raw += 0.25
    # Apply hex fingerprint
    fp = ((hex_num * 7 + 3) % 11 - 5) * 0.25
    if combined_home_mod > combined_away_mod:
        alt_h_raw += abs(fp)
        alt_a_raw -= abs(fp) * 0.4
    else:
        alt_a_raw += abs(fp)
        alt_h_raw -= abs(fp) * 0.4
    # Apply destiny variance
    dvar = _hex_destiny_variance(hex_num)
    vshift = ((hex_num * 13 + 7) % 19 - 9) * 0.08 * dvar
    alt_h_raw += vshift
    alt_a_raw -= vshift * 0.6

    # 天命收敛 — soft cap for extreme raw values (dynamic for mismatches)
    _ps_data_gap = abs(home_final - away_final)
    if _ps_data_gap > 25:
        _ps_cap, _ps_overflow = 7.0, 0.55
    elif _ps_data_gap > 20:
        _ps_cap, _ps_overflow = 5.0, 0.45
    else:
        _ps_cap, _ps_overflow = 4.0, 0.35
    if alt_h_raw > _ps_cap:
        alt_h_raw = _ps_cap + (alt_h_raw - _ps_cap) * _ps_overflow
    if alt_a_raw > _ps_cap:
        alt_a_raw = _ps_cap + (alt_a_raw - _ps_cap) * _ps_overflow

    alt_h = max(0, round(alt_h_raw))
    alt_a = max(0, round(alt_a_raw))
    if predicted_outcome == "home_win" and alt_h <= alt_a:
        alt_h = max(1, alt_a + 1)
    elif predicted_outcome == "away_win" and alt_a <= alt_h:
        alt_a = max(1, alt_h + 1)
    elif predicted_outcome == "draw" and alt_h != alt_a:
        avg = max(0, round((alt_h_raw + alt_a_raw) / 2))
        alt_h = alt_a = avg
    alt_score = {"home": alt_h, "away": alt_a}
    if alt_score != base_score:
        branches.append((alt_score, "pattern_shift", 0.08))

    # ── 大差距双峰分布 (Large-Gap Bimodal Scoring) ──
    # When the data_score gap is extreme (|delta| > 20), history shows two outcomes:
    # either a blowout (Germany 7-1 Curaçao) or an upset/stalemate (Spain 0-0 Cape Verde).
    _delta = abs(home_final - away_final)
    if _delta > 20:
        _fav_is_home = home_final > away_final
        _fav_fortune = home_fortune if _fav_is_home else away_fortune
        _dog_fortune = away_fortune if _fav_is_home else home_fortune

        # Blowout branch (大比分分支): when the favorite dominates
        _blowout_w = min(0.15, (_delta - 20) * 0.005)
        if _fav_fortune in ("大吉", "吉", "小吉") and _blowout_w > 0.02:
            for bh, ba in [(4, 0), (5, 1), (4, 1)]:
                if _fav_is_home:
                    _bs = {"home": bh, "away": ba}
                else:
                    _bs = {"home": ba, "away": bh}
                _bw = _blowout_w / 3.0
                branches.append((_bs, "blowout", _bw))

        # Upset/stalemate branch (爆冷/闷平分支): underdog parks the bus
        # Boost weight by 50% when warning stars (化忌/陀罗) are on the favored side
        _upset_w = min(0.12, (_delta - 20) * 0.004)
        if warning_star_active:
            _upset_w *= 1.5
        if _upset_w > 0.02:
            for uh, ua in [(0, 0), (1, 1)]:
                _us = {"home": uh, "away": ua}
                _uw = _upset_w / 2.0
                branches.append((_us, "upset_stalemate", _uw))

    # Deduplicate and build distribution
    seen: set[tuple[int, int]] = set()
    distribution: list[dict] = []
    total_weight = 0.0

    for score, branch_name, weight in branches:
        key = (score["home"], score["away"])
        if key in seen:
            continue
        seen.add(key)
        total_weight += weight
        distribution.append({
            "score": score,
            "probability": weight,  # Will be normalized below
            "reason": _tianji_scoreline_reason(score, base_score, branch_name),
        })

    # Normalize weights to sum to 1.0
    if total_weight > 0:
        for item in distribution:
            item["probability"] = round(item["probability"] / total_weight, 3)

    # Ensure we have at least one entry
    if not distribution:
        distribution.append({
            "score": base_score,
            "probability": 1.0,
            "reason": f"天纪主运：卦象运势直指 {base_score['home']}-{base_score['away']}，此为天命正轨。",
        })

    return distribution, clean_sheet, home_expected_goals, away_expected_goals


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


def _reconcile_evidence_from_disk(
    evidence_plan: dict, ed_root: Path
) -> dict:
    """Cross-check evidence plan statuses against actual data files on disk.

    When new data files have been added after the evidence plan was generated,
    this function upgrades stale ``blocked`` / ``partial`` statuses so the
    evidence gap list reflects reality.  Status is only ever upgraded, never
    downgraded.
    """
    plan = dict(evidence_plan)
    items = list(plan.get("items", []))

    def _j(rel: str) -> dict:
        return load_json(ed_root / rel, {})

    def _upgrade(item: dict, new_status: str, new_counts: dict, cleared: list[str] | None = None):
        old = item.get("status", "blocked")
        order = {"blocked": 0, "partial": 1, "complete": 2}
        if order.get(new_status, 0) > order.get(old, 0):
            item["status"] = new_status
            if cleared is not None:
                item["blockers"] = [b for b in item.get("blockers", []) if b not in cleared]
        if new_counts:
            item["current_counts"] = new_counts

    for item in items:
        eid = item.get("evidence_id", "")

        # --- fifa_rankings ---
        if eid == "fifa_rankings":
            data = _j("rankings/fifa-men-ranking.json")
            rankings = data.get("rankings", [])
            count = len(rankings)
            if count >= 40:
                _upgrade(item, "complete", {"ranked_teams": count}, ["ranking_json_empty"])

        # --- historical_worldcup_results ---
        elif eid == "historical_worldcup_results":
            data = _j("history/team-wc-history.json")
            teams = data.get("teams", [])
            with_hist = sum(1 for t in teams if t.get("wc_appearances", 0) > 0)
            if with_hist >= 30:
                _upgrade(
                    item, "partial",
                    {"teams_with_history": with_hist, "editions_processed": data.get("summary", {}).get("editions_processed", 0)},
                    ["historical_results_snapshot_missing", "historical_results_fetch_failed", "source_fetch_failed"],
                )

        # --- squad_depth_position_balance ---
        elif eid == "squad_depth_position_balance":
            data = _j("squad-depth-features.json")
            teams = data.get("teams", [])
            with_pos = sum(1 for t in teams if t.get("position_counts"))
            if with_pos >= 40:
                _upgrade(item, "complete", {"teams": with_pos}, ["position_depth_features_not_compiled"])

        # --- venue_rest_travel ---
        elif eid == "venue_rest_travel":
            ledger = load_json(ed_root.parent.parent / "match-ledger.json", {})
            matches = ledger.get("matches", [])
            with_ko = sum(1 for m in matches if m.get("kickoff_at"))
            if with_ko >= 10:
                _upgrade(
                    item, "partial",
                    {"matches": len(matches), "matches_with_kickoff": with_ko},
                    ["fixture_schedule_required_for_rest_travel", "fixture_schedule_not_imported"],
                )

    plan["items"] = items
    return plan


def _build_history_index(root: Path, edition: str) -> dict[str, dict]:
    """Load team World Cup history and map upper-cased team_id -> record."""
    data = load_edition_data_json(root, edition, "history/team-wc-history.json", {"teams": []})
    index: dict[str, dict] = {}
    for team in data.get("teams", []):
        tid = str(team.get("team_id", "")).upper()
        if tid:
            index[tid] = team
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


def _count_prior_matches(team_id: str, matches: list[dict], current_kickoff: datetime) -> int:
    """Count the number of prior matches for *team_id* before *current_kickoff*."""
    team_upper = team_id.upper()
    count = 0
    for match in matches:
        kickoff = parse_datetime(str(match.get("kickoff_at", "")))
        if not kickoff:
            continue
        if kickoff >= current_kickoff:
            continue
        home = str(match.get("home_team", {}).get("team_id", "")).upper()
        away = str(match.get("away_team", {}).get("team_id", "")).upper()
        if home == team_upper or away == team_upper:
            count += 1
    return count


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


def score_historical_proxy(
    team_ranking: dict | None,
    team_history: dict | None = None,
) -> float:
    """Component 3: historical performance proxy (0-100).

    When ``team_history`` (from ``team-wc-history.json``) is available the
    score blends actual World Cup pedigree (titles, appearances, win-rate,
    best result) with a FIFA-ranking baseline.  The ranking floor ensures
    teams with sparse World Cup history but strong current form are not
    over-penalised.  When no history exists, the fallback is deliberately
    shrunk toward neutral so ranking strength is not double-counted as both
    current quality and historical pedigree.
    """
    # --- Ranking baseline (always computed) ---
    ranking_baseline = 30.0
    if team_ranking:
        points = float(team_ranking.get("points", 0))
        ranking_baseline = _normalise_ranking_points(points)

    # --- Real history path ---
    if team_history and team_history.get("wc_appearances", 0) > 0:
        appearances = int(team_history.get("wc_appearances", 0))
        titles = int(team_history.get("wc_titles", 0))
        total_matches = int(team_history.get("wc_total_matches", 0))
        wins = int(team_history.get("wc_wins", 0))
        best = str(team_history.get("wc_best_result", ""))

        # Titles: 0→0, 1→15, 2→25, 3→32, 4→38, 5+→42
        title_score = min(42.0, titles * 12.0 + max(0, titles - 1) * 3.0)

        # Appearances: each appearance worth up to 2 pts, cap 20
        appearance_score = min(20.0, appearances * 2.0)

        # Win rate: (wins / total_matches) * 25
        win_rate_score = (wins / total_matches * 25.0) if total_matches > 0 else 0.0

        # Best result bonus
        best_map = {
            "winner": 15.0, "runner_up": 12.0, "third_place": 10.0,
            "semi_finals": 9.0, "quarter_finals": 7.0,
            "round_of_16": 5.0, "group_stage": 2.0,
        }
        best_score = best_map.get(best, 3.0)

        history_raw = title_score + appearance_score + win_rate_score + best_score

        # Blend: 40% real history + 60% ranking baseline
        # This prevents teams with sparse WC history from being over-penalised
        # while still rewarding genuine World Cup pedigree.
        blended = history_raw * 0.4 + ranking_baseline * 0.6
        return round(min(100.0, max(0.0, blended)), 2)

    # --- Fallback: neutral-shrunk FIFA ranking proxy ---
    neutralized = 50.0 + (ranking_baseline - 50.0) * 0.75
    return round(min(100.0, max(0.0, neutralized)), 2)


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


def compute_divination_overlay(date: str, match_id: str,
                                home_name: str = "", away_name: str = "") -> dict:
    """Compute the entertainment divination overlay for a match.

    Enhanced version that generates match-specific hexagram interpretation
    combining the I Ching hexagram with the actual matchup context.
    """
    number = _hexagram_hash(date, match_id)
    hex_num, hex_name, hex_interp = _HEXAGRAMS[number - 1]
    home_mod, away_mod = _modifier_from_hexagram(number)

    # Generate match-specific interpretation based on hexagram + modifiers
    match_interp = _generate_match_hexagram_interpretation(
        hex_num, hex_name, home_mod, away_mod, home_name, away_name,
        hex_interp=hex_interp
    )

    return {
        "hexagram_number": hex_num,
        "hexagram_name": hex_name,
        "interpretation": hex_interp,
        "home_modifier": home_mod,
        "away_modifier": away_mod,
        # New: match-specific fields
        "match_interpretation": match_interp["narrative"],
        "home_fortune": match_interp["home_fortune"],
        "away_fortune": match_interp["away_fortune"],
        "fortune_summary": match_interp["summary"],
    }


def _generate_match_hexagram_interpretation(
    hex_num: int, hex_name: str,
    home_mod: float, away_mod: float,
    home_name: str, away_name: str,
    hex_interp: str = ""
) -> dict:
    """Generate match-specific hexagram interpretation.

    Combines the I Ching hexagram meaning with home/away modifiers
    to produce a narrative specific to this matchup.
    """
    # Extract Chinese name from hex_name (e.g., "乾 (The Creative)" -> "乾")
    hex_cn = hex_name.split(" ")[0] if " " in hex_name else hex_name

    # Determine fortune level based on modifiers
    def fortune_level(mod: float) -> str:
        if mod >= 2.0:
            return "大吉"
        elif mod >= 1.0:
            return "吉"
        elif mod >= 0.3:
            return "小吉"
        elif mod > -0.3:
            return "平"
        elif mod > -1.0:
            return "小凶"
        elif mod > -2.0:
            return "凶"
        else:
            return "大凶"

    home_fortune = fortune_level(home_mod)
    away_fortune = fortune_level(away_mod)

    # Build narrative based on hexagram type and modifier comparison
    h_name = home_name or "主队"
    a_name = away_name or "客队"

    # Hexagram category analysis (simplified)
    _CREATIVE_HEXAGRAMS = {1, 14, 34, 43}  # Strong/active yang
    _RECEPTIVE_HEXAGRAMS = {2, 16, 24, 46}  # Nurturing yin
    _CONFLICT_HEXAGRAMS = {6, 23, 38, 49}   # Conflict/change
    _HARMONY_HEXAGRAMS = {8, 11, 45, 58}    # Harmony/cooperation

    narratives = []

    if hex_num in _CREATIVE_HEXAGRAMS:
        narratives.append(f"【{hex_cn}】卦显现，刚健之气充盈赛场。")
    elif hex_num in _RECEPTIVE_HEXAGRAMS:
        narratives.append(f"【{hex_cn}】卦当令，柔顺厚德之势成形。")
    elif hex_num in _CONFLICT_HEXAGRAMS:
        narratives.append(f"【{hex_cn}】卦动，攻守转换频繁，变数丛生。")
    elif hex_num in _HARMONY_HEXAGRAMS:
        narratives.append(f"【{hex_cn}】卦主和，双方或呈胶着态势。")
    else:
        narratives.append(f"本局得【{hex_cn}】卦，{hex_interp}。")

    # Modifier-based narrative
    if home_mod > away_mod + 1.5:
        narratives.append(f"{h_name}得天时之利，气势如虹。")
    elif away_mod > home_mod + 1.5:
        narratives.append(f"{a_name}客战得运，不可轻视。")
    elif abs(home_mod - away_mod) < 0.5:
        narratives.append("双方气运接近，胜负系于临场发挥。")

    # Specific fortune descriptions
    if home_mod >= 1.5:
        narratives.append(f"{h_name}星位高照，进攻端或有神来之笔。")
    elif home_mod <= -1.5:
        narratives.append(f"{h_name}需防运势低迷，后防宜稳扎稳打。")

    if away_mod >= 1.5:
        narratives.append(f"{a_name}暗藏杀机，反击值得警惕。")
    elif away_mod <= -1.5:
        narratives.append(f"{a_name}异地作战压力较大，破局需待时机。")

    narrative_str = " ".join(narratives[:3])  # Limit to 3 sentences

    # Summary for compact display
    if home_mod > away_mod:
        summary = f"利{h_name}"
    elif away_mod > home_mod:
        summary = f"利{a_name}"
    else:
        summary = "势均力敌"

    return {
        "narrative": narrative_str,
        "home_fortune": home_fortune,
        "away_fortune": away_fortune,
        "summary": summary,
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


def _realistic_stage_weights(match_id: str) -> tuple[float, float]:
    for prefix, weights in {
        "G": (0.75, 0.25),
        "R32": (0.78, 0.22),
        "R16": (0.80, 0.20),
        "QF": (0.82, 0.18),
        "SF": (0.82, 0.18),
        "F": (0.82, 0.18),
        "TP": (0.82, 0.18),
    }.items():
        if f"-{prefix}" in match_id:
            return weights
    return (0.80, 0.20)


def _poisson_pmf(lam: float, k: int) -> float:
    """Poisson PMF: P(X=k) = lambda^k * e^(-lambda) / k!"""
    if k < 0 or lam < 0:
        return 0.0
    if lam == 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _classify_market_odds(odds: dict | None) -> dict:
    if not odds:
        return {
            "status": "none",
            "reason": "odds_missing",
            "is_mock": False,
            "implied_probabilities": None,
            "market_outcome": None,
        }
    source = str(odds.get("source") or "missing")
    if odds.get("is_mock") or source in {"mock_bookmaker", "odds_unavailable"}:
        return {
            "status": "mock_invalid",
            "reason": source or "mock_odds",
            "is_mock": True,
            "implied_probabilities": None,
            "market_outcome": None,
        }
    try:
        home = float(odds.get("home_win"))
        draw = float(odds.get("draw"))
        away = float(odds.get("away_win"))
    except (TypeError, ValueError):
        return {
            "status": "none",
            "reason": "odds_not_parseable",
            "is_mock": False,
            "implied_probabilities": None,
            "market_outcome": None,
        }
    if min(home, draw, away) <= 1.01:
        return {
            "status": "suspect_market",
            "reason": "non_positive_market_shape",
            "is_mock": False,
            "implied_probabilities": None,
            "market_outcome": None,
        }
    raw_home = 1.0 / home
    raw_draw = 1.0 / draw
    raw_away = 1.0 / away
    total = raw_home + raw_draw + raw_away
    implied = {
        "home": round(raw_home / total, 3),
        "draw": round(raw_draw / total, 3),
        "away": round(raw_away / total, 3),
    }
    market_outcome = max(implied, key=implied.get)
    market_outcome = {"home": "home_win", "draw": "draw", "away": "away_win"}[market_outcome]
    suspect = (
        total < 1.01
        or total > 1.22
        or implied["draw"] > 0.48
        or implied["draw"] < 0.16
        or min(implied.values()) < 0.08
    )
    return {
        "status": "suspect_market" if suspect else "trusted_market",
        "reason": "shape_outlier" if suspect else "market_consensus_available",
        "is_mock": False,
        "implied_probabilities": implied,
        "market_outcome": market_outcome,
    }


def _overall_evidence_quality(*, market_status: str, local_gaps: list[str], evidence_gaps: list[str]) -> str:
    if market_status == "mock_invalid":
        return "unusable"
    if market_status == "suspect_market":
        return "suspect"
    if market_status == "trusted_market" and not local_gaps and not evidence_gaps:
        return "trusted"
    if market_status == "none" and (local_gaps or evidence_gaps):
        return "thin"
    if market_status == "trusted_market":
        return "thin"
    return "thin"


def _edge_tier(abs_edge: float) -> str:
    if abs_edge < 4.0:
        return "coinflip"
    if abs_edge < 8.0:
        return "slight"
    if abs_edge < 15.0:
        return "clear"
    return "strong"


def _pick_game_script(
    *,
    predicted_outcome: str,
    edge_tier: str,
    evidence_quality: str,
    phase: str,
    is_opener: bool,
    implied_probs: dict | None,
    home_final: float,
    away_final: float,
    news_swing: float,
) -> str:
    if predicted_outcome == "draw":
        return "low-event" if edge_tier in {"coinflip", "slight"} else "medium-event"
    if is_opener:
        return "low-event"
    # Evidence quality no longer forces low-event — data gaps reflect our knowledge,
    # not the actual game style. Game script should reflect the matchup itself.
    if edge_tier == "strong" and abs(home_final - away_final) >= 16:
        if phase != "group" and abs(news_swing) >= 1.5:
            return "open-game"
        if implied_probs and max(implied_probs.values()) >= 0.62:
            return "open-game"
        return "medium-event"
    if edge_tier in {"clear", "strong"}:
        return "medium-event"
    return "low-event"


def _goal_expectation(score: float, opponent_score: float, *, game_script: str = "medium-event", evidence_quality: str = "trusted") -> float:
    edge = score - opponent_score
    # Raised base (1.20 vs old 0.60), steeper coefficients (score/80, edge/50)
    # to match real World Cup scoring (~3.0-3.5 total goals per match).
    base = 1.20 + score / 80.0 + edge / 50.0
    # Gentler script multipliers — evidence gaps should not crush goal estimates
    script_multiplier = {
        "low-event": 0.92,
        "medium-event": 1.0,
        "open-game": 1.15,
    }.get(game_script, 1.0)
    # Thin evidence only mildly reduces xG (0.95 vs old 0.88)
    quality_multiplier = 0.95 if evidence_quality in {"thin", "suspect", "unusable"} else 1.0
    value = base * script_multiplier * quality_multiplier
    # Dynamic hard cap: allow higher xG for large mismatches
    _xg_cap = 5.5 if abs(edge) > 20 else 4.0
    return round(max(0.25, min(_xg_cap, value)), 2)


def _clean_sheet_probability(team_final: float, opponent_final: float, opponent_expected_goals: float, *, game_script: str = "medium-event") -> float:
    edge = team_final - opponent_final
    script_bonus = {"low-event": 0.05, "medium-event": 0.0, "open-game": -0.04}.get(game_script, 0.0)
    probability = 0.32 - opponent_expected_goals * 0.09 + edge * 0.004 + script_bonus
    return round(max(0.08, min(0.62, probability)), 2)


def _three_track_vote_summary(*, gap: float, market_outcome: str | None, market_status: str, home_news_sentiment: float, away_news_sentiment: float, rs_home: float, rs_away: float, sd_home: float, sd_away: float, hex_home_mod: float, hex_away_mod: float) -> dict:
    fundamentals = "home_win" if (gap > 0) else "away_win" if (gap < 0) else "draw"
    squad_side = "home_win" if (rs_home + sd_home) > (rs_away + sd_away) else "away_win" if (rs_away + sd_away) > (rs_home + sd_home) else "draw"
    if squad_side != fundamentals and abs(gap) < 6:
        fundamentals = squad_side
    info_side = "home_win" if home_news_sentiment > away_news_sentiment + 0.8 else "away_win" if away_news_sentiment > home_news_sentiment + 0.8 else "draw"
    mystic_side = "home_win" if hex_home_mod > hex_away_mod + 0.8 else "away_win" if hex_away_mod > hex_home_mod + 0.8 else "draw"
    votes = {"fundamentals": fundamentals, "market": market_outcome if market_status == "trusted_market" else None, "information": info_side, "mystic": mystic_side}
    lean = [v for v in votes.values() if v]
    counts = {k: lean.count(k) for k in {"home_win", "away_win", "draw"}}
    if not lean:
        consensus = "draw"
        leaders = ["draw"]
    else:
        max_count = max(counts.values())
        leaders = [outcome for outcome in ["draw", "home_win", "away_win"] if counts.get(outcome, 0) == max_count and max_count > 0]
        consensus = leaders[0] if len(leaders) == 1 else "draw"
    return {"votes": votes, "counts": counts, "consensus": consensus, "leaders": leaders, "tie_on_top": len(leaders) > 1}


def _determine_outcome_from_context(
    *,
    home_final: float,
    away_final: float,
    phase: str,
    market_status: str,
    market_outcome: str | None,
    home_news_sentiment: float,
    away_news_sentiment: float,
    rs_home: float,
    rs_away: float,
    sd_home: float,
    sd_away: float,
    evidence_quality: str,
    three_track_votes: dict | None = None,
) -> tuple[str, str, list[str]]:
    gap = home_final - away_final
    abs_gap = abs(gap)
    tier = _edge_tier(abs_gap)
    preferred = "home_win" if gap > 0 else "away_win" if gap < 0 else "draw"
    alignment_flags: list[str] = []
    squad_side = "home_win" if (rs_home + sd_home) > (rs_away + sd_away) else "away_win" if (rs_away + sd_away) > (rs_home + sd_home) else "draw"
    news_side = "home_win" if home_news_sentiment > away_news_sentiment + 0.8 else "away_win" if away_news_sentiment > home_news_sentiment + 0.8 else "draw"
    if market_outcome and market_status == "trusted_market" and market_outcome == preferred:
        alignment_flags.append("market")
    if squad_side == preferred:
        alignment_flags.append("squad")
    if news_side == preferred:
        alignment_flags.append("news")
    three_track_consensus = (three_track_votes or {}).get("consensus")
    three_track_counts = (three_track_votes or {}).get("counts") or {}
    matchup_gap = sd_home - sd_away
    draw_trigger = 4.0 if phase == "group" else 3.2
    if tier == "coinflip":
        if three_track_consensus == "draw" and preferred == "home_win" and abs_gap >= 1.5 and len(alignment_flags) >= 1:
            return preferred, tier, alignment_flags
        if len(alignment_flags) >= 2:
            return preferred, tier, alignment_flags
        if three_track_consensus and three_track_consensus != "draw" and three_track_counts.get(three_track_consensus, 0) >= 2:
            return three_track_consensus, tier, alignment_flags
        return "draw", tier, alignment_flags
    if abs_gap < draw_trigger:
        if len(alignment_flags) >= 2 or (three_track_consensus and three_track_consensus != "draw" and three_track_counts.get(three_track_consensus, 0) >= 2):
            return preferred, tier, alignment_flags
        return "draw", tier, alignment_flags
    if tier == "slight" and len(alignment_flags) < 2:
        return "draw", tier, alignment_flags
    if phase == "group" and evidence_quality in {"thin", "suspect", "unusable"}:
        if market_status == "suspect_market" and abs(matchup_gap) >= 10 and three_track_consensus == "draw":
            return "draw", tier, alignment_flags
        if market_status == "trusted_market" and three_track_consensus == "draw" and abs(matchup_gap) <= 5:
            return "draw", tier, alignment_flags
        if market_status in {"none", "mock_invalid"} and tier == "clear":
            if abs_gap >= 10 and three_track_consensus == "draw":
                return preferred, tier, alignment_flags
            if 8.0 <= abs_gap < 10.0 and three_track_consensus == preferred and three_track_counts.get(preferred, 0) >= 2:
                return preferred, tier, alignment_flags
            if abs_gap >= 10 and three_track_consensus == preferred and three_track_counts.get(preferred, 0) >= 2:
                return "draw", tier, alignment_flags
            if abs(matchup_gap) <= 7 and abs(rs_home - rs_away) <= 16:
                return "draw", tier, alignment_flags
        if market_status == "mock_invalid" and abs(matchup_gap) <= 3 and three_track_counts.get(preferred, 0) <= 1:
            return "draw", tier, alignment_flags
    return preferred, tier, alignment_flags


def _score_pool_for_script(game_script: str, predicted_outcome: str, edge_tier: str, *, abs_delta: float = 0.0) -> list[dict]:
    pools = {
        "low-event": [(1, 0), (0, 1), (1, 1), (2, 0), (0, 2), (2, 1), (1, 2), (0, 0)],
        "medium-event": [(2, 1), (1, 2), (2, 0), (0, 2), (3, 1), (1, 3), (1, 0), (0, 1), (1, 1), (0, 0), (2, 2), (3, 0), (0, 3)],
        "open-game": [(2, 1), (1, 2), (2, 2), (3, 1), (1, 3), (3, 2), (2, 3), (3, 3), (3, 0), (0, 3)],
    }
    candidates = []
    for home, away in pools.get(game_script, pools["medium-event"]):
        outcome = "draw" if home == away else "home_win" if home > away else "away_win"
        if outcome == predicted_outcome:
            candidates.append({"home": home, "away": away})
    if predicted_outcome != "draw" and edge_tier == "strong":
        extra = [{"home": 3, "away": 0}, {"home": 0, "away": 3}, {"home": 2, "away": 0}, {"home": 0, "away": 2}]
        for candidate in extra:
            outcome = "home_win" if candidate["home"] > candidate["away"] else "away_win"
            if outcome == predicted_outcome and candidate not in candidates:
                candidates.append(candidate)
    # Blowout candidates for large mismatches (|delta| > 20)
    if abs_delta > 20 and predicted_outcome != "draw":
        blowout_pool = [(4, 0), (0, 4), (4, 1), (1, 4), (5, 1), (1, 5)]
        if abs_delta > 30:
            blowout_pool.extend([(5, 0), (0, 5), (4, 2), (2, 4)])
        for home, away in blowout_pool:
            outcome = "home_win" if home > away else "away_win"
            if outcome == predicted_outcome and {"home": home, "away": away} not in candidates:
                candidates.append({"home": home, "away": away})
    if predicted_outcome == "draw" and edge_tier in {"coinflip", "slight"} and not candidates:
        candidates.extend([{"home": 0, "away": 0}, {"home": 1, "away": 1}])
    return candidates


def _phase_bucket_key(phase: str) -> str:
    return "knockout" if _is_knockout_phase(phase) else "group"


def _scoreline_key(score: dict | None) -> str:
    score = score or {}
    home = score.get("home")
    away = score.get("away")
    if home is None or away is None:
        return ""
    return f"{int(home)}-{int(away)}"


def _prediction_body_from_match(match: dict) -> dict:
    body = match.get("prediction")
    if isinstance(body, dict) and ("score" in body or "result" in body or "predicted_outcome" in body):
        return body
    for key in ("prediction_item", "prediction_report_item"):
        item = match.get(key) or {}
        nested = item.get("prediction") if isinstance(item, dict) else {}
        if isinstance(nested, dict) and nested:
            return nested
    return {}


def _empty_scoreline_segment() -> dict:
    return {
        "sample_size": 0,
        "actual_score_counts": {},
        "predicted_score_counts": {},
        "actual_total_goals_sum": 0.0,
        "predicted_total_goals_sum": 0.0,
        "actual_clean_sheet_matches": 0,
        "predicted_clean_sheet_matches": 0,
    }


def _finalize_scoreline_segment(segment: dict) -> dict:
    sample_size = int(segment.get("sample_size", 0) or 0)
    if sample_size <= 0:
        segment["actual_score_rates"] = {}
        segment["predicted_score_rates"] = {}
        segment["avg_actual_total_goals"] = 0.0
        segment["avg_predicted_total_goals"] = 0.0
        segment["actual_clean_sheet_rate"] = 0.0
        segment["predicted_clean_sheet_rate"] = 0.0
        return segment
    segment["actual_score_rates"] = {
        key: round(value / sample_size, 4)
        for key, value in (segment.get("actual_score_counts") or {}).items()
    }
    segment["predicted_score_rates"] = {
        key: round(value / sample_size, 4)
        for key, value in (segment.get("predicted_score_counts") or {}).items()
    }
    segment["avg_actual_total_goals"] = round(float(segment.get("actual_total_goals_sum", 0.0)) / sample_size, 3)
    segment["avg_predicted_total_goals"] = round(float(segment.get("predicted_total_goals_sum", 0.0)) / sample_size, 3)
    segment["actual_clean_sheet_rate"] = round(int(segment.get("actual_clean_sheet_matches", 0) or 0) / sample_size, 4)
    segment["predicted_clean_sheet_rate"] = round(int(segment.get("predicted_clean_sheet_matches", 0) or 0) / sample_size, 4)
    return segment


def _register_scoreline_observation(segment: dict, *, predicted_score: dict, actual_score: dict) -> None:
    pred_key = _scoreline_key(predicted_score)
    actual_key = _scoreline_key(actual_score)
    if not pred_key or not actual_key:
        return
    segment["sample_size"] += 1
    segment["predicted_score_counts"][pred_key] = int(segment["predicted_score_counts"].get(pred_key, 0) or 0) + 1
    segment["actual_score_counts"][actual_key] = int(segment["actual_score_counts"].get(actual_key, 0) or 0) + 1
    segment["predicted_total_goals_sum"] += int(predicted_score["home"]) + int(predicted_score["away"])
    segment["actual_total_goals_sum"] += int(actual_score["home"]) + int(actual_score["away"])
    if predicted_score["home"] == 0 or predicted_score["away"] == 0:
        segment["predicted_clean_sheet_matches"] += 1
    if actual_score["home"] == 0 or actual_score["away"] == 0:
        segment["actual_clean_sheet_matches"] += 1


def _build_scoreline_calibration(all_matches: list[dict]) -> dict:
    calibration = {
        "global": _empty_scoreline_segment(),
        "by_phase": {},
        "by_phase_outcome": {},
    }
    for match in all_matches:
        evaluation = match.get("evaluation") or {}
        if evaluation.get("status") != "evaluated":
            continue
        prediction = _prediction_body_from_match(match)
        predicted_score = (prediction.get("knockout") or {}).get("regular_time", {}).get("score") or prediction.get("score") or {}
        actual_score = evaluation.get("actual_regular_time_score") or evaluation.get("actual_score") or {}
        actual_result = evaluation.get("actual_regular_time_result")
        if not actual_result:
            if actual_score.get("home") is None or actual_score.get("away") is None:
                continue
            actual_result = "home_win" if actual_score["home"] > actual_score["away"] else "away_win" if actual_score["away"] > actual_score["home"] else "draw"
        phase_key = _phase_bucket_key(str(match.get("phase") or "group"))
        phase_segment = calibration["by_phase"].setdefault(phase_key, _empty_scoreline_segment())
        phase_outcome_key = f"{phase_key}:{actual_result}"
        outcome_segment = calibration["by_phase_outcome"].setdefault(phase_outcome_key, _empty_scoreline_segment())
        for segment in (calibration["global"], phase_segment, outcome_segment):
            _register_scoreline_observation(segment, predicted_score=predicted_score, actual_score=actual_score)
    calibration["global"] = _finalize_scoreline_segment(calibration["global"])
    calibration["by_phase"] = {key: _finalize_scoreline_segment(segment) for key, segment in calibration["by_phase"].items()}
    calibration["by_phase_outcome"] = {
        key: _finalize_scoreline_segment(segment)
        for key, segment in calibration["by_phase_outcome"].items()
    }
    return calibration


def _select_scoreline_segment(calibration: dict | None, *, phase: str, predicted_outcome: str) -> dict:
    if not calibration:
        return {}
    phase_key = _phase_bucket_key(phase)
    phase_outcome_key = f"{phase_key}:{predicted_outcome}"
    segment = (calibration.get("by_phase_outcome") or {}).get(phase_outcome_key)
    if segment and int(segment.get("sample_size", 0) or 0) >= 6:
        return segment
    phase_segment = (calibration.get("by_phase") or {}).get(phase_key)
    if phase_segment and int(phase_segment.get("sample_size", 0) or 0) >= 8:
        return phase_segment
    return calibration.get("global") or {}


def _scoreline_history_multiplier(candidate: dict, *, phase: str, predicted_outcome: str, calibration: dict | None) -> float:
    segment = _select_scoreline_segment(calibration, phase=phase, predicted_outcome=predicted_outcome)
    sample_size = int(segment.get("sample_size", 0) or 0)
    if sample_size < 6:
        return 1.0
    factor = 1.0
    score_key = _scoreline_key(candidate)
    actual_rate = float((segment.get("actual_score_rates") or {}).get(score_key, 0.0) or 0.0)
    predicted_rate = float((segment.get("predicted_score_rates") or {}).get(score_key, 0.0) or 0.0)
    actual_clean_sheet_rate = float(segment.get("actual_clean_sheet_rate", 0.0) or 0.0)
    predicted_clean_sheet_rate = float(segment.get("predicted_clean_sheet_rate", 0.0) or 0.0)
    is_clean_sheet = candidate.get("home") == 0 or candidate.get("away") == 0
    total_goals = int(candidate.get("home", 0) or 0) + int(candidate.get("away", 0) or 0)
    avg_actual_total_goals = float(segment.get("avg_actual_total_goals", 0.0) or 0.0)

    if predicted_rate >= 0.18 and actual_rate <= max(0.05, predicted_rate * 0.60):
        factor *= SCORELINE_MODE_COLLAPSE_PENALTY
    elif actual_rate >= predicted_rate + 0.06:
        factor *= 1.08

    clean_sheet_gap = actual_clean_sheet_rate - predicted_clean_sheet_rate
    if is_clean_sheet and clean_sheet_gap > 0.10:
        factor *= min(1.18, 1.0 + clean_sheet_gap * 0.60)
    elif not is_clean_sheet and clean_sheet_gap > 0.18:
        factor *= 0.90

    if avg_actual_total_goals:
        total_gap = abs(total_goals - avg_actual_total_goals)
        if total_gap <= 0.50:
            factor *= 1.03
        elif total_gap >= 1.50:
            factor *= 0.92

    if predicted_outcome == "draw" and score_key == "0-0" and actual_rate >= 0.08:
        factor *= SCORELINE_DRAW_NIL_BIAS

    return max(0.55, min(1.35, factor))


def _scoreline_structural_multiplier(candidate: dict, *, predicted_outcome: str, clean_sheet: dict, game_script: str) -> float:
    factor = 1.0
    if predicted_outcome == "home_win":
        shutout_prob = float(clean_sheet.get("home", 0.0) or 0.0)
        if candidate.get("away") == 0:
            factor *= 1.0 + max(0.0, shutout_prob - 0.16) * SCORELINE_CLEAN_SHEET_BIAS
        elif shutout_prob >= 0.30:
            factor *= max(0.78, 1.0 - (shutout_prob - 0.26) * 0.70)
    elif predicted_outcome == "away_win":
        shutout_prob = float(clean_sheet.get("away", 0.0) or 0.0)
        if candidate.get("home") == 0:
            factor *= 1.0 + max(0.0, shutout_prob - 0.16) * SCORELINE_CLEAN_SHEET_BIAS
        elif shutout_prob >= 0.30:
            factor *= max(0.78, 1.0 - (shutout_prob - 0.26) * 0.70)
    elif candidate.get("home") == 0 and candidate.get("away") == 0 and game_script in {"low-event", "medium-event"}:
        factor *= SCORELINE_DRAW_NIL_BIAS
    return max(0.55, min(1.40, factor))


def _score_probability(candidate: dict, home_xg: float, away_xg: float, predicted_outcome: str, *, game_script: str, evidence_quality: str, abs_delta: float = 0.0) -> float:
    home_goals = int(candidate["home"])
    away_goals = int(candidate["away"])
    prob = _poisson_pmf(home_xg, home_goals) * _poisson_pmf(away_xg, away_goals)
    total = home_goals + away_goals
    score_tuple = (home_goals, away_goals)
    if predicted_outcome == "draw":
        if score_tuple == (0, 0):
            prob *= 1.18 * SCORELINE_DRAW_NIL_BIAS
        elif score_tuple == (1, 1):
            prob *= 1.12 * max(0.92, SCORELINE_PAIRED_SCORE_BIAS + 0.08)
    elif score_tuple in {(2, 1), (1, 2), (1, 1)}:
        prob *= SCORELINE_PAIRED_SCORE_BIAS
    # Skip total-based penalties for large mismatches — high-scoring lines are
    # realistic when one team massively outclasses the other.
    if abs_delta <= 20:
        if game_script == "low-event" and total >= 3:
            prob *= 0.72
        elif game_script == "medium-event" and total >= 5:
            prob *= 0.82
    if game_script == "open-game" and total <= 1:
        prob *= 0.72
    if evidence_quality in {"thin", "suspect", "unusable"} and score_tuple in {(2, 1), (1, 2), (1, 1), (3, 2), (2, 3)}:
        prob *= SCORELINE_MODE_COLLAPSE_PENALTY
    if game_script != "open-game" and score_tuple in {(2, 1), (1, 2)}:
        prob *= max(0.90, SCORELINE_MODE_COLLAPSE_PENALTY)
    if predicted_outcome != "draw" and score_tuple in {(2, 0), (0, 2), (3, 1), (1, 3)}:
        prob *= 1.06
    return prob


def _scoreline_reason(candidate: dict, base_score: dict, clean_sheet: dict, predicted_outcome: str, *, game_script: str) -> str:
    score_text = f"{candidate['home']}-{candidate['away']}"
    if candidate == base_score:
        return f"Primary football script branch settles on {score_text}."
    if max(candidate["home"], candidate["away"]) >= 4:
        return f"Dominance script: overwhelming quality gap produces a commanding {score_text} result."
    if candidate["away"] == 0 and predicted_outcome == "home_win":
        return f"Clean-sheet branch keeps {score_text} alive with home shutout chance {clean_sheet['home']:.0%}."
    if candidate["home"] == 0 and predicted_outcome == "away_win":
        return f"Clean-sheet branch keeps {score_text} alive with away shutout chance {clean_sheet['away']:.0%}."
    if game_script == "low-event":
        return "Low-event branch: control, patience, and few clear chances keep the score compact."
    if game_script == "open-game":
        return "Open-game branch: transitions, game-state pressure, and defensive gaps lift the total."
    return "Medium-event branch: one side edges it without turning into a track meet."


def _build_scoreline_distribution(
    *,
    home_final: float,
    away_final: float,
    predicted_outcome: str,
    base_score: dict,
    game_script: str,
    evidence_quality: str,
    edge_tier: str,
    phase: str = "group",
    scoreline_calibration: dict | None = None,
) -> tuple[list[dict], dict, float, float]:
    home_xg = _goal_expectation(home_final, away_final, game_script=game_script, evidence_quality=evidence_quality)
    away_xg = _goal_expectation(away_final, home_final, game_script=game_script, evidence_quality=evidence_quality)
    if predicted_outcome == "draw" and edge_tier in {"coinflip", "slight"}:
        avg_xg = round(((home_xg + away_xg) / 2.0) * (0.88 if game_script == "low-event" else 0.95), 2)
        home_xg = away_xg = avg_xg
    elif evidence_quality in {"thin", "suspect", "unusable"}:
        avg_xg = round((home_xg + away_xg) / 2.0, 2)
        home_xg = round(home_xg * 0.65 + avg_xg * 0.35, 2)
        away_xg = round(away_xg * 0.65 + avg_xg * 0.35, 2)
    # ── xG direction alignment ──
    # Ensure xG favors the predicted winner. Without this, the formula can produce
    # away_xg > home_xg when the model predicts home_win (8 out of 11 matches).
    if predicted_outcome == "home_win" and away_xg >= home_xg:
        home_xg = round(away_xg * 1.25, 2)
    elif predicted_outcome == "away_win" and home_xg >= away_xg:
        away_xg = round(home_xg * 1.25, 2)
    loser_xg_suppression = float(SCORELINE_LOSER_XG_SUPPRESSION.get(edge_tier, SCORELINE_LOSER_XG_SUPPRESSION["slight"]))
    if predicted_outcome == "home_win":
        away_xg = round(max(0.12, away_xg * loser_xg_suppression), 2)
    elif predicted_outcome == "away_win":
        home_xg = round(max(0.12, home_xg * loser_xg_suppression), 2)
    elif game_script != "open-game":
        home_xg = round(home_xg * 0.96, 2)
        away_xg = round(away_xg * 0.96, 2)
    clean_sheet = {
        "home": _clean_sheet_probability(home_final, away_final, away_xg, game_script=game_script),
        "away": _clean_sheet_probability(away_final, home_final, home_xg, game_script=game_script),
    }
    abs_delta = abs(home_final - away_final)
    candidates = _score_pool_for_script(game_script, predicted_outcome, edge_tier, abs_delta=abs_delta)
    if base_score not in candidates:
        candidates.insert(0, base_score)
    scored: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for candidate in candidates:
        key = (candidate["home"], candidate["away"])
        if key in seen:
            continue
        seen.add(key)
        base_probability = _score_probability(candidate, home_xg, away_xg, predicted_outcome, game_script=game_script, evidence_quality=evidence_quality, abs_delta=abs_delta)
        history_multiplier = _scoreline_history_multiplier(
            candidate,
            phase=phase,
            predicted_outcome=predicted_outcome,
            calibration=scoreline_calibration,
        )
        structural_multiplier = _scoreline_structural_multiplier(
            candidate,
            predicted_outcome=predicted_outcome,
            clean_sheet=clean_sheet,
            game_script=game_script,
        )
        scored.append({
            "score": candidate,
            "raw_probability": base_probability * history_multiplier * structural_multiplier,
        })
    scored.sort(key=lambda item: item["raw_probability"], reverse=True)
    # Allow more scoreline slots for large mismatches (blowout candidates need room)
    top_n = 5 if abs_delta > 20 else 4
    top = scored[:top_n]
    total = sum(item["raw_probability"] for item in top) or 1.0
    distribution = []
    for item in top:
        candidate = item["score"]
        distribution.append({
            "score": candidate,
            "probability": round(item["raw_probability"] / total, 3),
            "reason": _scoreline_reason(candidate, base_score, clean_sheet, predicted_outcome, game_script=game_script),
        })
    return distribution, clean_sheet, home_xg, away_xg


def _estimate_scoreline(home_final: float, away_final: float, predicted_outcome: str, *, game_script: str = "medium-event", evidence_quality: str = "trusted", edge_tier: str = "slight", phase: str = "group", scoreline_calibration: dict | None = None) -> dict:
    if predicted_outcome == "home_win":
        if game_script == "low-event":
            base_score = {"home": 1, "away": 0}
        elif edge_tier in {"clear", "strong"}:
            base_score = {"home": 2, "away": 0}
        else:
            base_score = {"home": 2, "away": 1}
    elif predicted_outcome == "away_win":
        if game_script == "low-event":
            base_score = {"home": 0, "away": 1}
        elif edge_tier in {"clear", "strong"}:
            base_score = {"home": 0, "away": 2}
        else:
            base_score = {"home": 1, "away": 2}
    else:
        base_score = {"home": 1, "away": 1} if game_script == "low-event" else {"home": 2, "away": 2}
    distribution, _, _, _ = _build_scoreline_distribution(
        home_final=home_final,
        away_final=away_final,
        predicted_outcome=predicted_outcome,
        base_score=base_score,
        game_script=game_script,
        evidence_quality=evidence_quality,
        edge_tier=edge_tier,
        phase=phase,
        scoreline_calibration=scoreline_calibration,
    )
    return dict(distribution[0]["score"]) if distribution else {"home": 1, "away": 1}


def _split_confidence_fields(
    *,
    result_confidence: str,
    scoreline_distribution: list[dict],
    clean_sheet_probability: dict,
    evidence_gaps: list[str],
) -> dict:
    top_probability = float(scoreline_distribution[0]["probability"]) if scoreline_distribution else 0.0
    clean_gap = abs(float(clean_sheet_probability.get("home", 0)) - float(clean_sheet_probability.get("away", 0)))
    score_confidence = "medium" if top_probability >= 0.42 and not evidence_gaps else "low"
    if result_confidence == "high" and clean_gap >= 0.16 and not evidence_gaps:
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
            title="天纪比分分布",
            verdict=ctx["confidence_split"]["score_confidence"],
            confidence=ctx["confidence_split"]["score_confidence"],
            summary=(
                "比分由天纪卦象推演，非数学概率。各分支代表不同运势走向。"
                f"天命主线: {', '.join(top_scores) if top_scores else 'unavailable'}。"
            ),
            key_drivers=[
                f"天纪进球气场: {ctx.get('home_expected_goals')}-{ctx.get('away_expected_goals')}",
                f"零封概率 主/客: {float(clean_sheet.get('home', 0)):.0%}/{float(clean_sheet.get('away', 0)):.0%}",
            ],
            counter_signals=[
                "红牌、点球、定位球和追分状态可能使命运分支快速切换。"
            ],
            missing_context=[gap for gap in ctx.get("evidence_gaps", []) if not str(gap).endswith("_resolved")],
            watch_triggers=["确认首发阵容", "定位球错位", "红牌或早进球", "下半场换人状态"],
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
    history_index: dict[str, dict] | None = None,
    lessons: list[dict] | None = None,
    scoreline_calibration: dict | None = None,
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
    home_history = _lookup_team(home_id, history_index or {})
    away_history = _lookup_team(away_id, history_index or {})

    kickoff = parse_datetime(str(match.get("kickoff_at", "")))
    kickoff_dt = kickoff or datetime.now(timezone.utc)

    # --- Component scores (each 0-100 except evidence which is -15..+15) ---
    rs_home = score_ranking_strength(home_ranking)
    rs_away = score_ranking_strength(away_ranking)

    sd_home = score_squad_depth(home_squad, global_summary)
    sd_away = score_squad_depth(away_squad, global_summary)

    hp_home = score_historical_proxy(home_ranking, home_history)
    hp_away = score_historical_proxy(away_ranking, away_history)

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

    # --- Opener detection (小组赛首轮) ---
    # Check if both teams have no prior completed matches in this tournament
    is_opener = False
    _home_prior = _count_prior_matches(home_id, all_matches, kickoff_dt)
    _away_prior = _count_prior_matches(away_id, all_matches, kickoff_dt)
    if _home_prior == 0 and _away_prior == 0:
        is_opener = True

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
    market_snapshot = _classify_market_odds(odds)
    odds_is_usable = market_snapshot["status"] == "trusted_market"

    # 1. Referee Rigor Modifier
    referee_home_mod = 0.0
    referee_away_mod = 0.0
    yellow_cards_pred = 3.5
    red_cards_pred = 0.1
    penalties_pred = 0.2

    if referee:
        strictness = referee.get("strictness", "medium")
        if strictness == "high":
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

    home_news_sentiment = max(-3.0, min(3.0, home_news_sentiment))
    away_news_sentiment = max(-3.0, min(3.0, away_news_sentiment))

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

    divination = compute_tianji_overlay(
        match.get("kickoff_at", ""),
        match.get("match_id", ""),
        venue=str(match.get("venue", "")),
    )
    hexagram_overlay = compute_divination_overlay(date, match.get("match_id", ""))
    divination["hexagram_number"] = hexagram_overlay["hexagram_number"]
    divination["hexagram_name"] = hexagram_overlay["hexagram_name"]
    divination["hexagram"] = hexagram_overlay["hexagram_name"]
    divination["hexagram_interpretation"] = hexagram_overlay["interpretation"]
    divination["hexagram_home_modifier"] = hexagram_overlay["home_modifier"]
    divination["hexagram_away_modifier"] = hexagram_overlay["away_modifier"]

    match_id = match.get("match_id", "")
    stage_data_weight, stage_div_weight = _realistic_stage_weights(match_id)
    divination["weight"] = stage_div_weight
    divination["data_weight"] = stage_data_weight

    tianji_home_score = _tianji_score(data_home, float(divination["home_modifier"]))
    tianji_away_score = _tianji_score(data_away, float(divination["away_modifier"]))
    home_final = round((data_home * stage_data_weight) + (tianji_home_score * stage_div_weight), 1)
    away_final = round((data_away * stage_data_weight) + (tianji_away_score * stage_div_weight), 1)

    local_gaps = []
    if not home_squad or not away_squad:
        local_gaps.append("rosters_missing")
    if not referee:
        local_gaps.append("referee_missing")
    if market_snapshot["status"] != "trusted_market":
        local_gaps.append("odds_missing")

    evidence_gaps = _collect_evidence_gaps(evidence_index)
    evidence_quality = _overall_evidence_quality(
        market_status=market_snapshot["status"],
        local_gaps=local_gaps,
        evidence_gaps=evidence_gaps,
    )
    three_track_votes = _three_track_vote_summary(
        gap=home_final - away_final,
        market_outcome=market_snapshot["market_outcome"],
        market_status=market_snapshot["status"],
        home_news_sentiment=home_news_sentiment,
        away_news_sentiment=away_news_sentiment,
        rs_home=rs_home,
        rs_away=rs_away,
        sd_home=sd_home,
        sd_away=sd_away,
        hex_home_mod=float(divination.get("hexagram_home_modifier", 0)),
        hex_away_mod=float(divination.get("hexagram_away_modifier", 0)),
    )
    predicted_outcome, edge_tier, alignment_flags = _determine_outcome_from_context(
        home_final=home_final,
        away_final=away_final,
        phase=match.get("phase", "group"),
        market_status=market_snapshot["status"],
        market_outcome=market_snapshot["market_outcome"],
        home_news_sentiment=home_news_sentiment,
        away_news_sentiment=away_news_sentiment,
        rs_home=rs_home,
        rs_away=rs_away,
        sd_home=sd_home,
        sd_away=sd_away,
        evidence_quality=evidence_quality,
        three_track_votes=three_track_votes,
    )
    game_script = _pick_game_script(
        predicted_outcome=predicted_outcome,
        edge_tier=edge_tier,
        evidence_quality=evidence_quality,
        phase=match.get("phase", "group"),
        is_opener=is_opener,
        implied_probs=market_snapshot["implied_probabilities"],
        home_final=home_final,
        away_final=away_final,
        news_swing=home_news_sentiment - away_news_sentiment,
    )
    predicted_score = _estimate_scoreline(
        home_final,
        away_final,
        predicted_outcome,
        game_script=game_script,
        evidence_quality=evidence_quality,
        edge_tier=edge_tier,
        phase=match.get("phase", "group"),
        scoreline_calibration=scoreline_calibration,
    )
    scoreline_distribution, clean_sheet_probability, home_expected_goals, away_expected_goals = _build_scoreline_distribution(
        home_final=home_final,
        away_final=away_final,
        predicted_outcome=predicted_outcome,
        base_score=predicted_score,
        game_script=game_script,
        evidence_quality=evidence_quality,
        edge_tier=edge_tier,
        phase=match.get("phase", "group"),
        scoreline_calibration=scoreline_calibration,
    )
    if scoreline_distribution:
        predicted_score = dict(scoreline_distribution[0]["score"])
    total_goals = int(predicted_score["home"]) + int(predicted_score["away"])
    goals_line_2_5 = "over" if total_goals >= 3 else "under"
    avg_data = (data_home + data_away) / 2.0
    if evidence_quality == "trusted" and avg_data > 65.0 and edge_tier in {"clear", "strong"}:
        confidence = "high"
        confidence_label = "楂樹俊蹇?"
    elif avg_data >= 50.0 and evidence_quality not in {"unusable"}:
        confidence = "medium"
        confidence_label = "涓瓑淇″績"
    else:
        confidence = "low"
        confidence_label = "浣庝俊蹇?"
    knockout_prediction = _build_knockout_prediction(
        phase=match.get("phase", "group"),
        predicted_outcome=predicted_outcome,
        predicted_score=predicted_score,
        home_name=home_name,
        away_name=away_name,
        home_final=home_final,
        away_final=away_final,
        edge_tier=edge_tier,
        game_script=game_script,
        confidence=confidence,
    )

    divination["combined_home_fortune"] = hexagram_overlay.get("home_fortune", "?")
    divination["combined_away_fortune"] = hexagram_overlay.get("away_fortune", "?")
    divination["hex_pattern"] = game_script

    implied_probs = market_snapshot["implied_probabilities"]
    market_outcome = market_snapshot["market_outcome"]
    dual_track_alignment = "aligned" if market_outcome and market_outcome == predicted_outcome else "divergent" if market_outcome else "untracked"
    divergence_analysis = ""
    if market_outcome and dual_track_alignment == "aligned":
        divergence_analysis = "【双轨共振】基本面与可信市场方向一致。"
    elif market_outcome:
        divergence_analysis = "【双轨背离】基本面与市场方向不一致，本场应降档处理。"

    avg_data = (data_home + data_away) / 2.0
    if evidence_quality == "trusted" and avg_data > 65.0 and edge_tier in {"clear", "strong"}:
        confidence = "high"
        confidence_label = "高信心"
    elif avg_data >= 50.0 and evidence_quality not in {"unusable"}:
        confidence = "medium"
        confidence_label = "中等信心"
    else:
        confidence = "low"
        confidence_label = "低信心"
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
        "is_opener": is_opener,
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
        "odds": odds if market_snapshot["status"] == "trusted_market" else None,
        "raw_odds": odds,
        "odds_source_status": market_snapshot["status"],
        "implied_probs": implied_probs,
        "market_outcome": market_outcome,
        "dual_track_alignment": dual_track_alignment,
        "divergence_analysis": divergence_analysis,
        "evidence_quality": evidence_quality,
        "edge_tier": edge_tier,
        "alignment_flags": alignment_flags,
        "game_script": game_script,
        "three_track_votes": three_track_votes,
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
        hexagram_name=divination["hexagram_name"],
        data_weight=stage_data_weight,
        divination_weight=stage_div_weight,
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

    # --- Experience Loop: apply lessons from past evaluations ---
    lessons_applied: list[dict] = []
    if lessons:
        conf_to_num = {"high": 0.75, "medium": 0.60, "low": 0.45}
        num_to_conf = [(0.70, "high"), (0.50, "medium"), (0.0, "low")]
        total_adj = 0.0
        for lesson in lessons:
            adj = float(lesson.get("confidence_adjustment", 0.0))
            lessons_applied.append({
                "lesson_id": lesson.get("lesson_id", ""),
                "lesson_type": lesson.get("lesson_type", ""),
                "summary": lesson.get("summary", ""),
                "confidence_adjustment": adj,
            })
            total_adj += adj
        if total_adj != 0.0:
            base_num = conf_to_num.get(confidence, 0.60)
            adjusted_num = max(0.0, min(1.0, base_num + total_adj))
            for threshold, level in num_to_conf:
                if adjusted_num >= threshold:
                    confidence = level
                    break
            confidence_label_map = {"high": "高信心", "medium": "中等信心", "low": "低信心"}
            confidence_label = confidence_label_map.get(confidence, "中等信心")

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
            "score": predicted_score,
            "is_opener": is_opener,
            "total_goals": total_goals,
            "goals_line_2_5": goals_line_2_5,
            "confidence": confidence,
            "confidence_label": confidence_label,
            "result_confidence": confidence_split["result_confidence"],
            "score_confidence": confidence_split["score_confidence"],
            "total_goals_confidence": confidence_split["total_goals_confidence"],
            "confidence_note": confidence_split["confidence_note"],
            "evidence_quality": evidence_quality,
            "edge_tier": edge_tier,
            "game_script": game_script,
            "three_track_votes": three_track_votes,
            "scoreline_distribution": scoreline_distribution,
            "clean_sheet_probability": clean_sheet_probability,
            "expected_goals_proxy": {
                "home": home_expected_goals,
                "away": away_expected_goals,
            },
            "venue_adaptation_context": venue_adaptation_context,
            "evidence_gaps": evidence_gaps,
            "knockout": knockout_prediction,
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
        } if market_snapshot["status"] == "trusted_market" else None,
        "market_odds_status": {
            "status": market_snapshot["status"],
            "source": (odds or {}).get("source", "missing"),
            "is_mock": market_snapshot["is_mock"],
            "reason": market_snapshot["reason"],
        },
        "dual_track": {
            "alignment": dual_track_alignment,
            "divergence_analysis": divergence_analysis
        } if market_outcome else None,
        "analysis_layers": analysis_layers,
        "scenario_analysis": scenario_analysis,
        "decision_audit": decision_audit,
        "analysis_summary": {
            "layer_count": len(analysis_layers),
            "risk_level": decision_audit.get("risk_level"),
            "primary_edge": _edge_verdict(home_final - away_final),
            "edge_tier": edge_tier,
            "game_script": game_script,
            "evidence_quality": evidence_quality,
            "three_track_consensus": three_track_votes.get("consensus"),
            "storage_note": "JSON report remains the audit artifact; SQLite is an optional query/index layer.",
        },
        "play_card": play_card,
        "disclaimer": DISCLAIMER,
        "lessons_applied": lessons_applied if lessons_applied else None,
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
    force: bool = False,
) -> dict:
    """Run the prediction scoring model for all matches on *date*.

    Past-date protection: if the date is in the past AND a prediction report
    already exists, the run is skipped unless --force is used.
    """
    load_hyperparameters(root, edition)
    generated_at = iso_now(now)
    now_dt = now_datetime(now)
    ed_root = edition_data_root(root, edition)

    # --- Past-date protection (比赛已过的日期保护) ---
    if not dry_run and not force and date and _is_past_date(date):
        existing_path = ed_root / "daily-predictions" / f"{date}.json"
        if existing_path.exists():
            try:
                existing = load_json(existing_path, {})
                if existing.get("predictions"):
                    existing["_warning"] = (
                        f"日期 {date} 已过，预测结果已锁定。"
                        f"如需重新生成，请使用 --force。"
                    )
                    print(f"[SKIP] {date} 已存在预测结果，跳过（使用 --force 强制重新生成）", file=sys.stderr)
                    return existing
            except Exception:
                pass

    # --- Load data sources ---
    ledger = load_match_ledger(root, edition)
    rankings_data = load_json(raw_edition_root(root, edition) / "rankings/fifa-men-ranking.json", {"rankings": []})
    squad_data = load_edition_data_json(root, edition, "squad-depth-features.json", {"teams": [], "global_summary": {}})
    evidence_plan = load_json(ed_root / "prediction-evidence-plan.json", {"items": []})

    # Reconcile evidence statuses with actual files on disk
    evidence_plan = _reconcile_evidence_from_disk(evidence_plan, ed_root)

    ranking_index = _build_ranking_index(rankings_data)
    squad_index = _build_squad_index(squad_data)
    evidence_index = _build_evidence_index(evidence_plan)
    history_index = _build_history_index(root, edition)
    global_summary = squad_data.get("global_summary")
    all_matches = canonical_matches(ledger.get("matches", []) or [])
    scoreline_calibration = _build_scoreline_calibration(all_matches)

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
            history_index=history_index,
            scoreline_calibration=scoreline_calibration,
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
        "run_type": "experiment",
        "filters": {
            "match_id": match_id or "",
            "teams": teams or [],
        },
        "model_weights": {
            "note": "阶段自适应: 小组赛天纪65%/数据35%, R32天纪55%/数据45%, R16数据55%/天纪45%, QF+数据70%+/天纪30%-",
            "stage_weight_table": {k: {"data_weight": v[0], "divination_weight": v[1]} for k, v in _STAGE_WEIGHT_TABLE.items()},
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
        out_path = ed_root / "reports" / "backtests" / f"{report_date}{suffix}-prediction-report.json"
        write_json(out_path, report)

        # --- Sync predictions into SQLite DB ---
        try:
            from worldcup_db import (
                get_db_connection,
                init_database,
                save_match,
                save_prediction,
                save_prediction_analysis_layers,
            )
            from worldcup_core import worldcup_db_path

            db_path = worldcup_db_path(root, edition)
            init_database(db_path)
            conn = get_db_connection(db_path)
            try:
                with conn:
                    for p in predictions:
                        p["report_json_path"] = str(out_path)
                        p["generated_at"] = generated_at
                        p["prediction_date"] = report_date
                        p["run_type"] = "experiment"
                        p["run_id"] = f"scoring-model::{report_date}::{generated_at}"
                        p["experiment_id"] = f"{p.get('match_id', '')}::{generated_at}"
                        matched = [m for m in all_matches if m.get("match_id") == p.get("match_id")]
                        if matched:
                            save_match(conn, matched[0])
                        save_prediction(conn, p)
            finally:
                conn.close()
        except Exception:
            import traceback
            traceback.print_exc()

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
    predict.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration even for past dates with existing predictions",
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
            force=args.force,
        )
        # Handle Windows GBK encoding for Chinese output
        try:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except UnicodeEncodeError:
            print(json.dumps(result, ensure_ascii=True, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
