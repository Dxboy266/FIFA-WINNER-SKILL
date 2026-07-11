#!/usr/bin/env python3
"""Render prediction reports and agent actions as a static visual dashboard."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import http.server
import webbrowser

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tianji_oracle import compute_tianji_overlay  # noqa: E402
from prediction_scoring_model import compute_divination_overlay, _generate_match_hexagram_interpretation  # noqa: E402
from worldcup_core import apply_prediction_items_to_matches, raw_edition_root, DISCLAIMER, canonical_matches, edition_data_root, is_canonical_match, iso_now, load_edition_data_json, load_json, load_match_ledger, materialize_actual_knockout_fixtures, person_edition_root, public_edition_data_root, wiki_edition_root, worldcup_db_path, write_json, write_text  # noqa: E402


OUTCOME_LABELS = {"home_win": "主胜", "away_win": "客胜", "draw": "平局"}

KNOCKOUT_PHASES = {
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "third_place",
    "final",
}

PHASE_LABELS = {
    "all": "总数据",
    "group": "小组赛",
    "round_of_32": "1/16决赛",
    "round_of_16": "1/8决赛",
    "quarter_final": "1/4决赛",
    "semi_final": "半决赛",
    "third_place": "三四名",
    "final": "决赛",
}

STAT_PHASE_LABELS = {
    **PHASE_LABELS,
    "group": "小组赛汇总",
}

FACT_PHASE_ORDER = (
    "group",
    "round_of_32",
    "round_of_16",
    "quarter_final",
)

TEAM_ZH = {
    "mex": "墨西哥", "rsa": "南非", "kor": "韩国", "cze": "捷克",
    "can": "加拿大", "bih": "波黑", "qat": "卡塔尔", "sui": "瑞士",
    "bra": "巴西", "mar": "摩洛哥", "hai": "海地", "sco": "苏格兰",
    "usa": "美国", "par": "巴拉圭", "aus": "澳大利亚", "tur": "土耳其",
    "ger": "德国", "cuw": "库拉索", "civ": "科特迪瓦", "ecu": "厄瓜多尔",
    "ned": "荷兰", "jpn": "日本", "swe": "瑞典", "tun": "突尼斯",
    "bel": "比利时", "egy": "埃及", "irn": "伊朗", "nzl": "新西兰",
    "esp": "西班牙", "cpv": "佛得角", "ksa": "沙特", "uru": "乌拉圭",
    "fra": "法国", "sen": "塞内加尔", "irq": "伊拉克", "nor": "挪威",
    "arg": "阿根廷", "alg": "阿尔及利亚", "aut": "奥地利", "jor": "约旦",
    "por": "葡萄牙", "cod": "刚果（金）", "uzb": "乌兹别克斯坦", "col": "哥伦比亚",
    "eng": "英格兰", "cro": "克罗地亚", "gha": "加纳", "pan": "巴拿马",
}


def _zh_name_from_label(name: str, team_id: str = "") -> str:
    team_key = str(team_id or "").lower()
    if team_key and team_key in TEAM_ZH:
        return TEAM_ZH[team_key]
    value = str(name or "")
    if not value:
        return value
    for zh in TEAM_ZH.values():
        if value == zh:
            return value
    for code, zh in TEAM_ZH.items():
        if value.lower() == code:
            return zh
    return value


def _is_placeholder_team_name(name: str) -> bool:
    value = str(name or "").strip().upper()
    return bool(re.match(r"^[WL]\d{2,3}$", value))


def _dashboard_paths(root: Path, edition: str) -> tuple[Path, Path]:
    data_path = edition_data_root(root, edition) / "reports" / "dashboard" / "prediction-dashboard.json"
    html_path = wiki_edition_root(root, edition) / "dashboard" / "index.html"
    return data_path, html_path


def _dashboard_static_data_path(root: Path, edition: str) -> Path:
    return wiki_edition_root(root, edition) / "dashboard" / "prediction-dashboard.json"


def _display_path(path: Path | str, root: Path) -> str:
    candidate = Path(path)
    try:
        candidate = candidate.resolve()
        return candidate.relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _fortune_css_class(fortune: str) -> str:
    """Return CSS class for fortune level display."""
    fortune_map = {
        "大吉": "fortune-great",
        "吉": "fortune-good",
        "小吉": "fortune-small-good",
        "平": "fortune-neutral",
        "小凶": "fortune-small-bad",
        "凶": "fortune-bad",
        "大凶": "fortune-terrible",
    }
    return fortune_map.get(fortune, "fortune-neutral")


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _rate(hits: int, total: int) -> float:
    return hits / total if total else 0.0


def _prediction_files(root: Path, edition: str) -> list[Path]:
    reports = edition_data_root(root, edition) / "reports" / "daily-predictions"
    if not reports.exists():
        return []
    return sorted(reports.glob("*.json"))


def _prediction_sources(root: Path, edition: str, *, include_local: bool = True) -> list[tuple[Path, str, int]]:
    sources: list[tuple[Path, str, int]] = []
    public_reports_candidates = [
        public_edition_data_root(root, edition) / "daily-predictions",
        public_edition_data_root(root, edition) / "default-predictions" / "daily-predictions",
    ]
    local_reports = edition_data_root(root, edition) / "reports" / "daily-predictions"
    person_reports = person_edition_root(root, edition) / "reports" / "daily-predictions"
    seen_paths: set[Path] = set()
    for public_reports in public_reports_candidates:
        if public_reports.exists():
            for path in sorted(public_reports.glob("*.json")):
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                sources.append((path, "octopus_default", 10))
    if include_local and local_reports.exists():
        sources.extend((path, "user_local", 100) for path in sorted(local_reports.glob("*.json")))
    if include_local and person_reports.exists():
        sources.extend((path, "person_local", 200) for path in sorted(person_reports.glob("*.json")))
    return sources


def _prediction_items_by_match(root: Path, edition: str, *, include_local: bool = True) -> dict[str, dict]:
    selected: dict[str, dict] = {}
    for path, origin, priority in _prediction_sources(root, edition, include_local=include_local):
        report = load_json(path, {})
        for item in report.get("predictions", []) or []:
            match_id = str(item.get("match_id", ""))
            if not match_id or not is_canonical_match(match_id):
                continue
            current = selected.get(match_id)
            if current and current["priority"] > priority:
                continue
            enriched = dict(item)
            enriched["prediction_origin"] = origin
            enriched["prediction_source"] = origin
            enriched["prediction_source_path"] = _display_path(path, root)
            selected[match_id] = {"priority": priority, "item": enriched}
    return {match_id: value["item"] for match_id, value in selected.items()}


def _prediction_team_field(team: object, key: str, default: object = None) -> object:
    if isinstance(team, dict):
        return team.get(key, default)
    return default


def _prediction_team_id(team: object) -> str:
    return str(_prediction_team_field(team, "team_id", "") or "").lower()


def _evaluation_index(root: Path, edition: str) -> dict[str, dict]:
    eval_dir = edition_data_root(root, edition) / "reports" / "evaluations"
    result: dict[str, dict] = {}
    if not eval_dir.exists():
        return result
    for path in sorted(eval_dir.glob("*.json")):
        if path.name == "aggregate-dashboard.json" or path.name.endswith("-dashboard.json"):
            continue
        payload = load_json(path, {})
        for item in payload.get("evaluations", []) or []:
            match_id = str(item.get("match_id", ""))
            if match_id:
                result[match_id] = item
    return result


def _load_aggregate(root: Path, edition: str) -> dict:
    path = edition_data_root(root, edition) / "reports" / "evaluations" / "aggregate-dashboard.json"
    return load_json(path, {}) if path.exists() else {}


def _poster_assets(root: Path, html_path: Path) -> list[str]:
    asset_dir = root / "assets" / "posters"
    if not asset_dir.exists():
        return []
    paths = sorted(list(asset_dir.glob("*.png")) + list(asset_dir.glob("*.jpg")) + list(asset_dir.glob("*.jpeg")))
    rels = []
    for path in paths[:4]:
        rels.append(os.path.relpath(path, html_path.parent).replace(os.sep, "/"))
    return rels


def _normalize_market_odds_status(market_odds: dict | None, explicit_status: dict | None = None) -> tuple[bool, dict | None, dict]:
    odds = (market_odds or {}).get("odds") if isinstance(market_odds, dict) else None
    source = str((odds or {}).get("source") or (explicit_status or {}).get("source") or "missing")
    is_mock = bool((odds or {}).get("is_mock") or source == "mock_bookmaker" or (explicit_status or {}).get("is_mock"))
    usable = bool(
        market_odds
        and odds
        and not is_mock
        and source not in {"missing", "odds_unavailable"}
        and all((odds or {}).get(key) for key in ("home_win", "draw", "away_win"))
    )
    if usable:
        status = {"status": "usable", "source": source, "is_mock": False, "reason": ""}
        return True, market_odds, status
    reason = str((explicit_status or {}).get("reason") or (odds or {}).get("reason") or "")
    if is_mock:
        status = {
            "status": "mock_unusable",
            "source": source,
            "is_mock": True,
            "reason": "mock odds are not valid market evidence",
        }
    elif source == "odds_unavailable":
        status = {"status": "unavailable", "source": source, "is_mock": False, "reason": reason}
    else:
        status = {"status": "missing", "source": source, "is_mock": False, "reason": reason}
    return False, None, status


def _as_prediction_card(item: dict, *, evaluation: dict | None) -> dict:
    home_raw = item.get("home_team", {}) or {}
    away_raw = item.get("away_team", {}) or {}
    home = home_raw if isinstance(home_raw, dict) else {"name": str(home_raw)}
    away = away_raw if isinstance(away_raw, dict) else {"name": str(away_raw)}
    home_name = str(home.get("name") or home.get("team_id") or "Home")
    away_name = str(away.get("name") or away.get("team_id") or "Away")

    prediction = item.get("prediction", {}) or {}
    score = prediction.get("score", {}) or {}
    divination = item.get("divination_overlay", {}) or {}

    if not divination.get("local_kickoff_at"):
        divination = compute_tianji_overlay(
            str(item.get("kickoff_at", "")),
            str(item.get("match_id", "")),
            venue=str(item.get("venue", "")),
        )
        # Also compute I Ching hexagram overlay if missing (with match context)
        if not divination.get("hexagram_name") or divination.get("hexagram_number", 0) == 0:
            _date_str = str(item.get("kickoff_at", ""))[:10]
            # Prefer Chinese team names for divination display consistency
            _home = item.get("home_name_zh") or item.get("home_name", "") or ""
            _away = item.get("away_name_zh") or item.get("away_name", "") or ""
            _hex_overlay = compute_divination_overlay(_date_str, str(item.get("match_id", "")),
                                                      home_name=_home, away_name=_away)
            divination["hexagram_number"] = _hex_overlay["hexagram_number"]
            divination["hexagram_name"] = _hex_overlay["hexagram_name"]
            divination["hexagram"] = _hex_overlay["hexagram_name"]
            divination["hexagram_interpretation"] = _hex_overlay["interpretation"]
            divination["hexagram_home_modifier"] = _hex_overlay["home_modifier"]
            divination["hexagram_away_modifier"] = _hex_overlay["away_modifier"]
            # New: match-specific interpretation fields
            divination["match_interpretation"] = _hex_overlay.get("match_interpretation", "")
            divination["home_fortune"] = _hex_overlay.get("home_fortune", "")
            divination["away_fortune"] = _hex_overlay.get("away_fortune", "")
            divination["fortune_summary"] = _hex_overlay.get("fortune_summary", "")
    local_date = str(divination.get("local_kickoff_at", ""))[:10] or str(item.get("kickoff_at", ""))[:10]

    result = str(prediction.get("result") or prediction.get("predicted_outcome") or "")
    knockout_prediction = _normalize_knockout_payload(
        prediction.get("knockout"),
        home_name=home_name,
        away_name=away_name,
    )
    evaluated = evaluation or {}
    eval_status = str(evaluated.get("status") or "pending_final_score")
    result_hit = evaluated.get("result_hit")
    score_hit = evaluated.get("score_hit")

    if result_hit is True and score_hit is True:
        hit_class = "double-hit"
        eval_label = "完美双中"
    elif result_hit is True:
        hit_class = "result-hit"
        eval_label = "仅中赛果"
    elif result_hit is False:
        hit_class = "miss"
        eval_label = "预测偏差"
    else:
        hit_class = "pending"
        eval_label = "待开赛"

    # Analyze presence of key evidence features
    evidence_gaps = prediction.get("evidence_gaps") or []
    has_odds, market_odds, market_odds_status = _normalize_market_odds_status(
        item.get("market_odds"),
        item.get("market_odds_status"),
    )
    has_referee = 1 if item.get("referee_analysis") else 0
    has_news = 1 if (item.get("daily_evidence") or item.get("late_news")) else 0

    play_card = item.get("play_card", {}) or {}

    return {
        "match_id": str(item.get("match_id", "")),
        "prediction_origin": item.get("prediction_origin", "user_local"),
        "prediction_source": item.get("prediction_source", item.get("prediction_origin", "user_local")),
        "prediction_source_path": item.get("prediction_source_path", ""),
        "data_origin": item.get("prediction_origin", "user_local"),
        "date": local_date,
        "kickoff_at": item.get("kickoff_at", ""),
        "local_kickoff_at": divination.get("local_kickoff_at", "") or item.get("kickoff_at", ""),
        "calculation_timezone": divination.get("calculation_timezone", "") or "LocalTime",
        "venue": item.get("venue", ""),
        "group": item.get("group", ""),
        "phase": item.get("phase", ""),
        "home_name": home_name,
        "away_name": away_name,
        "predicted_result": result,
        "predicted_result_label": OUTCOME_LABELS.get(result, result or "Unknown"),
        "score_text": f"{score.get('home', '-')}-{score.get('away', '-')}",
        "total_goals": prediction.get("total_goals", "-"),
        "confidence": prediction.get("confidence", "unknown"),
        "confidence_label": prediction.get("confidence", "unknown").upper(),
        "knockout_prediction": knockout_prediction,

        # Enriched model fields
        "expected_goals_proxy": prediction.get("expected_goals_proxy"),
        "clean_sheet_probability": prediction.get("clean_sheet_probability"),
        "scoreline_distribution": prediction.get("scoreline_distribution"),
        "result_confidence": prediction.get("result_confidence") or prediction.get("confidence", "unknown"),
        "score_confidence": prediction.get("score_confidence") or "unknown",
        "total_goals_confidence": prediction.get("total_goals_confidence") or "unknown",
        "confidence_note": prediction.get("confidence_note") or "",
        "venue_adaptation_context": item.get("venue_adaptation_context"),
        "referee_analysis": item.get("referee_analysis"),
        "play_card": play_card,

        "divination_hexagram": divination.get("hexagram", ""),
        "evaluation_status": eval_status,
        "evaluation_label": eval_label,
        "hit_class": hit_class,
        "result_hit": result_hit,
        "score_hit": score_hit,
        "home_colors": "",
        "away_colors": "",
        "home_ranking": None,
        "away_ranking": None,
        "evidence_gaps": evidence_gaps,
        "play_title": play_card.get("share_title", ""),
        "risk_flags": play_card.get("risk_flags", []) or [],
        "watch_points": play_card.get("watch_points", []) or [],
        "primary_error": evaluated.get("primary_error") or "",
        "has_odds": has_odds,
        "market_odds": market_odds,
        "market_odds_status": market_odds_status,
        "market_odds_source": market_odds_status.get("source", "missing"),
        "market_odds_is_mock": bool(market_odds_status.get("is_mock")),
        "has_referee": has_referee,
        "has_news": has_news,
        "analysis_layers": item.get("analysis_layers", []) or [],
        "home_radar": {"attack": 70, "defense": 70, "midfield": 70, "fitness": 70, "recent_form": 70},
        "away_radar": {"attack": 70, "defense": 70, "midfield": 70, "fitness": 70, "recent_form": 70},
        "home_form": [],
        "away_form": [],
        "h2h": [],
        "home_players": [],
        "away_players": [],
        "home_injuries": [],
        "away_injuries": [],
        "home_suspensions": [],
        "away_suspensions": [],
        "late_news": [],
    }


def _merge_prediction_item_into_card(card: dict, item: dict, *, root: Path) -> bool:
    prediction = item.get("prediction", {}) or {}
    score = prediction.get("score", {}) or {}
    result = str(prediction.get("result") or prediction.get("predicted_outcome") or "")
    if not result and (score.get("home") is None or score.get("away") is None):
        return False

    home = item.get("home_team", {}) or {}
    away = item.get("away_team", {}) or {}
    play_card = item.get("play_card", {}) or {}
    has_market_odds, market_odds, market_odds_status = _normalize_market_odds_status(
        item.get("market_odds"),
        item.get("market_odds_status"),
    )
    knockout_prediction = _normalize_knockout_payload(
        prediction.get("knockout"),
        home_name=str((home or {}).get("name") or card.get("home_name") or ""),
        away_name=str((away or {}).get("name") or card.get("away_name") or ""),
    )

    card.update({
        "prediction_origin": item.get("prediction_origin", "user_local"),
        "prediction_source": item.get("prediction_source", item.get("prediction_origin", "user_local")),
        "prediction_source_path": item.get("prediction_source_path", ""),
        "data_origin": item.get("prediction_origin", "user_local"),
        "prediction_status": "locked_pre_match_prediction",
        "predicted_result": result,
        "predicted_result_label": OUTCOME_LABELS.get(result, result),
        "score_text": f"{score.get('home', '-')}-{score.get('away', '-')}",
        "knockout_prediction": knockout_prediction,
        "total_goals": prediction.get("total_goals", "-"),
        "confidence": prediction.get("confidence", "unknown"),
        "confidence_label": str(prediction.get("confidence", "unknown")).upper(),
        "expected_goals_proxy": prediction.get("expected_goals_proxy"),
        "clean_sheet_probability": prediction.get("clean_sheet_probability"),
        "scoreline_distribution": prediction.get("scoreline_distribution"),
        "result_confidence": prediction.get("result_confidence", prediction.get("confidence", "unknown")),
        "score_confidence": prediction.get("score_confidence", "unknown"),
        "total_goals_confidence": prediction.get("total_goals_confidence", "unknown"),
        "confidence_note": prediction.get("confidence_note", ""),
        "venue_adaptation_context": item.get("venue_adaptation_context") or prediction.get("venue_adaptation_context"),
        "referee_analysis": item.get("referee_analysis"),
        "play_card": play_card,
        "divination_overlay": item.get("divination_overlay") or card.get("divination_overlay"),
        "divination_hexagram": prediction.get("divination_hexagram") or prediction.get("hexagram") or card.get("divination_hexagram", ""),
        "home_ranking": _prediction_team_field(home, "ranking", card.get("home_ranking")),
        "away_ranking": _prediction_team_field(away, "ranking", card.get("away_ranking")),
        "evidence_gaps": prediction.get("evidence_gaps", card.get("evidence_gaps", [])),
        "play_title": play_card.get("share_title", card.get("play_title", "")),
        "risk_flags": play_card.get("risk_flags", card.get("risk_flags", [])) or [],
        "watch_points": play_card.get("watch_points", card.get("watch_points", [])) or [],
        "has_odds": has_market_odds,
        "market_odds": market_odds,
        "market_odds_status": market_odds_status,
        "market_odds_source": market_odds_status.get("source", "missing"),
        "market_odds_is_mock": bool(market_odds_status.get("is_mock")),
        "analysis_layers": item.get("analysis_layers", card.get("analysis_layers", [])) or [],
    })

    if not card.get("venue"):
        card["venue"] = item.get("venue", "")
    if not card.get("group"):
        card["group"] = item.get("group", "")
    if not card.get("phase"):
        card["phase"] = item.get("phase", "")
    if not card.get("kickoff_at"):
        card["kickoff_at"] = item.get("kickoff_at", "")
    return True


def _merge_daily_prediction_sources_into_cards(payload: dict, root: Path, edition: str, *, include_local: bool = True) -> int:
    prediction_items = _prediction_items_by_match(root, edition, include_local=include_local)
    if not prediction_items:
        return 0
    cards = payload.get("cards", []) or []
    by_match = {str(card.get("match_id", "")): card for card in cards if card.get("match_id")}
    merged = 0
    for match_id, item in prediction_items.items():
        card = by_match.get(match_id)
        if not card:
            continue
        needs_knockout_backfill = (
            _is_knockout_phase(str(card.get("phase") or item.get("phase") or ""))
            and (
                not card.get("knockout_prediction")
                or str(card.get("home_name") or "").upper().startswith("W")
                or str(card.get("away_name") or "").upper().startswith("W")
            )
        )
        if _prediction_exists(card) and not needs_knockout_backfill:
            continue
        if _merge_prediction_item_into_card(card, item, root=root):
            merged += 1
    if merged:
        payload.setdefault("summary", {})["prediction_source_backfill_count"] = merged
        invariants = payload.setdefault("safety_invariants", [])
        if "dashboard_backfills_missing_predictions_from_daily_prediction_reports" not in invariants:
            invariants.append("dashboard_backfills_missing_predictions_from_daily_prediction_reports")
    return merged


def query_db_data(db_path: Path) -> dict | None:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "matches" not in tables or "predictions" not in tables:
            conn.close()
            return None

        # Status values that represent REAL match data (not placeholders)
        _REAL_MATCH_STATUSES = ("final", "fixture_official", "live", "postponed", "suspended", "abandoned")
        _PLACEHOLDER_STATUS = "knockout_placeholder_until_teams_known"

        matches = [dict(r) for r in cursor.execute("""
            SELECT
                m.match_id, m.edition, m.phase, m.group_name, m.kickoff_at, m.venue, m.status AS match_status,
                m.home_team_id, m.away_team_id,
                m.final_score_home, m.final_score_away,
                t_home.name_zh AS home_name_zh, t_home.name_en AS home_name_en, t_home.colors AS home_colors,
                t_away.name_zh AS away_name_zh, t_away.name_en AS away_name_en, t_away.colors AS away_colors,
                p.prediction_date, p.predicted_result, p.predicted_score_home, p.predicted_score_away,
                p.confidence, p.divination_hexagram, p.generated_at AS predicted_at,
                p.evidence_quality, p.has_odds, p.has_referee, p.has_news, p.report_json_path,
                e.actual_score_home, e.actual_score_away, e.is_result_correct, e.is_score_correct,
                e.evaluated_at, e.primary_error, e.model_issue_tags_str, e.review_json_path
            FROM matches m
            LEFT JOIN teams t_home ON m.home_team_id = t_home.team_id
            LEFT JOIN teams t_away ON m.away_team_id = t_away.team_id
            LEFT JOIN predictions p ON m.match_id = p.match_id
            LEFT JOIN evaluations e ON m.match_id = e.match_id
            ORDER BY m.kickoff_at ASC, m.match_id ASC
        """).fetchall()]

        # Mark each match with its data source type
        for m in matches:
            status = str(m.get("match_status") or "")
            if status == _PLACEHOLDER_STATUS:
                m["_data_source"] = "placeholder"
                m["_data_source_label"] = "占位"
            elif status in _REAL_MATCH_STATUSES:
                m["_data_source"] = "official"
                m["_data_source_label"] = ""
            else:
                m["_data_source"] = "unknown"
                m["_data_source_label"] = ""

        actions = []
        if "corrective_actions" in tables:
            actions = [dict(r) for r in cursor.execute("""
                SELECT action_id, priority, description, status, created_at
                FROM corrective_actions
                WHERE status = 'open'
                ORDER BY CASE priority WHEN 'P0' THEN 1 WHEN 'P1' THEN 2 WHEN 'P2' THEN 3 ELSE 4 END, created_at DESC
            """).fetchall()]

        issue_tags = []
        if "model_issue_tags" in tables:
            issue_tags = [dict(r) for r in cursor.execute("""
                SELECT tag, severity, SUM(occurrence_count) as total_occurrences, MIN(first_seen_in) as first_seen
                FROM model_issue_tags
                GROUP BY tag, severity
                ORDER BY total_occurrences DESC
            """).fetchall()]

        daily_stats = []
        if "daily_stats" in tables:
            daily_stats = [dict(r) for r in cursor.execute("""
                SELECT stat_date, matches_evaluated, result_hits, score_hits, total_goals_hits,
                       result_hit_rate, score_hit_rate, total_goals_hit_rate,
                       brier_score_result, brier_score_total_goals, avg_confidence,
                       high_confidence_hit_rate, medium_confidence_hit_rate, low_confidence_hit_rate,
                       top_error, updated_at
                FROM daily_stats
                ORDER BY stat_date DESC
            """).fetchall()]

        layers = []
        if "prediction_analysis_layers" in tables:
            layers = [dict(r) for r in cursor.execute("""
                SELECT match_id, layer_id, title, verdict, confidence
                FROM prediction_analysis_layers
                ORDER BY match_id ASC, layer_id ASC
            """).fetchall()]

        players = []
        if "players" in tables:
            players = [dict(r) for r in cursor.execute("""
                SELECT team_id, shirt_number, position, player_name, club, height_cm
                FROM players
                ORDER BY team_id ASC, position ASC, shirt_number ASC
            """).fetchall()]

        players = []
        if "players" in tables:
            players = [dict(r) for r in cursor.execute("""
                SELECT team_id, shirt_number, position, player_name, club, height_cm
                FROM players
                ORDER BY team_id ASC, position ASC, shirt_number ASC
            """).fetchall()]

        conn.close()
        return {
            "matches": matches,
            "corrective_actions": actions,
            "model_issue_tags": issue_tags,
            "daily_stats": daily_stats,
            "analysis_layers": layers,
            "players": players
        }
    except Exception:
        return None


def load_all_historical_matches(root: Path, edition: str) -> list[dict]:
    try:
        from worldcup_history_fetcher import parse_matches
    except ImportError:
        return []
    raw_root = root / "knowledge-base" / edition / "raw"
    snap_dir = raw_root / "snapshots"
    if not snap_dir.exists():
        return []

    all_matches = []
    for path in sorted(snap_dir.glob("openfootball-wc-*.txt")):
        filename = path.name
        parts = filename.split("-")
        if len(parts) < 3 or not parts[2].isdigit():
            continue
        year = int(parts[2])
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            matches = parse_matches(text)
            for m in matches:
                # Apply date prefix cleaning to home and away teams
                m["home_team"] = re.sub(r"^\d{1,2}\s+[A-Za-z]+\s*", "", re.sub(r"^\d{1,2}:\d{2}\s*", "", m["home_team"]).strip()).strip()
                m["away_team"] = re.sub(r"^\d{1,2}\s+[A-Za-z]+\s*", "", re.sub(r"^\d{1,2}:\d{2}\s*", "", m["away_team"]).strip()).strip()
                m["year"] = year
                all_matches.append(m)
        except Exception:
            continue
    return all_matches


def find_h2h_matches(all_history: list[dict], team_a: str, team_b: str) -> list[dict]:
    try:
        from worldcup_history_fetcher import _normalize_key, TEAM_NAME_ALIASES
    except ImportError:
        return []

    def canonical_key(name: str) -> str:
        key = _normalize_key(name)
        if key in TEAM_NAME_ALIASES:
            return _normalize_key(TEAM_NAME_ALIASES[key])
        return key

    key_a = canonical_key(team_a)
    key_b = canonical_key(team_b)

    h2h = []
    for m in all_history:
        m_home = canonical_key(m["home_team"])
        m_away = canonical_key(m["away_team"])

        if (m_home == key_a and m_away == key_b) or (m_home == key_b and m_away == key_a):
            h2h.append({
                "year": m["year"],
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "home_goals": m["home_goals"],
                "away_goals": m["away_goals"],
                "home_pen": m.get("home_pen"),
                "away_pen": m.get("away_pen"),
                "stage": m.get("stage", "Group")
            })
    h2h.sort(key=lambda x: x["year"], reverse=True)
    return h2h


def calculate_radar_dimensions(ranking: dict | None, squad: dict | None, history: dict | None, is_home: bool, xg: float | None, cs: float | None) -> dict[str, int]:
    # Points baseline: 1500
    points = ranking.get("points") or 1500.0 if ranking else 1500.0
    fw_count = squad.get("position_counts", {}).get("FW") or 7 if squad else 7
    xg_val = xg if xg is not None else (1.5 if is_home else 1.2)

    attack = 60 + (points - 1400) * 0.04 + (fw_count - 6) * 1.5 + (xg_val - 1.2) * 12
    attack = min(99, max(50, int(attack)))

    df_count = squad.get("position_counts", {}).get("DF") or 8 if squad else 8
    cs_val = cs if cs is not None else 0.3

    defense = 60 + (points - 1400) * 0.04 + (df_count - 7) * 1.5 + (cs_val - 0.25) * 40
    defense = min(99, max(50, int(defense)))

    mf_count = squad.get("position_counts", {}).get("MF") or 8 if squad else 8
    midfield = 60 + (points - 1400) * 0.05 + (mf_count - 6) * 2.0
    midfield = min(99, max(50, int(midfield)))

    age = squad.get("avg_age_years") or 27.5 if squad else 27.5
    age_factor = (29.0 - age) * 2.0
    fitness = 75 + age_factor + (5 if is_home else 0)
    fitness = min(99, max(50, int(fitness)))

    wc_matches = history.get("wc_total_matches") or 0 if history else 0
    wc_wins = history.get("wc_wins") or 0 if history else 0
    win_rate = wc_wins / wc_matches if wc_matches else 0.4

    recent_form = 65 + (points - 1400) * 0.03 + (win_rate - 0.4) * 20
    recent_form = min(99, max(50, int(recent_form)))

    return {
        "attack": attack,
        "defense": defense,
        "midfield": midfield,
        "fitness": fitness,
        "recent_form": recent_form
    }


def get_team_recent_form(all_history: list[dict], team_name_or_id: str, db_matches: list[dict], canonical_key_func) -> list[dict]:
    key = canonical_key_func(team_name_or_id)
    recent_matches = []

    for m in db_matches:
        if m.get("final_score_home") is not None and m.get("final_score_away") is not None:
            m_h = canonical_key_func(m.get("home_name_en") or m.get("home_team_id") or "")
            m_a = canonical_key_func(m.get("away_name_en") or m.get("away_team_id") or "")

            if m_h == key or m_a == key:
                is_home = (m_h == key)
                goals_for = m["final_score_home"] if is_home else m["final_score_away"]
                goals_against = m["final_score_away"] if is_home else m["final_score_home"]

                outcome = "W" if goals_for > goals_against else "L" if goals_against > goals_for else "D"
                opp_name = m["away_name_zh"] or m["away_name_en"] if is_home else m["home_name_zh"] or m["home_name_en"]

                recent_matches.append({
                    "date": m["kickoff_at"][:10] if m.get("kickoff_at") else "2026",
                    "opponent": opp_name,
                    "score": f"{goals_for}-{goals_against}",
                    "outcome": outcome,
                    "is_current": True
                })

    for m in all_history:
        m_h = canonical_key_func(m["home_team"])
        m_a = canonical_key_func(m["away_team"])

        if m_h == key or m_a == key:
            is_home = (m_h == key)
            goals_for = m["home_goals"] if is_home else m["away_goals"]
            goals_against = m["away_goals"] if is_home else m["home_goals"]

            outcome = "W" if goals_for > goals_against else "L" if goals_against > goals_for else "D"
            opp_name = m["away_team"] if is_home else m["home_team"]

            recent_matches.append({
                "date": str(m["year"]),
                "opponent": opp_name,
                "score": f"{goals_for}-{goals_against}",
                "outcome": outcome,
                "is_current": False
            })

    recent_matches.sort(key=lambda x: (x["is_current"], x["date"]), reverse=True)
    return recent_matches[:5]


def _ledger_team_ids(ledger: dict, match_id: str) -> tuple[str, str]:
    for match in _canonical_ledger_matches(ledger):
        if match.get("match_id") == match_id:
            home = match.get("home_team") or {}
            away = match.get("away_team") or {}
            return str(home.get("team_id") or "").lower(), str(away.get("team_id") or "").lower()
    return "", ""


def _ledger_matches_by_id(ledger: dict) -> dict[str, dict]:
    return {
        str(match.get("match_id") or ""): match
        for match in _canonical_ledger_matches(ledger)
        if match.get("match_id")
    }


def _daily_evidence_details(ed_root: Path, date: str, home_id: str, away_id: str) -> dict:
    daily_ev_path = ed_root / "daily-evidence" / f"{date}.json"
    details = {
        "home_injuries": [],
        "away_injuries": [],
        "home_suspensions": [],
        "away_suspensions": [],
        "late_news": [],
    }
    if not daily_ev_path.exists():
        return details
    try:
        dev_data = load_json(daily_ev_path, {})
    except Exception:
        return details
    inj_ext = dev_data.get("injuries_extracted", {}) or {}
    teams_inj = inj_ext.get("teams", {}) or {}

    home_inj_data = teams_inj.get(home_id.upper(), {}) or {}
    away_inj_data = teams_inj.get(away_id.upper(), {}) or {}
    details["home_injuries"].extend(home_inj_data.get("injuries", []) or [])
    details["away_injuries"].extend(away_inj_data.get("injuries", []) or [])
    details["home_suspensions"].extend(home_inj_data.get("suspensions", []) or [])
    details["away_suspensions"].extend(away_inj_data.get("suspensions", []) or [])

    # injuries can be a flat list (legacy) or a dict with teams sub-dict (new format)
    inj_root = dev_data.get("injuries", []) or []
    if isinstance(inj_root, dict):
        for _tc, _td in (inj_root.get("teams") or {}).items():
            if not isinstance(_td, dict):
                continue
            for inj in (_td.get("injuries") or []):
                team = str(inj.get("team_code") or _tc).lower()
                if team == home_id:
                    details["home_injuries"].append(inj)
                elif team == away_id:
                    details["away_injuries"].append(inj)
            for susp in (_td.get("suspensions") or []):
                team = str(susp.get("team_code") or _tc).lower()
                if team == home_id:
                    details["home_suspensions"].append(susp)
                elif team == away_id:
                    details["away_suspensions"].append(susp)
    elif isinstance(inj_root, list):
        for inj in inj_root:
            if not isinstance(inj, dict):
                continue
            team = str(inj.get("team_code") or "").lower()
            if team == home_id:
                details["home_injuries"].append(inj)
            elif team == away_id:
                details["away_injuries"].append(inj)

    for susp in dev_data.get("suspensions", []) or []:
        team = str(susp.get("team_code") or "").lower()
        if team == home_id:
            details["home_suspensions"].append(susp)
        elif team == away_id:
            details["away_suspensions"].append(susp)

    for news in dev_data.get("late_news", []) or []:
        team = str(news.get("team_code") or "").lower()
        if team in (home_id, away_id):
            details["late_news"].append(news)
    return details


def _daily_evidence_match_odds_status(ed_root: Path, date: str, match_id: str) -> tuple[bool, dict | None, dict] | None:
    daily_ev_path = ed_root / "daily-evidence" / f"{date}.json"
    if not daily_ev_path.exists():
        return None
    try:
        dev_data = load_json(daily_ev_path, {})
    except Exception:
        return None
    for match in dev_data.get("matches", []) or []:
        if match.get("match_id") != match_id:
            continue
        odds = match.get("odds")
        if not odds:
            return None
        market_odds = {"odds": odds}
        explicit_status = {
            "source": odds.get("source", "missing"),
            "is_mock": bool(odds.get("is_mock")),
            "reason": odds.get("reason", ""),
        }
        return _normalize_market_odds_status(market_odds, explicit_status)
    return None


def _enrich_card_from_sources(
    card: dict,
    *,
    ledger: dict,
    ed_root: Path,
    players_by_team: dict[str, list[dict]],
    all_history: list[dict],
    db_matches: list[dict],
    canonical_key_func,
    team_id_to_name: dict[str, str],
) -> None:
    home_id, away_id = _ledger_team_ids(ledger, str(card.get("match_id", "")))
    if not home_id or not away_id:
        return
    home_name = team_id_to_name.get(home_id, home_id)
    away_name = team_id_to_name.get(away_id, away_id)
    if not card.get("home_players"):
        card["home_players"] = players_by_team.get(home_id, [])
    if not card.get("away_players"):
        card["away_players"] = players_by_team.get(away_id, [])
    if not card.get("home_form"):
        card["home_form"] = get_team_recent_form(all_history, home_name, db_matches, canonical_key_func)
    if not card.get("away_form"):
        card["away_form"] = get_team_recent_form(all_history, away_name, db_matches, canonical_key_func)
    if not card.get("h2h"):
        card["h2h"] = find_h2h_matches(all_history, home_name, away_name)
    evidence = _daily_evidence_details(ed_root, str(card.get("date", ""))[:10], home_id, away_id)
    for key, value in evidence.items():
        if value and not card.get(key):
            card[key] = value


def build_dashboard_payload(*, root: Path, edition: str, now: str | None = None, include_local: bool = True) -> dict:
    generated_at = iso_now(now)
    data_path, html_path = _dashboard_paths(root, edition)
    db_path = worldcup_db_path(root, edition)
    ledger = _load_match_ledger(root, edition)
    ledger_by_id = _ledger_matches_by_id(ledger)

    db_data = query_db_data(db_path) if include_local and db_path.exists() else None

    cards = []
    dates = set()

    if db_data:
        # Load indices for enrichment
        ed_root = edition_data_root(root, edition)
        rankings_data = load_json(raw_edition_root(root, edition) / "rankings/fifa-men-ranking.json", {"rankings": []})
        squad_data = load_edition_data_json(root, edition, "squad-depth-features.json", {"teams": [], "global_summary": {}})
        history_data = load_edition_data_json(root, edition, "history/team-wc-history.json", {"teams": []})

        ranking_index = {}
        for r in rankings_data.get("rankings", []):
            tid = r.get("team_id", "").lower()
            if tid:
                ranking_index[tid] = r

        squad_index = {}
        for t in squad_data.get("teams", []):
            tid = t.get("team_id", "").lower()
            if tid:
                squad_index[tid] = t

        history_index = {}
        for t in history_data.get("teams", []):
            tid = t.get("team_id", "").lower()
            if tid:
                history_index[tid] = t

        all_history = load_all_historical_matches(root, edition)

        players_by_team = {}
        for p in db_data.get("players", []):
            tid = p["team_id"].lower()
            players_by_team.setdefault(tid, []).append({
                "shirt_number": p["shirt_number"],
                "position": p["position"],
                "name": p["player_name"],
                "club": p["club"],
                "height": p["height_cm"]
            })

        try:
            from worldcup_history_fetcher import _normalize_key, TEAM_NAME_ALIASES
            def canonical_key(name: str) -> str:
                key = _normalize_key(name)
                if key in TEAM_NAME_ALIASES:
                    return _normalize_key(TEAM_NAME_ALIASES[key])
                return key
        except ImportError:
            def canonical_key(name: str) -> str:
                return name.lower().strip()

        layers_by_match = {}
        for layer in db_data.get("analysis_layers", []):
            m_id = layer["match_id"]
            layers_by_match.setdefault(m_id, []).append({
                "layer_id": layer["layer_id"],
                "title": layer["title"],
                "verdict": layer["verdict"],
                "confidence": layer["confidence"]
            })

        team_id_to_name = {}
        for m in db_data.get("matches", []):
            h_id = m.get("home_team_id")
            if h_id and m.get("home_name_en"):
                team_id_to_name[h_id.lower()] = m.get("home_name_en")
            a_id = m.get("away_team_id")
            if a_id and m.get("away_name_en"):
                team_id_to_name[a_id.lower()] = m.get("away_name_en")

        for m in db_data["matches"]:
            if not m["predicted_result"]:
                ledger_match = ledger_by_id.get(str(m.get("match_id") or ""))
                if not ledger_match:
                    continue
                fact_card = _fact_card_from_match(ledger_match)
                if fact_card.get("data_source") != "placeholder":
                    fact_card["home_name"] = m.get("home_name_zh") or m.get("home_name_en") or fact_card["home_name"]
                    fact_card["away_name"] = m.get("away_name_zh") or m.get("away_name_en") or fact_card["away_name"]
                fact_card["home_colors"] = m.get("home_colors") or ""
                fact_card["away_colors"] = m.get("away_colors") or ""
                fact_card["home_players"] = players_by_team.get((m.get("home_team_id") or "").lower(), [])
                fact_card["away_players"] = players_by_team.get((m.get("away_team_id") or "").lower(), [])
                fact_card["actual_score_home"] = m.get("final_score_home") if m.get("final_score_home") is not None else fact_card.get("actual_score_home")
                fact_card["actual_score_away"] = m.get("final_score_away") if m.get("final_score_away") is not None else fact_card.get("actual_score_away")
                fact_card["is_completed"] = fact_card["actual_score_home"] is not None and fact_card["actual_score_away"] is not None
                fact_card["actual_result"] = _actual_result_from_score(fact_card["actual_score_home"], fact_card["actual_score_away"])
                cards.append(fact_card)
                # Only collect dates from REAL (non-placeholder) matches
                if fact_card["date"] and fact_card.get("data_source") != "placeholder":
                    dates.add(fact_card["date"])
                continue

            result = m["predicted_result"]
            confidence = m["confidence"] or "unknown"
            local_date = m["prediction_date"] or ""
            if not local_date and m.get("kickoff_at"):
                try:
                    _bjt = timezone(timedelta(hours=8))
                    local_date = datetime.fromisoformat(str(m["kickoff_at"]).replace("Z", "+00:00")).astimezone(_bjt).strftime("%Y-%m-%d")
                except Exception:
                    local_date = str(m.get("kickoff_at") or "")[:10]
            local_date = local_date or "unknown"
            ledger_match = ledger_by_id.get(str(m.get("match_id") or ""))
            db_home_name = m["home_name_zh"] or m["home_name_en"] or ""
            db_away_name = m["away_name_zh"] or m["away_name_en"] or ""
            ledger_home_name = _match_team_name(ledger_match, "home") if ledger_match else ""
            ledger_away_name = _match_team_name(ledger_match, "away") if ledger_match else ""
            display_home_name = db_home_name or ledger_home_name or "Home"
            display_away_name = db_away_name or ledger_away_name or "Away"
            unresolved_placeholder = bool(
                ledger_match
                and str(ledger_match.get("status") or "") == "knockout_placeholder_until_teams_known"
                and (
                    _is_placeholder_team_name(ledger_home_name or display_home_name)
                    or _is_placeholder_team_name(ledger_away_name or display_away_name)
                )
            )
            resolved_knockout_match = bool(
                not unresolved_placeholder
                and not _is_placeholder_team_name(ledger_home_name or display_home_name)
                and not _is_placeholder_team_name(ledger_away_name or display_away_name)
                and (
                    (ledger_home_name and ledger_away_name)
                    or (db_home_name and db_away_name)
                    or (m.get("final_score_home") is not None and m.get("final_score_away") is not None)
                )
            )
            data_source = m.get("_data_source", "official")
            data_source_label = m.get("_data_source_label", "")
            if unresolved_placeholder:
                data_source = "placeholder"
                data_source_label = "占位"
            elif data_source == "placeholder" and resolved_knockout_match:
                data_source = "official"
                data_source_label = ""

            expected_goals_proxy = None
            clean_sheet_probability = None
            scoreline_distribution = None
            result_confidence = confidence
            score_confidence = "unknown"
            total_goals_confidence = "unknown"
            confidence_note = ""
            venue_adaptation_context = None
            referee_analysis = None
            play_card = {}
            home_ranking = None
            away_ranking = None
            market_odds = None
            market_odds_status = {"status": "missing", "source": "missing", "is_mock": False, "reason": ""}
            predicted_result_from_fallback = None

            # Load report JSON file to get deep model metrics if report_json_path exists
            report_path_str = m.get("report_json_path")
            report_found = False
            if report_path_str:
                report_path = Path(report_path_str)
                if not report_path.is_absolute():
                    report_path = root / report_path

                # Check if file exists, if not, try to search it in the reports folder
                if not report_path.exists():
                    filename = report_path.name
                    alt_path = edition_data_root(root, edition) / "reports" / "daily-predictions" / filename
                    if alt_path.exists():
                        report_path = alt_path
                    else:
                        alt_path2 = edition_data_root(root, edition) / "reports" / filename
                        if alt_path2.exists():
                            report_path = alt_path2

                if report_path.exists():
                    try:
                        report_json = load_json(report_path, {})
                        for p in report_json.get("predictions", []):
                            if p.get("match_id") == m["match_id"]:
                                pred_sec = p.get("prediction", {})
                                expected_goals_proxy = pred_sec.get("expected_goals_proxy")
                                clean_sheet_probability = pred_sec.get("clean_sheet_probability")
                                scoreline_distribution = pred_sec.get("scoreline_distribution")
                                result_confidence = pred_sec.get("result_confidence") or result_confidence
                                score_confidence = pred_sec.get("score_confidence") or score_confidence
                                total_goals_confidence = pred_sec.get("total_goals_confidence") or total_goals_confidence
                                confidence_note = pred_sec.get("confidence_note") or confidence_note
                                venue_adaptation_context = p.get("venue_adaptation_context") or report_json.get("venue_adaptation_context")
                                referee_analysis = p.get("referee_analysis")
                                play_card = p.get("play_card", {}) or {}
                                home_ranking = p.get("home_team", {}).get("ranking")
                                away_ranking = p.get("away_team", {}).get("ranking")
                                market_odds = p.get("market_odds")
                                market_odds_status = p.get("market_odds_status") or market_odds_status
                                report_found = True
                                break
                    except Exception:
                        pass

            # Fallback: try person reports if no report found via report_json_path
            if not report_found:
                kickoff = m.get("kickoff_at", "")
                if kickoff:
                    date_str = kickoff[:10]
                    # Step A: try daily-predictions aggregate files (person > public > default-predictions)
                    _search_dirs = [
                        person_edition_root(root, edition) / "reports" / "daily-predictions",
                        edition_data_root(root, edition) / "reports" / "daily-predictions",
                        edition_data_root(root, edition) / "default-predictions" / "daily-predictions",
                    ]
                    for _rdir in _search_dirs:
                        _rfile = _rdir / f"{date_str}.json"
                        if _rfile.exists():
                            try:
                                _rdata = load_json(_rfile, {})
                                for p in _rdata.get("predictions", []):
                                    if p.get("match_id") == m["match_id"]:
                                        pred_sec = p.get("prediction", {})
                                        if not predicted_result_from_fallback:
                                            predicted_result_from_fallback = pred_sec.get("result") or p.get("prediction", {}).get("predicted_outcome")
                                        if not scoreline_distribution:
                                            scoreline_distribution = pred_sec.get("scoreline_distribution")
                                        if not expected_goals_proxy:
                                            expected_goals_proxy = pred_sec.get("expected_goals_proxy")
                                        if not clean_sheet_probability:
                                            clean_sheet_probability = pred_sec.get("clean_sheet_probability")
                                        if not play_card or not play_card.get("watch_points"):
                                            play_card = p.get("play_card", {}) or play_card
                                        if not venue_adaptation_context:
                                            venue_adaptation_context = p.get("venue_adaptation_context")
                                        if not referee_analysis:
                                            referee_analysis = p.get("referee_analysis")
                                        if not home_ranking:
                                            home_ranking = p.get("home_team", {}).get("ranking")
                                        if not away_ranking:
                                            away_ranking = p.get("away_team", {}).get("ranking")
                                        if not market_odds:
                                            market_odds = p.get("market_odds")
                                        if not result_confidence or result_confidence == confidence:
                                            result_confidence = pred_sec.get("result_confidence") or result_confidence
                                        if score_confidence == "unknown":
                                            score_confidence = pred_sec.get("score_confidence") or score_confidence
                                        break
                            except Exception:
                                pass
                        if scoreline_distribution:
                            break

                    # Step B: if still nothing, scan standalone *-prediction-report.json files
                    if not scoreline_distribution:
                        for _report_dir in [
                            person_edition_root(root, edition) / "reports",
                            edition_data_root(root, edition) / "reports",
                        ]:
                            if not _report_dir.exists():
                                continue
                            try:
                                for _rp_file in sorted(_report_dir.glob("*-prediction-report.json")):
                                    _rp_data = load_json(_rp_file, {})
                                    for p in _rp_data.get("predictions", []):
                                        if p.get("match_id") == m["match_id"]:
                                            pred_sec = p.get("prediction", {}) or {}
                                            if not predicted_result_from_fallback:
                                                predicted_result_from_fallback = pred_sec.get("result") or pred_sec.get("predicted_outcome")
                                            if not scoreline_distribution:
                                                scoreline_distribution = pred_sec.get("scoreline_distribution")
                                            if not expected_goals_proxy:
                                                expected_goals_proxy = pred_sec.get("expected_goals_proxy") or p.get("expected_goals_proxy")
                                            if not clean_sheet_probability:
                                                clean_sheet_probability = pred_sec.get("clean_sheet_probability") or p.get("clean_sheet_probability")
                                            if not play_card or not play_card.get("watch_points"):
                                                play_card = p.get("play_card", {}) or play_card
                                            if not venue_adaptation_context:
                                                venue_adaptation_context = p.get("venue_adaptation_context") or pred_sec.get("venue_adaptation_context")
                                            if not referee_analysis:
                                                referee_analysis = p.get("referee_analysis")
                                            if not home_ranking:
                                                home_ranking = p.get("home_team", {}).get("ranking")
                                            if not away_ranking:
                                                away_ranking = p.get("away_team", {}).get("ranking")
                                            if not market_odds:
                                                market_odds = p.get("market_odds")
                                            if not result_confidence or result_confidence == confidence:
                                                result_confidence = pred_sec.get("result_confidence") or result_confidence
                                            if score_confidence == "unknown":
                                                score_confidence = pred_sec.get("score_confidence") or score_confidence
                                            break
                                    if scoreline_distribution:
                                        break
                            except Exception:
                                pass
                            if scoreline_distribution:
                                break
            result_hit = True if m["is_result_correct"] == 1 else False if m["is_result_correct"] == 0 else None
            score_hit = True if m["is_score_correct"] == 1 else False if m["is_score_correct"] == 0 else None

            # Live evaluation fallback: if DB has no evaluation but we found prediction data
            # and actual score exists, compute direction hit in real-time
            if result_hit is None and (scoreline_distribution or predicted_result_from_fallback):
                fsh = m.get("final_score_home")
                fsa = m.get("final_score_away")
                if fsh is not None and fsa is not None:
                    # Determine predicted direction from fallback data
                    _pred_result = predicted_result_from_fallback
                    if not _pred_result and scoreline_distribution:
                        top = max(scoreline_distribution, key=lambda x: x.get("probability", 0))
                        sc = top.get("score", {}) or {}
                        h, a = sc.get("home", 0), sc.get("away", 0)
                        if h > a:
                            _pred_result = "home_win"
                        elif a > h:
                            _pred_result = "away_win"
                        else:
                            _pred_result = "draw"
                    if _pred_result:
                        if fsh > fsa:
                            _actual = "home_win"
                        elif fsa > fsh:
                            _actual = "away_win"
                        else:
                            _actual = "draw"
                        result_hit = (_pred_result == _actual)
                        # Also check exact score if we have it
                        if scoreline_distribution:
                            top = max(scoreline_distribution, key=lambda x: x.get("probability", 0))
                            sc = top.get("score", {}) or {}
                            score_hit = (sc.get("home") == fsh and sc.get("away") == fsa)

            if result_hit is True and score_hit is True:
                hit_class = "double-hit"
                eval_label = "完美双中"
            elif result_hit is True:
                hit_class = "result-hit"
                eval_label = "仅中赛果"
            elif result_hit is False:
                hit_class = "miss"
                eval_label = "预测偏差"
            else:
                hit_class = "pending"
                eval_label = "待开赛"

            home_id = m.get("home_team_id") or ""
            away_id = m.get("away_team_id") or ""
            if ledger_match:
                home_id = str((ledger_match.get("home_team") or {}).get("team_id") or home_id or "")
                away_id = str((ledger_match.get("away_team") or {}).get("team_id") or away_id or "")

            home_ranking_info = ranking_index.get(home_id.lower())
            away_ranking_info = ranking_index.get(away_id.lower())
            home_squad_info = squad_index.get(home_id.lower())
            away_squad_info = squad_index.get(away_id.lower())
            home_history_info = history_index.get(home_id.lower())
            away_history_info = history_index.get(away_id.lower())

            home_radar = calculate_radar_dimensions(
                home_ranking_info, home_squad_info, home_history_info,
                is_home=True,
                xg=expected_goals_proxy.get("home") if expected_goals_proxy else None,
                cs=clean_sheet_probability.get("home") if clean_sheet_probability else None
            )
            away_radar = calculate_radar_dimensions(
                away_ranking_info, away_squad_info, away_history_info,
                is_home=False,
                xg=expected_goals_proxy.get("away") if expected_goals_proxy else None,
                cs=clean_sheet_probability.get("away") if clean_sheet_probability else None
            )

            home_en_name = team_id_to_name.get(home_id.lower(), home_id)
            away_en_name = team_id_to_name.get(away_id.lower(), away_id)
            home_form = get_team_recent_form(all_history, home_en_name, db_data["matches"], canonical_key)
            away_form = get_team_recent_form(all_history, away_en_name, db_data["matches"], canonical_key)

            h2h_matches = find_h2h_matches(all_history, home_en_name, away_en_name)

            home_players = players_by_team.get(home_id.lower(), [])
            away_players = players_by_team.get(away_id.lower(), [])

            market_has_odds, market_odds, market_odds_status = _normalize_market_odds_status(market_odds, market_odds_status)
            evidence_details = _daily_evidence_details(ed_root, local_date, home_id.lower(), away_id.lower())

            # Compute Tianji divination overlay on the fly
            divination_overlay = compute_tianji_overlay(
                m["kickoff_at"],
                m["match_id"],
                venue=m["venue"]
            )
            # Also compute I Ching hexagram overlay (with match context)
            _home_zh = display_home_name
            _away_zh = display_away_name
            _hex_overlay = compute_divination_overlay(local_date, m["match_id"],
                                                      home_name=_home_zh, away_name=_away_zh)
            divination_overlay["hexagram_number"] = _hex_overlay["hexagram_number"]
            divination_overlay["hexagram_name"] = _hex_overlay["hexagram_name"]
            divination_overlay["hexagram"] = _hex_overlay["hexagram_name"]
            divination_overlay["hexagram_interpretation"] = _hex_overlay["interpretation"]
            divination_overlay["hexagram_home_modifier"] = _hex_overlay["home_modifier"]
            divination_overlay["hexagram_away_modifier"] = _hex_overlay["away_modifier"]
            # New: match-specific interpretation fields
            divination_overlay["match_interpretation"] = _hex_overlay.get("match_interpretation", "")
            divination_overlay["home_fortune"] = _hex_overlay.get("home_fortune", "")
            divination_overlay["away_fortune"] = _hex_overlay.get("away_fortune", "")
            divination_overlay["fortune_summary"] = _hex_overlay.get("fortune_summary", "")

            card = {
                "match_id": m["match_id"],
                "prediction_origin": "user_local",
                "prediction_source": "user_local",
                "prediction_source_path": m.get("report_json_path") or "",
                "data_origin": "user_local",
                # Data source: official once real teams/results are known locally.
                "data_source": data_source,
                "data_source_label": data_source_label,
                "divination_overlay": divination_overlay,
                "date": local_date,
                "kickoff_at": m["kickoff_at"],
                "local_kickoff_at": m["kickoff_at"],
                "calculation_timezone": "LocalTime",
                "venue": m["venue"] or "",
                "group": m["group_name"] or "",
                "phase": m["phase"] or "",
                "home_name": display_home_name,
                "away_name": display_away_name,
                "predicted_result": "" if unresolved_placeholder else result,
                "predicted_result_label": "未预测" if unresolved_placeholder else OUTCOME_LABELS.get(result, result or "Unknown"),
                "score_text": "-:-" if unresolved_placeholder else (f"{m['predicted_score_home']}-{m['predicted_score_away']}" if m['predicted_score_home'] is not None else "-:-"),
                "knockout_prediction": None,
                "total_goals": "-" if unresolved_placeholder else ((m['predicted_score_home'] or 0) + (m['predicted_score_away'] or 0) if m['predicted_score_home'] is not None else "-"),
                "confidence": "none" if unresolved_placeholder else confidence,
                "confidence_label": "NONE" if unresolved_placeholder else confidence.upper(),

                # Deep prediction fields
                "expected_goals_proxy": None if unresolved_placeholder else expected_goals_proxy,
                "clean_sheet_probability": None if unresolved_placeholder else clean_sheet_probability,
                "scoreline_distribution": None if unresolved_placeholder else scoreline_distribution,
                "result_confidence": "none" if unresolved_placeholder else result_confidence,
                "score_confidence": "none" if unresolved_placeholder else score_confidence,
                "total_goals_confidence": "none" if unresolved_placeholder else total_goals_confidence,
                "confidence_note": confidence_note,
                "venue_adaptation_context": venue_adaptation_context,
                "referee_analysis": referee_analysis,
                "play_card": {} if unresolved_placeholder else play_card,

                "divination_hexagram": m["divination_hexagram"] or "",
                "evaluation_status": "evaluated" if m["is_result_correct"] is not None else "pending_final_score",
                "evaluation_label": eval_label,
                "hit_class": hit_class,
                "result_hit": result_hit,
                "score_hit": score_hit,
                "actual_score_home": m.get("final_score_home"),
                "actual_score_away": m.get("final_score_away"),
                "actual_result": _actual_result_from_score(m.get("final_score_home"), m.get("final_score_away")),
                "knockout_actual": None,
                "is_completed": m.get("final_score_home") is not None and m.get("final_score_away") is not None,
                "home_colors": m.get("home_colors") or "",
                "away_colors": m.get("away_colors") or "",
                "home_ranking": home_ranking,
                "away_ranking": away_ranking,
                "evidence_gaps": [],
                "play_title": "" if unresolved_placeholder else play_card.get("share_title", ""),
                "risk_flags": [] if unresolved_placeholder else (play_card.get("risk_flags", []) or []),
                "watch_points": [] if unresolved_placeholder else (play_card.get("watch_points", []) or []),
                "primary_error": m["primary_error"] or "",
                "has_odds": market_has_odds,
                "market_odds": market_odds,
                "market_odds_status": market_odds_status,
                "market_odds_source": market_odds_status.get("source", "missing"),
                "market_odds_is_mock": bool(market_odds_status.get("is_mock")),
                "has_referee": bool(m["has_referee"]),
                "has_news": bool(m["has_news"]),
                "analysis_layers": layers_by_match.get(m["match_id"], []),
                "home_radar": home_radar,
                "away_radar": away_radar,
                "home_form": home_form,
                "away_form": away_form,
                "h2h": h2h_matches,
                "home_players": home_players,
                "away_players": away_players,
                "home_injuries": evidence_details["home_injuries"],
                "away_injuries": evidence_details["away_injuries"],
                "home_suspensions": evidence_details["home_suspensions"],
                "away_suspensions": evidence_details["away_suspensions"],
                "late_news": evidence_details["late_news"],
                "prediction_status": "not_predicted" if unresolved_placeholder else "locked_pre_match_prediction"
            }

            if not card["has_odds"]:
                card["evidence_gaps"].append("missing odds")
            if not card["has_referee"]:
                card["evidence_gaps"].append("missing referee")
            if not card["has_news"]:
                card["evidence_gaps"].append("missing news")
            if not card["evidence_gaps"]:
                card["evidence_gaps"].append("evidence complete")

            cards.append(card)
            # Only collect dates from REAL (non-placeholder) matches
            if card["date"] and card.get("data_source") != "placeholder":
                dates.add(card["date"])

        # --- Stats: Only count REAL (non-placeholder) data ---
        real_cards = [c for c in cards if c.get("data_source") != "placeholder"]
        placeholder_count = len(cards) - len(real_cards)

        evaluated_matches = sum(1 for c in real_cards if c["evaluation_status"] == "evaluated")
        result_hits = sum(1 for c in real_cards if c["result_hit"] is True)

        # Score hits need to check from DB data but only for non-placeholder matches
        real_match_ids = {c.get("match_id") for c in real_cards}
        score_hits = sum(1 for m in db_data["matches"]
                         if m.get("match_id") in real_match_ids and m["is_score_correct"] == 1)
        score_hit_rate = _rate(score_hits, evaluated_matches)

        total_goals_hits = sum(1 for m in db_data["matches"]
                                if m.get("match_id") in real_match_ids
                                and m["final_score_home"] is not None
                                and m["predicted_score_home"] is not None
                                and (m["final_score_home"] + m["final_score_away"]) == (m["predicted_score_home"] + m["predicted_score_away"]))
        total_goals_hit_rate = _rate(total_goals_hits, evaluated_matches)

        brier_scores = [s["brier_score_result"] for s in db_data.get("daily_stats", []) if s["brier_score_result"] is not None]
        avg_brier = sum(brier_scores) / len(brier_scores) if brier_scores else 0.0

        payload = {
            "version": 1,
            "edition": edition,
            "generated_at": generated_at,
            "mode": "worldcup-prediction-visual-dashboard",
            "status": "written" if cards else "no_predictions_found",
            "data_path": _display_path(data_path, root),
            "html_path": _display_path(html_path, root),
            "summary": {
                "predictions": len(real_cards),
                "placeholder_count": placeholder_count,
                "total_cards": len(cards),
                "fact_cards": sum(1 for card in real_cards if card.get("prediction_status") == "not_predicted"),
                "dates": sorted(dates),
                "evaluated_matches": evaluated_matches,
                "result_hits": result_hits,
                "result_hit_rate": _rate(result_hits, evaluated_matches),
                "score_hit_rate": score_hit_rate,
                "total_goals_hit_rate": total_goals_hit_rate,
                "avg_brier_score": avg_brier,
                "divergent_matches": 0,
                "open_information_gaps": [],
            },
            "corrective_actions": db_data.get("corrective_actions", []),
            "model_issue_tags": db_data.get("model_issue_tags", []),
            "daily_stats": db_data.get("daily_stats", []),
            "cards": cards,
            "disclaimer": DISCLAIMER,
            "safety_invariants": [
                "dashboard_reads_sqlite_database_tables",
                "dashboard_does_not_emit_betting_or_stake_advice",
                "sqlite_remains_canonical"
            ]
        }
    else:
        # Fallback to older JSON aggregates scanning
        evaluations = _evaluation_index(root, edition)
        teams_data = load_edition_data_json(root, edition, "teams.json", [])
        colors_by_id = {t["team_id"].lower(): t.get("colors", "") for t in teams_data if isinstance(t, dict) and "team_id" in t}

        for item in _prediction_items_by_match(root, edition, include_local=include_local).values():
            card = _as_prediction_card(item, evaluation=evaluations.get(str(item.get("match_id", ""))))
            home_team = item.get("home_team")
            away_team = item.get("away_team")
            home_id = _prediction_team_id(home_team)
            away_id = _prediction_team_id(away_team)
            card["home_colors"] = colors_by_id.get(home_id, "")
            card["away_colors"] = colors_by_id.get(away_id, "")
            card["home_ranking"] = _prediction_team_field(home_team, "ranking")
            card["away_ranking"] = _prediction_team_field(away_team, "ranking")
            cards.append(card)
            # Only collect dates from REAL (non-placeholder) matches
            if card["date"] and card.get("data_source") != "placeholder":
                dates.add(card["date"])

        aggregate = _load_aggregate(root, edition)
        summary = aggregate.get("summary", {}) or {}
        rates = aggregate.get("rates", {}) or {}
        evaluated = int(summary.get("evaluated_matches", sum(1 for c in cards if c["evaluation_status"] == "evaluated")) or 0)
        result_hits = int(summary.get("result_hits", sum(1 for c in cards if c["result_hit"] is True)) or 0)

        payload = {
            "version": 1,
            "edition": edition,
            "generated_at": generated_at,
            "mode": "worldcup-prediction-visual-dashboard",
            "status": "written" if cards else "no_predictions_found",
            "data_path": _display_path(data_path, root),
            "html_path": _display_path(html_path, root),
            "summary": {
                "predictions": len(cards),
                "dates": sorted(dates),
                "evaluated_matches": evaluated,
                "result_hits": result_hits,
                "result_hit_rate": rates.get("result_hit_rate", _rate(result_hits, evaluated)),
                "score_hit_rate": rates.get("score_hit_rate", 0.0),
                "total_goals_hit_rate": rates.get("total_goals_hit_rate", 0.0),
                "avg_brier_score": 0.0,
                "divergent_matches": sum(1 for card in cards if card.get("alignment") == "divergent"),
                "open_information_gaps": [],
            },
            "corrective_actions": [],
            "model_issue_tags": [],
            "daily_stats": [],
            "cards": cards,
            "disclaimer": DISCLAIMER,
            "safety_invariants": [
                "dashboard_reads_locked_prediction_reports",
                "dashboard_does_not_emit_betting_or_stake_advice",
                "json_reports_remain_canonical"
            ]
        }
    existing_card_ids = {card.get("match_id") for card in payload.get("cards", [])}
    for match in _canonical_ledger_matches(ledger):
        mid = match.get("match_id")
        if mid and mid not in existing_card_ids:
            card = _fact_card_from_match(match)
            payload.setdefault("cards", []).append(card)
            existing_card_ids.add(mid)
            # Only collect dates from REAL (non-placeholder) matches
            if card.get("date") and card.get("data_source") != "placeholder":
                payload.setdefault("summary", {}).setdefault("dates", [])
                if card["date"] not in payload["summary"]["dates"]:
                    payload["summary"]["dates"].append(card["date"])
    payload.setdefault("summary", {})["predictions"] = sum(
        1 for card in payload.get("cards", []) if card.get("prediction_status") != "not_predicted"
    )
    payload["summary"]["fact_cards"] = sum(
        1 for card in payload.get("cards", []) if card.get("prediction_status") == "not_predicted"
    )
    payload["summary"]["dates"] = sorted(payload.get("summary", {}).get("dates", []))
    # SAFETY NET: Rebuild dates from ONLY real (non-placeholder) cards
    _final_real_dates = sorted(set(
        c["date"] for c in payload.get("cards", [])
        if c.get("date") and c.get("data_source") != "placeholder"
    ))
    if _final_real_dates:
        payload["summary"]["dates"] = _final_real_dates

    # Post-process: normalize divination_overlay to use Chinese team names
    # This fixes cached English team names in fortune_summary & match_interpretation
    import re as _re
    for _c in payload.get("cards", []):
        _div = _c.get("divination_overlay")
        if not isinstance(_div, dict):
            continue
        _zh_home = _c.get("home_name_zh") or _c.get("home_name", "") or ""
        _zh_away = _c.get("away_name_zh") or _c.get("away_name", "") or ""
        _hm = float(_div.get("hexagram_home_modifier", 0) or 0)
        _am = float(_div.get("hexagram_away_modifier", 0) or 0)

        _fs = _div.get("fortune_summary", "")
        if _fs and _re.search(r'[a-zA-Z]{2,}', _fs) and (_zh_home or _zh_away):
            if _hm > _am:
                _div["fortune_summary"] = f"利{_zh_home}"
            elif _am > _hm:
                _div["fortune_summary"] = f"利{_zh_away}"
            else:
                _div["fortune_summary"] = "势均力敌"

        # Also rebuild match_interpretation with Chinese team names
        _mi = _div.get("match_interpretation", "")
        if _mi and _re.search(r'[a-zA-Z]{2,}', _mi) and _zh_home and _zh_away:
            try:
                _hn = int(_div.get("hexagram_number", 1) or 1)
                _hn_name = _div.get("hexagram_name", "") or ""
                _hi = _div.get("hexagram_interpretation", "") or ""
                _new = _generate_match_hexagram_interpretation(
                    _hn, _hn_name, _hm, _am, _zh_home, _zh_away, hex_interp=_hi
                )
                _div["match_interpretation"] = _new.get("narrative", _mi)
            except Exception:
                pass

    # ── Observation data for model health monitoring ──
    observation_daily = payload.get("daily_stats", [])
    # Sort by stat_date ascending for trend charts
    observation_daily_sorted = sorted(observation_daily, key=lambda x: x.get("stat_date", ""))

    # Error distribution from evaluations
    error_distribution = []
    try:
        if db_path.exists():
            _obs_conn = sqlite3.connect(str(db_path))
            _obs_conn.row_factory = sqlite3.Row
            _obs_tables = [r[0] for r in _obs_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "evaluations" in _obs_tables:
                _err_rows = _obs_conn.execute("""
                    SELECT COALESCE(primary_error, 'unknown') AS err_type,
                           COUNT(*) AS cnt
                    FROM evaluations
                    WHERE primary_error IS NOT NULL AND primary_error != ''
                    GROUP BY primary_error
                    ORDER BY cnt DESC
                """).fetchall()
                error_distribution = [{"error_type": r["err_type"], "count": r["cnt"]}
                                      for r in _err_rows]
                # Also add rows with no primary_error as "correct"
                _correct_row = _obs_conn.execute("""
                    SELECT COUNT(*) AS cnt FROM evaluations
                    WHERE primary_error IS NULL OR primary_error = ''
                """).fetchone()
                if _correct_row and _correct_row["cnt"] > 0:
                    error_distribution.insert(0, {
                        "error_type": "预测正确",
                        "count": _correct_row["cnt"]
                    })
            _obs_conn.close()
    except Exception:
        pass

    # Compute model health score (0-100) from latest daily_stats
    health_score = None
    if observation_daily_sorted:
        _latest = observation_daily_sorted[-1]
        _rhr = float(_latest.get("result_hit_rate", 0) or 0)
        _brier = float(_latest.get("brier_score_result", 0.25) or 0.25)
        _hc = float(_latest.get("high_confidence_hit_rate", 0) or 0)
        # Weighted: 50% result hit rate + 30% brier (inverted) + 20% high-conf
        _brier_component = max(0, (0.5 - _brier) / 0.5) * 100  # 0.0 brier = 100, 0.5+ = 0
        health_score = round(
            0.50 * _rhr + 0.30 * _brier_component + 0.20 * (_hc if _hc else _rhr), 1
        )
        health_score = max(0.0, min(100.0, health_score))

    payload["observation"] = {
        "daily_trends": observation_daily_sorted,
        "error_distribution": error_distribution,
        "health_score": health_score,
    }

    return payload




# 鈹€鈹€ Group Schedule Helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€




# 鈹€鈹€ Group Schedule Helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _load_match_ledger(root: Path, edition: str) -> dict:
    ledger = load_match_ledger(root, edition)
    matches = canonical_matches(ledger.get("matches", []) or [])
    matches = apply_prediction_items_to_matches(
        matches,
        _prediction_items_by_match(root, edition, include_local=True),
    )
    ledger["matches"] = materialize_actual_knockout_fixtures(matches, edition=edition)
    return ledger


def _canonical_ledger_matches(ledger: dict) -> list[dict]:
    return canonical_matches(ledger.get("matches", []) or [])


def _match_team_id(match: dict, side: str) -> str:
    team = match.get(f"{side}_team") or {}
    if isinstance(team, dict):
        return str(team.get("team_id") or "").lower()
    return ""


def _match_team_name(match: dict, side: str) -> str:
    team = match.get(f"{side}_team") or {}
    if isinstance(team, dict):
        tid = str(team.get("team_id") or "").lower()
        return TEAM_ZH.get(tid, team.get("name", "")) if tid else str(team.get("name", ""))
    return str(team or "")


def _actual_result_from_score(home_score, away_score) -> str:
    if home_score is None or away_score is None:
        return ""
    if home_score > away_score:
        return "home_win"
    if home_score < away_score:
        return "away_win"
    return "draw"


def _is_knockout_phase(phase: str) -> bool:
    return str(phase or "").strip().lower() in KNOCKOUT_PHASES


def _knockout_side_label(side: str, home_name: str, away_name: str) -> str:
    if side == "home":
        return home_name or "主队"
    if side == "away":
        return away_name or "客队"
    return ""


def _normalize_knockout_payload(data: dict | None, *, home_name: str = "", away_name: str = "") -> dict | None:
    data = data or {}
    if not data:
        return None

    regular = data.get("regular_time") or {}
    extra = data.get("extra_time") or {}
    penalties = data.get("penalties") or {}
    advance = data.get("advance") or {}

    def _section_scores(section: dict) -> tuple[object, object]:
        score = section.get("score") if isinstance(section.get("score"), dict) else {}
        home_score = score.get("home", section.get("home"))
        away_score = score.get("away", section.get("away"))
        return home_score, away_score

    winner_side = str(advance.get("winner") or penalties.get("winner") or "").strip()
    winner_name = str(advance.get("winner_name") or _knockout_side_label(winner_side, home_name, away_name))
    penalties_winner_side = str(penalties.get("winner") or "").strip()
    penalties_winner_name = str(penalties.get("winner_name") or _knockout_side_label(penalties_winner_side, home_name, away_name))
    regular_home, regular_away = _section_scores(regular)
    extra_home, extra_away = _section_scores(extra)
    penalties_home, penalties_away = _section_scores(penalties)

    return {
        "regular_time": {
            "score": _parse_score_pair({"home": regular_home, "away": regular_away}),
            "score_text": _score_text_from_pair(regular_home, regular_away),
            "result": regular.get("result") or _actual_result_from_score(regular_home, regular_away),
            "result_label": OUTCOME_LABELS.get(
                regular.get("result") or _actual_result_from_score(regular_home, regular_away),
                "",
            ),
        },
        "extra_time": {
            "played": bool(extra.get("played")),
            "score": _parse_score_pair({"home": extra_home, "away": extra_away}),
            "score_text": _score_text_from_pair(extra_home, extra_away),
            "result": extra.get("result") or _actual_result_from_score(extra_home, extra_away),
            "result_label": OUTCOME_LABELS.get(
                extra.get("result") or _actual_result_from_score(extra_home, extra_away),
                "",
            ),
        },
        "penalties": {
            "played": bool(penalties.get("played")),
            "score": _parse_score_pair({"home": penalties_home, "away": penalties_away}),
            "score_text": _score_text_from_pair(penalties_home, penalties_away),
            "winner": penalties_winner_side,
            "winner_name": _zh_name_from_label(penalties_winner_name),
        },
        "advance": {
            "winner": winner_side,
            "winner_name": _zh_name_from_label(winner_name),
            "winner_result": advance.get("winner_result") or ("home_advance" if winner_side == "home" else "away_advance" if winner_side == "away" else ""),
        },
    }


def _fallback_knockout_prediction(
    *,
    phase: str,
    predicted_result: str,
    predicted_score: dict[str, int | None],
    home_name: str,
    away_name: str,
) -> dict | None:
    if not _is_knockout_phase(phase):
        return None
    if predicted_score.get("home") is None or predicted_score.get("away") is None:
        return None
    advance_side = "home" if predicted_result == "home_win" else "away" if predicted_result == "away_win" else ""
    return _normalize_knockout_payload(
        {
            "regular_time": {
                "score": predicted_score,
                "result": predicted_result or _actual_result_from_score(predicted_score.get("home"), predicted_score.get("away")),
            },
            "extra_time": {
                "played": False,
                "score": {"home": None, "away": None},
                "result": "",
            },
            "penalties": {
                "played": False,
                "score": {"home": None, "away": None},
                "winner": "",
            },
            "advance": {
                "winner": advance_side,
                "winner_name": _knockout_side_label(advance_side, home_name, away_name),
            },
        },
        home_name=home_name,
        away_name=away_name,
    )


def _fallback_knockout_actual(
    *,
    phase: str,
    actual_score: dict[str, int | None],
    home_name: str,
    away_name: str,
) -> dict | None:
    if not _is_knockout_phase(phase):
        return None
    if actual_score.get("home") is None or actual_score.get("away") is None:
        return None
    result = _actual_result_from_score(actual_score.get("home"), actual_score.get("away"))
    advance_side = "home" if result == "home_win" else "away" if result == "away_win" else ""
    return _normalize_knockout_payload(
        {
            "regular_time": {
                "home": actual_score.get("home"),
                "away": actual_score.get("away"),
                "result": result,
            },
            "extra_time": {
                "played": False,
                "home": None,
                "away": None,
                "result": "",
            },
            "penalties": {
                "played": False,
                "home": None,
                "away": None,
                "winner": "",
            },
            "advance": {
                "winner": advance_side,
                "winner_name": _knockout_side_label(advance_side, home_name, away_name),
            },
        },
        home_name=home_name,
        away_name=away_name,
    )


def _fact_card_from_match(match: dict) -> dict:
    final_score = match.get("final_score") or {}
    actual_home = final_score.get("home") if isinstance(final_score, dict) else None
    actual_away = final_score.get("away") if isinstance(final_score, dict) else None
    is_completed = match.get("status") == "final" and actual_home is not None and actual_away is not None
    actual_result = _actual_result_from_score(actual_home, actual_away)
    home_id = _match_team_id(match, "home")
    away_id = _match_team_id(match, "away")

    # Determine data source from match status
    match_status = str(match.get("status") or "")
    if match_status == "knockout_placeholder_until_teams_known":
        data_source = "placeholder"
        data_source_label = "占位"
    else:
        data_source = "official"
        data_source_label = ""

    return {
        "match_id": match.get("match_id", ""),
        "date": str(match.get("kickoff_at", ""))[:10],
        "kickoff_at": match.get("kickoff_at", ""),
        "local_kickoff_at": match.get("kickoff_at", ""),
        "calculation_timezone": "LocalTime",
        "venue": match.get("venue", ""),
        "group": match.get("group", ""),
        "phase": match.get("phase", ""),
        # Data source identification
        "data_source": data_source,
        "data_source_label": data_source_label,
        "home_name": _match_team_name(match, "home"),
        "away_name": _match_team_name(match, "away"),
        "home_id": home_id,
        "away_id": away_id,
        "prediction_status": "not_predicted",
        "prediction_origin": "none",
        "prediction_source": "none",
        "prediction_source_path": "",
        "data_origin": "public_facts",
        "predicted_result": "",
        "predicted_result_label": "未预测",
        "score_text": "-:-",
        "total_goals": "-",
        "confidence": "none",
        "confidence_label": "NONE",
        "expected_goals_proxy": None,
        "clean_sheet_probability": None,
        "scoreline_distribution": None,
        "result_confidence": "none",
        "score_confidence": "none",
        "total_goals_confidence": "none",
        "confidence_note": "No local locked prediction found; showing public match facts only.",
        "venue_adaptation_context": None,
        "referee_analysis": None,
        "play_card": {},
        "divination_hexagram": "",
        "evaluation_status": "actual_only" if is_completed else "fixture_only",
        "evaluation_label": "未预测",
        "hit_class": "not-predicted",
        "result_hit": None,
        "score_hit": None,
        "home_colors": "",
        "away_colors": "",
        "home_ranking": None,
        "away_ranking": None,
        "evidence_gaps": ["no local prediction"],
        "play_title": "",
        "risk_flags": [],
        "watch_points": [],
        "primary_error": "",
        "has_odds": False,
        "market_odds": None,
        "market_odds_status": {"status": "missing", "source": "missing", "is_mock": False, "reason": ""},
        "market_odds_source": "missing",
        "market_odds_is_mock": False,
        "has_referee": False,
        "has_news": False,
        "analysis_layers": [],
        "home_radar": {"attack": 0, "defense": 0, "midfield": 0, "fitness": 0, "recent_form": 0},
        "away_radar": {"attack": 0, "defense": 0, "midfield": 0, "fitness": 0, "recent_form": 0},
        "home_form": [],
        "away_form": [],
        "h2h": [],
        "home_players": [],
        "away_players": [],
        "home_injuries": [],
        "away_injuries": [],
        "home_suspensions": [],
        "away_suspensions": [],
        "late_news": [],
        "actual_score_home": actual_home,
        "actual_score_away": actual_away,
        "actual_result": actual_result,
        "knockout_actual": _normalize_knockout_payload(
            match.get("knockout_result"),
            home_name=_match_team_name(match, "home"),
            away_name=_match_team_name(match, "away"),
        ),
        "is_completed": is_completed,
    }


def _build_tournament_schedule(ledger: dict) -> dict:
    schedule = {
        "group": {},
        "round_of_32": [],
        "round_of_16": [],
        "quarter_final": [],
        "semi_final": [],
        "final": [],
        "standings": {}
    }

    # Load group standings if available
    standings_path = Path("wiki/public/2026/group-standings.json")
    if standings_path.exists():
        try:
            standings_data = load_json(standings_path)
            schedule["standings"] = standings_data.get("groups", {})
        except Exception:
            pass

    for m in _canonical_ledger_matches(ledger):
        phase = m.get("phase", "")
        home = m.get("home_team", {}) or {}
        away = m.get("away_team", {}) or {}
        final_score = m.get("final_score") or {}
        status = m.get("status", "")

        if isinstance(home, dict):
            h_id = home.get("team_id", "") or ""
            h_name = TEAM_ZH.get(h_id.lower(), home.get("name", "")) if h_id else home.get("name", "")
        else:
            h_id = ""
            h_name = str(home or "")

        if isinstance(away, dict):
            a_id = away.get("team_id", "") or ""
            a_name = TEAM_ZH.get(a_id.lower(), away.get("name", "")) if a_id else away.get("name", "")
        else:
            a_id = ""
            a_name = str(away or "")

        # Beijing time for schedule
        kickoff_utc = m.get("kickoff_at", "")
        beijing_time_short = ""
        if kickoff_utc and "T" in str(kickoff_utc):
            try:
                _bjt = timezone(timedelta(hours=8))
                utc_s = str(kickoff_utc).replace("Z", "+00:00")
                dt = datetime.fromisoformat(utc_s).astimezone(_bjt)
                beijing_time_short = dt.strftime("%m/%d %H:%M")
            except Exception:
                beijing_time_short = str(kickoff_utc)[:16].replace("T", " ")
        else:
            beijing_time_short = str(kickoff_utc)[:16].replace("T", " ") if kickoff_utc else ""

        match_data = {
            "match_id": m.get("match_id", ""),
            "match_number": m.get("match_number", 0),
            "phase": phase,
            "kickoff_at": kickoff_utc,
            "beijing_time_short": beijing_time_short,
            "venue": m.get("venue", ""),
            "home_name": h_name,
            "away_name": a_name,
            "home_id": h_id,
            "away_id": a_id,
            "status": status,
            "score_home": final_score.get("home") if status == "final" else None,
            "score_away": final_score.get("away") if status == "final" else None,
            "knockout_result": _normalize_knockout_payload(
                m.get("knockout_result"),
                home_name=h_name,
                away_name=a_name,
            ),
            "evaluation": m.get("evaluation") or {},
        }

        if phase == "group":
            g = m.get("group", "")
            if g:
                schedule["group"].setdefault(g, []).append(match_data)
        elif phase == "round_of_32":
            schedule["round_of_32"].append(match_data)
        elif phase == "round_of_16":
            schedule["round_of_16"].append(match_data)
        elif phase == "quarter_final":
            schedule["quarter_final"].append(match_data)
        elif phase == "semi_final":
            schedule["semi_final"].append(match_data)
        elif phase in ("third_place", "final"):
            schedule["final"].append(match_data)

    # Sort group matches by kickoff time
    for g in schedule["group"]:
        schedule["group"][g].sort(key=lambda x: x.get("kickoff_at", ""))
    schedule["group"] = dict(sorted(schedule["group"].items()))

    # Sort knockout matches by kickoff time
    for phase_key in ["round_of_32", "round_of_16", "quarter_final", "semi_final", "final"]:
        schedule[phase_key].sort(key=lambda x: x.get("kickoff_at", ""))

    return schedule



# 鈹€鈹€ Small Rendering Helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _render_metric(label: str, value: str, detail: str = "") -> str:
    return (
        '<div class="metric">'
        f'<span class="metric-label">{_safe(label)}</span>'
        f'<span class="metric-value">{_safe(value)}</span>'
        f'<span class="metric-detail">{_safe(detail)}</span>'
        '</div>'
    )


def _render_match_card(card: dict) -> str:
    fixture = card.get("fixture") or {}
    prediction = card.get("prediction") or {}
    actual = card.get("actual") or {}
    evaluation = card.get("evaluation") or {}
    display_state = card.get("display_state") or _card_display_state(card)

    confidence = str(prediction.get("confidence") or card.get("confidence", "unknown"))
    hit_class = evaluation.get("hit_class") or card.get("hit_class", "pending")
    if hit_class == "hit":
        hit_class = "double-hit"
    has_prediction = bool(prediction.get("exists"))
    has_actual = bool(actual.get("exists"))
    is_not_predicted = not has_prediction

    result_map = {
        "home_win": ("主胜", "outcome-home"),
        "away_win": ("客胜", "outcome-away"),
        "draw": ("平局", "outcome-draw"),
    }
    actual_result = actual.get("result") or card.get("actual_result", "")
    score_text = prediction.get("score_text") or card.get("score_text", "-:-")

    h_rank = fixture.get("home_ranking", card.get("home_ranking"))
    a_rank = fixture.get("away_ranking", card.get("away_ranking"))
    h_rank_html = f'<span class="rank-tag">#{h_rank}</span>' if h_rank else ""
    a_rank_html = f'<span class="rank-tag">#{a_rank}</span>' if a_rank else ""

    # Use Beijing time
    date_str = fixture.get("beijing_date") or card.get("beijing_date", "") or str(card.get("kickoff_at", ""))[:10]
    time_str = fixture.get("beijing_time") or card.get("beijing_time", "")

    match_id = fixture.get("match_id") or card.get("match_id", "")
    group = fixture.get("group") or card.get("group", "")
    phase = fixture.get("phase") or card.get("phase", "")
    venue = fixture.get("venue") or card.get("venue", "")
    is_knockout = _is_knockout_phase(phase)
    knockout_prediction = prediction.get("knockout") or {}
    knockout_actual = actual.get("knockout") or {}

    xg = prediction.get("expected_goals_proxy", card.get("expected_goals_proxy"))
    xg_html = ""
    if has_prediction and xg and isinstance(xg, dict):
        h_xg = xg.get("home")
        a_xg = xg.get("away")
        if h_xg is not None and a_xg is not None:
            xg_html = (
                f'<div class="inline-metric metric-xg">'
                f'<span>xG</span>'
                f'<strong>{h_xg:.1f} vs {a_xg:.1f}</strong>'
                f'</div>'
            )

    cs = prediction.get("clean_sheet_probability", card.get("clean_sheet_probability"))
    cs_html = ""
    if has_prediction and cs and isinstance(cs, dict):
        h_cs = cs.get("home")
        a_cs = cs.get("away")
        if h_cs is not None and a_cs is not None:
            cs_html = (
                f'<div class="inline-metric metric-clean">'
                f'<span>零封</span>'
                f'<strong>{h_cs:.0%} / {a_cs:.0%}</strong>'
                f'</div>'
            )

    dist_html = ""
    score_dist = prediction.get("scoreline_distribution", card.get("scoreline_distribution"))
    if has_prediction and score_dist and isinstance(score_dist, list):
        sorted_dist = sorted(score_dist, key=lambda x: x.get("probability", 0.0), reverse=True)
        top_3 = sorted_dist[:3]
        if top_3:
            items_html = []
            for item in top_3:
                sc = item.get("score") or {}
                prob = item.get("probability", 0.0)
                if sc and "home" in sc and "away" in sc:
                    score_str = f"{sc['home']}-{sc['away']}"
                    reason = item.get("reason", "")
                    items_html.append(
                        f'<span class="dist-item" title="{_safe(reason)}">'
                        f'<strong class="dist-score">{_safe(score_str)}</strong>'
                        f'<span class="dist-prob">{prob:.1%}</span>'
                        f'</span>'
                    )
            if items_html:
                dist_html = (
                    f'<div class="scoreline-dist-row">'
                    f'<span class="section-label">预测比分概率 Top 3</span>'
                    f'<div class="dist-list">{"".join(items_html)}</div>'
                    f'</div>'
                )

    div_overlay = card.get("divination_overlay")
    div_html = ""
    if has_prediction and div_overlay and isinstance(div_overlay, dict):
        # Get hexagram name from overlay (preferred) or top-level field
        hex_label = (
            div_overlay.get("hexagram_name")
            or div_overlay.get("hexagram")
            or card.get("divination_hexagram")
            or ""
        )
        # Clean up: if it looks like a shichen/time pattern, show "未起卦"
        _shichen_kw = ["时(", "时（", "时 ", "周期"]
        if not hex_label or any(kw in hex_label for kw in _shichen_kw):
            hex_label = "未起卦"

        if hex_label:
            # Build match-specific hexagram display
            hex_interp = div_overlay.get("hexagram_interpretation") or ""
            match_interp = div_overlay.get("match_interpretation") or ""
            home_fortune = div_overlay.get("home_fortune") or ""
            away_fortune = div_overlay.get("away_fortune") or ""
            fortune_summary = div_overlay.get("fortune_summary") or ""

            # Hexagram name chip
            parts = [f'<span class="div-chip div-hex">卦象 {_safe(hex_label)}</span>']

            # Fortune summary chip (e.g., "利主队" / "利客队" / "势均力敌")
            if fortune_summary:
                parts.append(f'<span class="div-chip div-fortune-summary">{_safe(fortune_summary)}</span>')

            # Home/Away fortune level chips
            home_name = card.get("home_name", "") or "主"
            away_name = card.get("away_name", "") or "客"
            if home_fortune:
                fortune_class = _fortune_css_class(home_fortune)
                parts.append(f'<span class="div-chip {fortune_class}>{home_name}:{_safe(home_fortune)}</span>')
            if away_fortune:
                fortune_class = _fortune_css_class(away_fortune)
                parts.append(f'<span class="div-chip {fortune_class}">{away_name}:{_safe(away_fortune)}</span>')

            # Classic interpretation (collapsed)
            interp_html = f'<span class="div-chip div-hex-interp" title="{_safe(hex_interp)}">卦辞 {_safe(hex_interp[:12])}{"..." if len(hex_interp) > 12 else ""}</span>' if hex_interp else ""

            div_html = (
                f'<div class="divination-summary-row">'
                f'{"".join(parts)}'
                f'{interp_html}'
                f'</div>'
            )

            # Match-specific narrative (below the chips)
            if match_interp:
                div_html += (
                    f'<div class="div-match-narrative">'
                    f'{_safe(match_interp)}'
                    f'</div>'
                )

    watch_points = prediction.get("watch_points") or card.get("watch_points") or []
    watch_html = ""
    if has_prediction and watch_points:
        items = "".join(f"<li>{_safe(wp)}</li>" for wp in watch_points[:3])
        watch_html = (
            '<div class="watchpoints">'
            '<span class="section-label">本场看点</span>'
            f'<ul>{items}</ul>'
            '</div>'
        )

    eval_badge = ""
    if hit_class == "double-hit":
        eval_badge = '<span class="eval-badge eval-double">双中</span>'
    elif hit_class == "result-hit":
        eval_badge = '<span class="eval-badge eval-result">中赛果</span>'
    elif hit_class == "miss":
        eval_badge = '<span class="eval-badge eval-miss">偏差</span>'

    # Keep the original card layout: upcoming predicted matches use the large score slot,
    # completed predicted matches use the two-row comparison block.
    compare_html = ""
    if has_actual and has_prediction and is_knockout and knockout_prediction:
        rows = []
        pred_regular = (knockout_prediction.get("regular_time") or {}).get("score_text") or score_text
        act_regular = (knockout_actual.get("regular_time") or {}).get("score_text") or actual.get("score_text") or "-:-"
        rows.append(
            f'<div class="compare-row"><span class="compare-label">90分钟</span><span class="compare-score">{_safe(pred_regular)}</span><span class="compare-score compare-actual">{_safe(act_regular)}</span></div>'
        )

        pred_extra = knockout_prediction.get("extra_time") or {}
        act_extra = knockout_actual.get("extra_time") or {}
        pred_extra_text = pred_extra.get("score_text") if pred_extra.get("played") else "否"
        act_extra_text = act_extra.get("score_text") if act_extra.get("played") else "否"
        rows.append(
            f'<div class="compare-row"><span class="compare-label">加时</span><span class="compare-score">{_safe(pred_extra_text or "否")}</span><span class="compare-score compare-actual">{_safe(act_extra_text or "否")}</span></div>'
        )

        pred_pen = knockout_prediction.get("penalties") or {}
        act_pen = knockout_actual.get("penalties") or {}
        pred_pen_text = pred_pen.get("score_text") if pred_pen.get("played") else "否"
        act_pen_text = act_pen.get("score_text") if act_pen.get("played") else "否"
        rows.append(
            f'<div class="compare-row"><span class="compare-label">点球</span><span class="compare-score">{_safe(pred_pen_text or "否")}</span><span class="compare-score compare-actual">{_safe(act_pen_text or "否")}</span></div>'
        )

        pred_adv = (knockout_prediction.get("advance") or {}).get("winner_name") or "待定"
        act_adv = (knockout_actual.get("advance") or {}).get("winner_name") or "待定"
        rows.append(
            f'<div class="compare-row"><span class="compare-label">晋级</span><span class="compare-score">{_safe(pred_adv)}</span><span class="compare-score compare-actual">{_safe(act_adv)}</span></div>'
        )
        compare_html = (
            f'<div class="score-compare score-compare-knockout">'
            f'<div class="compare-head"><span>项目</span><span>预测</span><span>实际</span></div>'
            f'{"".join(rows)}'
            f'</div>'
        )
    elif has_actual and has_prediction:
        ah = actual.get("score", {}).get("home", card.get("actual_score_home", "?"))
        aa = actual.get("score", {}).get("away", card.get("actual_score_away", "?"))
        actual_text = f"{ah}-{aa}"
        compare_html = (
            f'<div class="score-compare">'
            f'<div class="compare-head"><span>项目</span><span>预测</span><span>实际</span></div>'
            f'<div class="compare-row">'
            f'<span class="compare-label">90分钟</span>'
            f'<span class="compare-score">{_safe(score_text)}</span>'
            f'<span class="compare-score compare-actual">{_safe(actual_text)}</span>'
            f'</div>'
            f'</div>'
        )

    if display_state == "actual_only":
        if is_knockout and has_actual and knockout_actual:
            act_adv = (knockout_actual.get("advance") or {}).get("winner_name") or "待定"
            act_regular = (knockout_actual.get("regular_time") or {}).get("score_text") or actual.get("score_text") or "-:-"
            act_extra = knockout_actual.get("extra_time") or {}
            act_pen = knockout_actual.get("penalties") or {}
            score_display_html = (
                f'<div class="ko-summary">'
                f'<div class="ko-summary-pill"><b>实际90分</b><span>{_safe(act_regular)}</span></div>'
                f'<div class="ko-summary-pill"><b>实际加时</b><span>{_safe(act_extra.get("score_text") or ("否" if not act_extra.get("played") else "-:-"))}</span></div>'
                f'<div class="ko-summary-pill"><b>实际点球</b><span>{_safe(act_pen.get("score_text") or ("否" if not act_pen.get("played") else "-:-"))}</span></div>'
                f'<div class="ko-summary-pill"><b>实际晋级</b><span>{_safe(act_adv)}</span></div>'
                f'</div>'
            )
        else:
            actual_text = actual.get("score_text") or _score_text_from_pair(actual_home, actual_away) or "-:-"
            score_display_html = f'<div class="mc-score">实际 { _safe(actual_text) }</div>'
        score_display = ""
    elif display_state in {"fixture", "placeholder"} and not (is_knockout and has_prediction and knockout_prediction):
        score_display = "待预测"
    elif is_knockout and has_prediction and knockout_prediction:
        pred_adv = (knockout_prediction.get("advance") or {}).get("winner_name") or "待定"
        pred_regular = (knockout_prediction.get("regular_time") or {}).get("score_text") or score_text
        pred_extra = knockout_prediction.get("extra_time") or {}
        pred_pen = knockout_prediction.get("penalties") or {}
        score_display_html = (
            f'<div class="ko-summary">'
            f'<div class="ko-summary-pill"><b>90分钟</b><span>{_safe(pred_regular)}</span></div>'
            f'<div class="ko-summary-pill"><b>加时</b><span>{"是" if pred_extra.get("played") else "否"}</span></div>'
            f'<div class="ko-summary-pill"><b>点球</b><span>{"是" if pred_pen.get("played") else "否"}</span></div>'
            f'<div class="ko-summary-pill"><b>晋级</b><span>{_safe(pred_adv)}</span></div>'
            f'</div>'
        )
        score_display = ""
    else:
        score_display = score_text
    if not (is_knockout and has_prediction and knockout_prediction and not has_actual):
        score_display_html = compare_html if compare_html else f'<div class="mc-score">{_safe(score_display)}</div>'

    result_html = ""
    if has_prediction and has_actual:
        result_text, result_cls = result_map.get(actual_result, ("", "outcome-none"))
        if result_text:
            result_html = f'<div class="mc-result"><span class="outcome-badge {_safe(result_cls)}">真实结果 { _safe(result_text) }</span></div>'

    # Data source badge (placeholder vs real)
    data_source = card.get("data_source", "official")
    data_label = card.get("data_source_label", "")
    source_badge = ""
    if data_source == "placeholder" and data_label:
        source_badge = f'<span class="source-badge source-placeholder" title="淘汰赛队伍待确认，数据为占位">{_safe(data_label)}</span>'
    elif data_source == "official":
        phase_val = str(card.get("phase") or "")
        if phase_val.startswith("group"):
            source_badge = '<span class="source-badge source-official" title="官方赛程">小组赛</span>'
        elif phase_val and phase_val != "unknown":
            source_badge = f'<span class="source-badge source-official" title="官方赛程">{_safe(PHASE_LABELS.get(phase_val, phase_val))}</span>'

    group_badge = f'<span class="mc-group">{_safe(group)}组</span>' if group else ""
    time_html = f'<div class="mc-time">{_safe(date_str)} {_safe(time_str)}</div>' if (date_str or time_str) else ""

    return (
        f'<article class="match-card{" match-card-placeholder" if data_source == "placeholder" else ""}" data-hit="{_safe(hit_class)}" data-match-id="{_safe(match_id)}" '
        f'data-date="{_safe(date_str)}" data-phase="{_safe(phase)}" data-source="{_safe(data_source)}">'
        f'<div class="mc-top">'
        f'<div class="mc-meta"><div class="mc-meta-row"><span class="mc-id">{_safe(match_id)}</span>{group_badge}</div>{time_html}</div>'
        f'<div class="mc-badges">{eval_badge}{source_badge}</div>'
        f'</div>'
        f'<div class="mc-body">'
        f'<div class="mc-team">'
        f'<span class="team-name">{_safe(card.get("home_name"))}{h_rank_html}</span>'
        f'<span class="mc-vs">vs</span>'
        f'<span class="team-name">{_safe(card.get("away_name"))}{a_rank_html}</span>'
        f'</div>'
        f'{score_display_html}'
        f'{result_html}'
        f'</div>'
        f'<div class="mc-bottom">'
        f'<div class="conf-row"><span class="conf-label">置信度</span>'
        f'<div class="conf-track"><div class="conf-fill conf-{_safe(confidence)}"></div></div>'
        f'<span class="conf-val">{_safe(confidence.upper())}</span></div>'
        f'<div class="meta-row">'
        f'{xg_html}{cs_html}'
        f'<div class="inline-metric metric-venue"><span>场馆</span><strong>{_safe(venue)}</strong></div>'
        f'</div>'
        f'{div_html}'
        f'{dist_html}'
        f'{watch_html}'
        f'</div>'
        f'</article>'
    )


def _render_match_card_from_state(card: dict) -> str:
    return _render_match_card(card)


def _render_match_schedule_row(m: dict, predictions: dict) -> str:
    mid = m["match_id"]
    pred = predictions.get(mid, {})
    pred_result = pred.get("predicted_result", "")
    pred_score = pred.get("score", {}) or {}
    pred_knockout = pred.get("knockout") or {}
    home_name = str(m.get("home_name") or "")
    away_name = str(m.get("away_name") or "")
    unresolved_knockout_slot = bool(
        re.match(r"^[WL]\d{2,3}$", home_name.upper())
        or re.match(r"^[WL]\d{2,3}$", away_name.upper())
    )
    result_map = {
        "home_win": "主胜",
        "away_win": "客胜",
        "draw": "平局",
    }
    result_label = result_map.get(pred_result, "")
    score_label = ""
    if pred_score.get("home") is not None:
        score_label = f'{pred_score["home"]}-{pred_score["away"]}'

    played = m["status"] == "final"
    row_cls = "match-played" if played else ""
    is_knockout = _is_knockout_phase(m.get("phase", ""))

    # Actual score
    actual_score = ""
    if played and m.get("score_home") is not None:
        actual_score = f'{m["score_home"]}-{m["score_away"]}'
    actual_knockout = m.get("knockout_result") or {}

    # Evaluation data
    ev = m.get("evaluation") or {}

    # Prediction cell
    if unresolved_knockout_slot:
        pred_val_html = '<span class="sch-tag sch-tag-none">待预测</span>'
    elif pred_result and is_knockout and pred_knockout:
        pred_regular = (pred_knockout.get("regular_time") or {}).get("score_text") or score_label or "-:-"
        pred_adv = (pred_knockout.get("advance") or {}).get("winner_name") or "待定"
        pred_val_html = (
            f'<span class="sch-tag sch-tag-pred">预测90分: {pred_regular}</span>'
            f'<span class="sch-tag sch-tag-pred">预测晋级: {pred_adv}</span>'
        )
    elif pred_result:
        pred_val_html = f'<span class="sch-tag sch-tag-pred">预测: {score_label or "-:-"}</span>'
    else:
        pred_val_html = '<span class="sch-tag sch-tag-none">待预测</span>'

    # Actual cell
    actual_val_html = ""
    actual_result_html = ""
    if played and is_knockout and actual_knockout:
        actual_regular = (actual_knockout.get("regular_time") or {}).get("score_text") or actual_score
        actual_advance = (actual_knockout.get("advance") or {}).get("winner_name") or "待定"
        actual_extra = actual_knockout.get("extra_time") or {}
        actual_pen = actual_knockout.get("penalties") or {}
        parts = [f'<span class="sch-tag sch-tag-actual">实际90分: {actual_regular}</span>']
        if actual_extra.get("played"):
            parts.append(
                f'<span class="sch-tag sch-tag-actual">实际加时: {actual_extra.get("score_text") or "-:-"}</span>'
            )
        if actual_pen.get("played"):
            parts.append(
                f'<span class="sch-tag sch-tag-actual">实际点球: {actual_pen.get("score_text") or "-:-"}</span>'
            )
        parts.append(f'<span class="sch-tag sch-tag-truth">实际晋级: {actual_advance}</span>')
        actual_val_html = "".join(parts)
    elif played and actual_score and pred_result:
        actual_result = ev.get("actual_result", "")
        actual_label = result_map.get(actual_result, "")
        actual_val_html = f'<span class="sch-tag sch-tag-actual">实际: {actual_score}</span>'
        if actual_label:
            actual_result_html = f'<span class="sch-tag sch-tag-truth">真实结果: {actual_label}</span>'
    elif pred_result and not unresolved_knockout_slot:
        actual_val_html = '<span class="sch-tag sch-tag-pending">未开赛</span>'

    kickoff_short = m.get("beijing_time_short", "")
    if not kickoff_short:
        kickoff_short = str(m.get("kickoff_at", ""))
        if "T" in kickoff_short:
            kickoff_short = kickoff_short.split("T")[1][:5]

    return (
        f'<div class="sch-match {_safe(row_cls)}">'
        f'<div class="sch-row-top">'
        f'<span class="sch-teams">{_safe(m["home_name"])} vs {_safe(m["away_name"])}</span>'
        f'<span class="sch-time">{_safe(kickoff_short)}</span>'
        f'</div>'
        f'<div class="sch-row-bottom">'
        f'{pred_val_html}'
        f'{actual_val_html}'
        f'{actual_result_html}'
        f'</div>'
        f'</div>'
    )


def _render_knockout_stage(matches: list[dict], predictions: dict, title: str) -> str:
    if not matches:
        return f'<p class="empty-state">暂无{title}赛程数据。</p>'
    pred_rows = "".join(_render_match_schedule_row(m, predictions) for m in matches)
    return f'<div class="ko-grid">{pred_rows}</div>'


def _render_tournament_schedule(schedule_data: dict, predictions: dict) -> str:
    if not schedule_data:
        return '<p class="empty-state">暂无赛程数据。</p>'

    group_data = schedule_data.get("group", {})

    # Load group standings if available
    standings_by_group = schedule_data.get("standings", {})

    # 1. Group Stage HTML
    group_parts = ['<div class="schedule-grid">']
    for g_letter, matches in sorted(group_data.items()):
        teams_set: list[str] = []
        for m in matches:
            for name in (m["home_name"], m["away_name"]):
                if name and name not in teams_set:
                    teams_set.append(name)

        pred_rows = ""
        for m in matches:
            pred_rows += _render_match_schedule_row(m, predictions)

        # Render teams with status badges
        teams_html = ""
        group_standings = standings_by_group.get(g_letter, [])
        standings_map = {s["team"]: s for s in group_standings}

        for team in teams_set[:4]:
            team_info = standings_map.get(team, {})
            status = team_info.get("status", "pending")
            badge = ""
            if status == "qualified":
                badge = '<span class="team-status qualified">✅</span>'
            elif status == "eliminated":
                badge = '<span class="team-status eliminated">❌</span>'
            teams_html += f'<span class="team-item">{_safe(team)}{badge}</span>'

        group_parts.append(
            f'<div class="group-block">'
            f'<div class="gb-header">'
            f'<span class="gb-letter">{_safe(g_letter)}</span>'
            f'<span class="gb-phase">小组赛</span>'
            f'</div>'
            f'<div class="gb-teams">{teams_html}</div>'
            f'<div class="gb-matches">{pred_rows}</div>'
            f'</div>'
        )
    group_parts.append('</div>')
    group_html = "".join(group_parts)

    knockout_tabs = [
        ("r32", "round_of_32", PHASE_LABELS["round_of_32"]),
        ("r16", "round_of_16", PHASE_LABELS["round_of_16"]),
        ("qf", "quarter_final", PHASE_LABELS["quarter_final"]),
    ]
    subtab_buttons = [
        '<button class="subtab active" data-subtab="group">小组赛</button>'
    ]
    subtab_panels = [
        f'<div id="sch-group" class="schedule-subpanel active">{group_html}</div>'
    ]
    for tab_id, phase_key, phase_label in knockout_tabs:
        phase_html = _render_knockout_stage(schedule_data.get(phase_key, []), predictions, phase_label)
        subtab_buttons.append(
            f'<button class="subtab" data-subtab="{tab_id}">{_safe(phase_label)}</button>'
        )
        subtab_panels.append(
            f'<div id="sch-{tab_id}" class="schedule-subpanel">{phase_html}</div>'
        )

    return (
        '<div class="schedule-subtabs">'
        + "".join(subtab_buttons)
        + '</div>'
        + "".join(subtab_panels)
    )


# 鈹€鈹€ ECharts & Stats 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _render_action_item(act: dict) -> str:
    priority = _safe(act.get("priority", "P2"))
    return (
        f'<div class="action-item">'
        f'<span class="badge badge-{priority.lower()}">{priority}</span>'
        f'<span class="action-desc">{_safe(act.get("description", ""))}</span>'
        f'<span class="action-time">{_safe(act.get("created_at", ""))[:16].replace("T", " ")}</span>'
        f'</div>'
    )


def _render_issue_tag(tag: dict) -> str:
    severity = _safe(tag.get("severity", "medium"))
    count = int(tag.get("total_occurrences") or tag.get("occurrence_count", 1))
    return (
        f'<div class="issue-item">'
        f'<div class="issue-info">'
        f'<span class="badge badge-{severity.lower()}">{severity}</span>'
        f'<span class="issue-tag">{_safe(tag.get("tag", ""))}</span>'
        f'</div>'
        f'<span class="issue-count">{count} 次出现</span>'
        f'</div>'
    )


def _render_daily_stat(stat: dict) -> str:
    date = _safe(stat.get("stat_date", ""))
    evaluated = int(stat.get("matches_evaluated", 0))
    hits = int(stat.get("result_hits", 0))
    score_hits = int(stat.get("score_hits", 0))
    brier = stat.get("brier_score_result")
    brier_text = f"{brier:.4f}" if brier is not None else "N/A"
    top_error = _safe(stat.get("top_error", ""))
    error_html = f'<div class="stat-error">{top_error}</div>' if top_error else ""
    return (
        f'<div class="stat-card">'
        f'<div class="stat-head"><h3>{date}</h3><span>Brier: {brier_text}</span></div>'
        f'<div class="stat-body">'
        f'<span>评估 {evaluated} 场</span>'
        f'<span>赛果 {hits} ({_pct(_rate(hits, evaluated))})</span>'
        f'<span>比分 {score_hits} ({_pct(_rate(score_hits, evaluated))})</span>'
        f'</div>'
        f'{error_html}'
        f'</div>'
    )


def _render_phase_stat_card(stat: dict) -> str:
    label = _safe(stat.get("label", ""))
    total = int(stat.get("total_matches", 0))
    predicted = int(stat.get("predicted_matches", 0))
    actual = int(stat.get("actual_matches", 0))
    evaluated = int(stat.get("evaluated_matches", 0))
    result_hits = int(stat.get("result_hits", 0))
    score_hits = int(stat.get("score_hits", 0))
    result_rate = _pct(float(stat.get("result_hit_rate", 0)))
    score_rate = _pct(float(stat.get("score_hit_rate", 0)))
    return (
        '<div class="phase-stat-card">'
        f'<div class="phase-stat-head"><h3>{label}</h3><span>{total} 场</span></div>'
        '<div class="phase-stat-grid">'
        f'<span>已预测 {predicted}</span>'
        f'<span>已完赛 {actual}</span>'
        f'<span>已评估 {evaluated}</span>'
        f'<span>赛果 {result_hits} ({result_rate})</span>'
        f'<span>比分 {score_hits} ({score_rate})</span>'
        '</div>'
        '</div>'
    )


def _render_stats_card_grid(stats: list[dict]) -> str:
    cards = "".join(_render_phase_stat_card(stat) for stat in stats)
    return cards or '<p class="empty-state">暂无统计数据。</p>'


# 鈹€鈹€ Main HTML Renderer 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _card_display_date(card: dict) -> str:
    return str(card.get("beijing_date") or card.get("date") or str(card.get("kickoff_at", ""))[:10] or "")


def _card_display_time(card: dict) -> str:
    return str(card.get("beijing_time") or "")


def _card_chrono_key(card: dict) -> tuple[str, str, str, str]:
    return (
        _card_display_date(card),
        _card_display_time(card),
        str(card.get("kickoff_at") or ""),
        str(card.get("match_id") or ""),
    )


def _default_matchday_date(payload: dict, dates: list[str]) -> str:
    if not dates:
        return "all"
    cards = payload.get("cards", []) or []
    completed_dates = sorted({
        _card_display_date(card)
        for card in cards
        if card.get("data_source") != "placeholder"
        and _card_display_date(card)
        and (card.get("actual", {}) or {}).get("exists")
    })
    generated_date = str(payload.get("generated_at", ""))[:10]
    if generated_date and completed_dates:
        past_completed = [date for date in completed_dates if date <= generated_date]
        if past_completed:
            return past_completed[-1]
    if completed_dates:
        return completed_dates[-1]
    if generated_date in dates:
        return generated_date
    if generated_date:
        for date in dates:
            if date >= generated_date:
                return date
    return dates[0]


def _default_visible_dates(payload: dict, dates: list[str]) -> list[str]:
    if not dates:
        return []
    active_date = _default_matchday_date(payload, dates)
    if active_date == "all":
        return ["all"]
    if active_date in dates:
        active_idx = dates.index(active_date)
        return dates[max(0, active_idx - 1):active_idx + 1]
    return [dates[0]]


def _date_filter_button(date: str, active_date: str) -> str:
    active_attr = ' class="active"' if date == active_date else ""
    return f'<button{active_attr} data-filter-date="{_safe(date)}">{_safe(date)}</button>'


def _decorate_dashboard_payload_for_frontend(payload: dict) -> dict:
    summary = payload.get("summary", {}) or {}

    pred_index: dict[str, dict] = {}
    for c in payload.get("cards", []):
        mid = c.get("match_id", "")
        if mid:
            score_text = c.get("score_text", "") or ""
            parts = score_text.split("-")
            pred_index[mid] = {
                "predicted_result": c.get("predicted_result", ""),
                "score": {
                    "home": parts[0] if len(parts) > 1 else None,
                    "away": parts[-1] if len(parts) > 1 else None,
                },
                "knockout": ((c.get("prediction") or {}).get("knockout") or c.get("knockout_prediction")),
            }

    schedule_data = payload.get("schedule_data", {})
    schedule_html = _render_tournament_schedule(schedule_data, pred_index)

    sorted_cards = sorted(payload.get("cards", []), key=_card_chrono_key)
    cards_html = "".join(_render_match_card(c) for c in sorted_cards) or '<p class="empty-state">暂无预测数据报告。</p>'

    all_cards = payload.get("cards", [])
    real_cards = [c for c in all_cards if c.get("data_source") != "placeholder"]
    dates = sorted(set(_card_display_date(c) for c in real_cards if _card_display_date(c)))
    default_date = _default_matchday_date(payload, dates)
    default_visible_dates = _default_visible_dates(payload, dates)
    date_btns = '<button{} data-filter-date="all">全部</button>'.format(' class="active"' if default_date == "all" else "")
    date_btns += "".join(_date_filter_button(d, default_date) for d in dates)

    evaluated = int(summary.get("evaluated_matches", 0))
    brier = summary.get("avg_brier_score", 0.0)
    brier_text = f"{brier:.4f}" if brier > 0 else "N/A"
    predictions_count = summary.get("predictions", 0)
    placeholder_count = summary.get("placeholder_count", 0)

    metrics_html = (
        _render_metric("真实预测", str(predictions_count), "场")
        + _render_metric("完成复盘", str(evaluated), "场")
        + _render_metric("赛果命中", _pct(float(summary.get("result_hit_rate", 0))), "胜平负")
        + _render_metric("比分命中", _pct(float(summary.get("score_hit_rate", 0))), "精确比分")
        + _render_metric("Brier", brier_text, "校准度")
    )
    if placeholder_count > 0:
        metrics_html += (
            '<div class="metric metric-placeholder">'
            '<span class="metric-label">淘汰赛占位</span>'
            f'<span class="metric-value" style="color:var(--amber)">{placeholder_count}</span>'
            '<span class="metric-detail">待确认队伍</span>'
            '</div>'
        )

    cmp = payload.get("comparison_stats", {})
    cmp_total = cmp.get("total_completed", 0)
    comparison_bar_html = ""
    if cmp_total > 0:
        cmp_res_ok = cmp.get("result_correct", 0)
        cmp_score_ok = cmp.get("score_correct", 0)
        cmp_res_rate = cmp.get("result_rate", 0)
        cmp_score_rate = cmp.get("score_rate", 0)
        res_pct = f"{cmp_res_rate:.0%}"
        score_pct = f"{cmp_score_rate:.0%}"
        res_w = max(4, cmp_res_rate * 100)
        score_w = max(4, cmp_score_rate * 100)
        comparison_bar_html = (
            '<div class="cmp-bar">'
            '<div class="cmp-title">预测 vs 实际 · 完赛统计</div>'
            '<div class="cmp-row">'
            '<span class="cmp-label">赛果命中</span>'
            f'<div class="cmp-track"><div class="cmp-fill cmp-fill-res" style="width:{res_w:.0f}%"></div></div>'
            f'<span class="cmp-val">{cmp_res_ok}/{cmp_total} ({res_pct})</span>'
            '</div>'
            '<div class="cmp-row">'
            '<span class="cmp-label">比分命中</span>'
            f'<div class="cmp-track"><div class="cmp-fill cmp-fill-score" style="width:{score_w:.0f}%"></div></div>'
            f'<span class="cmp-val">{cmp_score_ok}/{cmp_total} ({score_pct})</span>'
            '</div>'
            '</div>'
        )

    actions_html = "".join(_render_action_item(a) for a in payload.get("corrective_actions", [])) or '<p class="empty-state">暂无待处理的模型治理行动项。</p>'
    issues_html = "".join(_render_issue_tag(t) for t in payload.get("model_issue_tags", [])) or '<p class="empty-state">暂无活跃的模型异常反馈。</p>'
    phase_stats = payload.get("phase_stats", [])
    overall_stats = phase_stats[:1]
    stage_stats = phase_stats[1:]
    group_stats = payload.get("group_stats", [])
    daily_stats_cards = "".join(_render_daily_stat(s) for s in payload.get("daily_stats", []))
    daily_stats_html = daily_stats_cards or '<p class="empty-state">暂无每日统计。</p>'
    stats_html = (
        '<div class="phase-stats-section">'
        '<div class="phase-stats-title">总数据</div>'
        f'<div class="phase-stats-grid">{_render_stats_card_grid(overall_stats)}</div>'
        '</div>'
        '<div class="phase-stats-section">'
        '<div class="phase-stats-title">阶段汇总</div>'
        f'<div class="phase-stats-grid">{_render_stats_card_grid(stage_stats)}</div>'
        '</div>'
        '<div class="phase-stats-section">'
        '<div class="phase-stats-title">小组赛 / 分组汇总</div>'
        f'<div class="phase-stats-grid">{_render_stats_card_grid(group_stats)}</div>'
        '</div>'
        '<div class="daily-stats-section">'
        '<div class="phase-stats-title">逐日统计日志</div>'
        f'<div class="stats-grid">{daily_stats_html}</div>'
        '</div>'
    )

    payload["ui_defaults"] = {
        "active_date": default_date,
        "visible_dates": default_visible_dates,
    }
    payload["rendered"] = {
        "metrics_html": metrics_html,
        "comparison_bar_html": comparison_bar_html,
        "cards_html": cards_html,
        "schedule_html": schedule_html,
        "actions_html": actions_html,
        "issues_html": issues_html,
        "stats_html": stats_html,
        "date_buttons_html": date_btns,
    }
    return payload


_OVERVIEW_CARD_FIELDS = {
    "match_id",
    "prediction_origin",
    "prediction_source",
    "prediction_status",
    "data_origin",
    "date",
    "kickoff_at",
    "local_kickoff_at",
    "calculation_timezone",
    "venue",
    "group",
    "phase",
    "home_name",
    "away_name",
    "home_name_zh",
    "away_name_zh",
    "predicted_result",
    "predicted_result_label",
    "score_text",
    "total_goals",
    "confidence",
    "confidence_label",
    "expected_goals_proxy",
    "clean_sheet_probability",
    "scoreline_distribution",
    "result_confidence",
    "score_confidence",
    "total_goals_confidence",
    "confidence_note",
    "divination_overlay",
    "divination_hexagram",
    "evaluation_status",
    "evaluation_label",
    "hit_class",
    "result_hit",
    "score_hit",
    "home_colors",
    "away_colors",
    "home_ranking",
    "away_ranking",
    "play_title",
    "risk_flags",
    "watch_points",
    "primary_error",
    "actual_score_home",
    "actual_score_away",
    "is_completed",
    "beijing_date",
    "beijing_time",
    "data_source",
    "data_source_label",
    "display_state",
    "predicted_result",
    "actual_result",
    "fixture",
    "prediction",
    "actual",
    "evaluation",
}


def _card_overview(card: dict) -> dict:
    return {key: card.get(key) for key in _OVERVIEW_CARD_FIELDS if key in card}


def _dashboard_overview_payload(payload: dict) -> dict:
    payload = _refresh_dashboard_summary(payload)
    overview = {
        key: payload.get(key)
        for key in (
            "version",
            "edition",
            "generated_at",
            "mode",
            "status",
            "summary",
            "corrective_actions",
            "model_issue_tags",
            "daily_stats",
            "comparison_stats",
            "schedule_data",
            "phase_stats",
            "group_stats",
            "observation",
            "disclaimer",
            "safety_invariants",
            "hyperparameters",
            "data_path",
            "html_path",
            "static_data_path",
        )
        if key in payload
    }
    overview["cards"] = [_card_overview(card) for card in payload.get("cards", [])]
    overview["ui_defaults"] = payload.get("ui_defaults", {})
    overview["api_capabilities"] = {
        "overview": True,
        "match_detail": True,
    }
    return _decorate_dashboard_payload_for_frontend(overview)


def _find_dashboard_card(payload: dict, match_id: str) -> dict | None:
    for card in payload.get("cards", []):
        if str(card.get("match_id", "")) == str(match_id):
            return card
    return None


def _parse_score_text(score_text: str | None) -> dict[str, int | None]:
    text = str(score_text or "").strip()
    if "-" not in text:
        return {"home": None, "away": None}
    home_text, away_text = text.split("-", 1)
    home_val = int(home_text) if home_text.strip().isdigit() else None
    away_val = int(away_text) if away_text.strip().isdigit() else None
    return {"home": home_val, "away": away_val}


def _parse_score_pair(value: dict | None) -> dict[str, int | None]:
    value = value or {}
    home_val = value.get("home")
    away_val = value.get("away")
    try:
        home_val = int(home_val) if home_val is not None else None
    except (TypeError, ValueError):
        home_val = None
    try:
        away_val = int(away_val) if away_val is not None else None
    except (TypeError, ValueError):
        away_val = None
    return {"home": home_val, "away": away_val}


def _score_text_from_pair(home_score, away_score, *, missing: str = "-:-") -> str:
    if home_score is None or away_score is None:
        return missing
    return f"{home_score}-{away_score}"


def _clean_legacy_prediction_text(value):
    if isinstance(value, str):
        if "Recovered from prior remaining-predictions artifact" in value:
            return "从历史锁定预测恢复，保留赛前预测记录。"
        if value.strip(" ?？!！.。:：;；,，") == "" and "?" in value:
            return "历史预测文案缺失，保留比分和方向。"
        if "???" in value:
            return "历史预测文案缺失，保留比分和方向。"
        return value
    if isinstance(value, list):
        cleaned = [_clean_legacy_prediction_text(item) for item in value]
        return [item for item in cleaned if item not in ("", None)]
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _clean_legacy_prediction_text(item)) not in ("", None, [])
        }
    return value


def _prediction_exists(card: dict) -> bool:
    if card.get("prediction_status") == "not_predicted":
        return False
    if card.get("predicted_result"):
        return True
    score = _parse_score_text(card.get("score_text"))
    return score["home"] is not None and score["away"] is not None


def _actual_exists(card: dict) -> bool:
    return card.get("actual_score_home") is not None and card.get("actual_score_away") is not None


def _card_display_state(card: dict) -> str:
    home_name = str(card.get("home_name") or "")
    away_name = str(card.get("away_name") or "")
    if card.get("data_source") == "placeholder":
        return "placeholder"
    has_prediction = _prediction_exists(card)
    has_actual = _actual_exists(card)
    if has_prediction and has_actual:
        return "evaluated"
    if has_prediction:
        return "predicted"
    if has_actual:
        return "actual_only"
    return "fixture"


def _normalize_card_state(card: dict) -> dict:
    for text_key in ("play_title", "confidence_note"):
        card[text_key] = _clean_legacy_prediction_text(card.get(text_key, ""))
    for list_key in ("risk_flags", "watch_points"):
        card[list_key] = _clean_legacy_prediction_text(card.get(list_key, []) or [])
    card["play_card"] = _clean_legacy_prediction_text(card.get("play_card", {}) or {})

    pred_score = _parse_score_text(card.get("score_text"))
    actual_home = card.get("actual_score_home")
    actual_away = card.get("actual_score_away")
    has_prediction = _prediction_exists(card)
    has_actual = _actual_exists(card)
    home_name = str(card.get("home_name") or "")
    away_name = str(card.get("away_name") or "")
    knockout_prediction = _normalize_knockout_payload(
        card.get("knockout_prediction"),
        home_name=home_name,
        away_name=away_name,
    )
    knockout_actual = _normalize_knockout_payload(
        card.get("knockout_actual"),
        home_name=home_name,
        away_name=away_name,
    )
    is_knockout = _is_knockout_phase(card.get("phase", ""))

    predicted_result = str(card.get("predicted_result") or "")
    if not predicted_result and pred_score["home"] is not None and pred_score["away"] is not None:
        predicted_result = _actual_result_from_score(pred_score["home"], pred_score["away"])
    if not knockout_prediction:
        knockout_prediction = _fallback_knockout_prediction(
            phase=card.get("phase", ""),
            predicted_result=predicted_result,
            predicted_score=pred_score,
            home_name=home_name,
            away_name=away_name,
        )

    actual_result = _actual_result_from_score(actual_home, actual_away) if has_actual else str(card.get("actual_result") or "")
    if actual_result:
        card["actual_result"] = actual_result
    if not knockout_actual and has_actual:
        knockout_actual = _fallback_knockout_actual(
            phase=card.get("phase", ""),
            actual_score={"home": actual_home, "away": actual_away},
            home_name=home_name,
            away_name=away_name,
        )

    result_hit = None if has_prediction and has_actual else card.get("result_hit")
    score_hit = None if has_prediction and has_actual else card.get("score_hit")
    if has_prediction and has_actual:
        result_hit = predicted_result == actual_result if predicted_result and actual_result else None
        card["result_hit"] = result_hit
        score_hit = (
            pred_score["home"] == actual_home
            and pred_score["away"] == actual_away
            and pred_score["home"] is not None
            and pred_score["away"] is not None
        )
        card["score_hit"] = score_hit
    if is_knockout and knockout_prediction and knockout_actual:
        prediction_regular = knockout_prediction.get("regular_time", {})
        actual_regular = knockout_actual.get("regular_time", {})
        prediction_extra = knockout_prediction.get("extra_time", {})
        actual_extra = knockout_actual.get("extra_time", {})
        prediction_penalties = knockout_prediction.get("penalties", {})
        actual_penalties = knockout_actual.get("penalties", {})
        prediction_advance = knockout_prediction.get("advance", {})
        actual_advance = knockout_actual.get("advance", {})
        card["result_hit"] = (
            prediction_regular.get("result") == actual_regular.get("result")
            if prediction_regular.get("result") and actual_regular.get("result")
            else card.get("result_hit")
        )
        card["score_hit"] = (
            prediction_regular.get("score", {}).get("home") == actual_regular.get("score", {}).get("home")
            and prediction_regular.get("score", {}).get("away") == actual_regular.get("score", {}).get("away")
            and prediction_regular.get("score", {}).get("home") is not None
            and actual_regular.get("score", {}).get("home") is not None
        )
        card["knockout_evaluation"] = {
            "regular_time_result_hit": card.get("result_hit"),
            "regular_time_score_hit": card.get("score_hit"),
            "extra_time_hit": (
                prediction_extra.get("played") == actual_extra.get("played")
                and (
                    not actual_extra.get("played")
                    or prediction_extra.get("score") == actual_extra.get("score")
                    or prediction_extra.get("result") == actual_extra.get("result")
                )
            ),
            "penalties_hit": (
                prediction_penalties.get("played") == actual_penalties.get("played")
                and (
                    not actual_penalties.get("played")
                    or prediction_penalties.get("score") == actual_penalties.get("score")
                    or prediction_penalties.get("winner") == actual_penalties.get("winner")
                )
            ),
            "advance_hit": (
                prediction_advance.get("winner") == actual_advance.get("winner")
                if prediction_advance.get("winner") and actual_advance.get("winner")
                else None
            ),
        }

    display_state = _card_display_state(card)
    evaluation_exists = has_prediction and has_actual

    if display_state == "evaluated":
        if score_hit is True:
            hit_class = "double-hit"
            evaluation_label = "完美双中"
        elif result_hit is True:
            hit_class = "result-hit"
            evaluation_label = "仅中赛果"
        else:
            hit_class = "miss"
            evaluation_label = "预测偏差"
        evaluation_status = "evaluated"
    elif display_state == "predicted":
        hit_class = "pending"
        evaluation_label = "待赛果"
        evaluation_status = "predicted_only"
    elif display_state == "actual_only":
        hit_class = "not-predicted"
        evaluation_label = "未预测"
        evaluation_status = "actual_only"
    elif display_state == "placeholder":
        hit_class = "placeholder"
        evaluation_label = "占位"
        evaluation_status = "placeholder"
    else:
        hit_class = "fixture"
        evaluation_label = "待预测"
        evaluation_status = "fixture_only"

    if not card.get("prediction_status"):
        card["prediction_status"] = "locked_pre_match_prediction" if has_prediction else "not_predicted"
    card["predicted_result"] = predicted_result
    card["predicted_result_label"] = OUTCOME_LABELS.get(predicted_result, predicted_result or "未预测")
    card["score_text"] = _score_text_from_pair(pred_score["home"], pred_score["away"])
    card["is_completed"] = has_actual
    card["hit_class"] = hit_class
    card["evaluation_label"] = evaluation_label
    card["evaluation_status"] = evaluation_status
    card["display_state"] = display_state

    fixture = {
        "exists": True,
        "status": "placeholder" if display_state == "placeholder" else "scheduled",
        "match_id": card.get("match_id", ""),
        "phase": card.get("phase", ""),
        "group": card.get("group", ""),
        "venue": card.get("venue", ""),
        "kickoff_at": card.get("kickoff_at", ""),
        "local_kickoff_at": card.get("local_kickoff_at") or card.get("kickoff_at", ""),
        "beijing_date": card.get("beijing_date") or card.get("date", ""),
        "beijing_time": card.get("beijing_time", ""),
        "home_name": card.get("home_name", ""),
        "away_name": card.get("away_name", ""),
        "home_ranking": card.get("home_ranking"),
        "away_ranking": card.get("away_ranking"),
        "data_source": card.get("data_source", "official"),
    }
    prediction = {
        "exists": has_prediction,
        "status": card.get("prediction_status") or ("locked_pre_match_prediction" if has_prediction else "not_predicted"),
        "origin": card.get("prediction_origin", "none"),
        "source": card.get("prediction_source", "none"),
        "source_path": card.get("prediction_source_path", ""),
        "result": predicted_result,
        "result_label": OUTCOME_LABELS.get(predicted_result, predicted_result or "未预测"),
        "score": pred_score,
        "score_text": _score_text_from_pair(pred_score["home"], pred_score["away"]),
        "knockout": knockout_prediction,
        "confidence": card.get("confidence", "none"),
        "confidence_label": card.get("confidence_label", "NONE"),
        "expected_goals_proxy": card.get("expected_goals_proxy"),
        "clean_sheet_probability": card.get("clean_sheet_probability"),
        "scoreline_distribution": card.get("scoreline_distribution"),
        "watch_points": card.get("watch_points", []) or [],
        "risk_flags": card.get("risk_flags", []) or [],
        "play_title": card.get("play_title", ""),
        "confidence_note": card.get("confidence_note", ""),
    }
    actual = {
        "exists": has_actual,
        "status": "final" if has_actual else "pending",
        "score": {"home": actual_home, "away": actual_away},
        "score_text": _score_text_from_pair(actual_home, actual_away),
        "result": actual_result,
        "result_label": OUTCOME_LABELS.get(actual_result, actual_result or ""),
        "knockout": knockout_actual,
    }
    evaluation = {
        "exists": evaluation_exists,
        "status": evaluation_status,
        "result_hit": result_hit,
        "score_hit": score_hit,
        "knockout": card.get("knockout_evaluation"),
        "hit_class": hit_class,
        "label": evaluation_label,
        "primary_error": card.get("primary_error", ""),
    }

    card["fixture"] = fixture
    card["prediction"] = prediction
    card["actual"] = actual
    card["evaluation"] = evaluation
    return card


def _refresh_dashboard_summary(payload: dict) -> dict:
    cards = payload.get("cards", []) or []
    for idx, card in enumerate(cards):
        cards[idx] = _normalize_card_state(card)

    real_cards = [c for c in cards if c.get("data_source") != "placeholder"]
    predicted_cards = [c for c in real_cards if c.get("prediction", {}).get("exists")]
    actual_cards = [c for c in real_cards if c.get("actual", {}).get("exists")]
    evaluated_cards = [c for c in real_cards if c.get("evaluation", {}).get("exists")]
    actual_only_cards = [c for c in real_cards if c.get("display_state") == "actual_only"]
    prediction_only_cards = [c for c in real_cards if c.get("display_state") == "predicted"]
    fixture_only_cards = [c for c in real_cards if c.get("display_state") == "fixture"]

    result_correct = sum(1 for c in evaluated_cards if c.get("evaluation", {}).get("result_hit") is True)
    score_correct = sum(1 for c in evaluated_cards if c.get("evaluation", {}).get("score_hit") is True)
    total_goals_correct = 0
    for card in evaluated_cards:
        pred_score = card.get("prediction", {}).get("score", {}) or {}
        actual_score = card.get("actual", {}).get("score", {}) or {}
        if pred_score.get("home") is None or pred_score.get("away") is None:
            continue
        if actual_score.get("home") is None or actual_score.get("away") is None:
            continue
        if (pred_score["home"] + pred_score["away"]) == (actual_score["home"] + actual_score["away"]):
            total_goals_correct += 1

    dates = sorted(
        set(_card_display_date(c) for c in real_cards if _card_display_date(c))
    )

    summary = payload.setdefault("summary", {})
    summary["predictions"] = len(predicted_cards)
    summary["predicted_matches"] = len(predicted_cards)
    summary["actual_matches"] = len(actual_cards)
    summary["evaluated_matches"] = len(evaluated_cards)
    summary["comparable_matches"] = len(evaluated_cards)
    summary["actual_only_matches"] = len(actual_only_cards)
    summary["prediction_only_matches"] = len(prediction_only_cards)
    summary["fixture_only_matches"] = len(fixture_only_cards)
    summary["fact_cards"] = sum(1 for c in real_cards if not c.get("prediction", {}).get("exists"))
    summary["placeholder_count"] = len(cards) - len(real_cards)
    summary["total_cards"] = len(cards)
    summary["dates"] = dates
    summary["result_hits"] = result_correct
    summary["score_hits"] = score_correct
    summary["result_hit_rate"] = _rate(result_correct, len(evaluated_cards))
    summary["score_hit_rate"] = _rate(score_correct, len(evaluated_cards))
    summary["total_goals_hit_rate"] = _rate(total_goals_correct, len(evaluated_cards))

    payload["comparison_stats"] = {
        "total_completed": len(evaluated_cards),
        "result_correct": result_correct,
        "score_correct": score_correct,
        "result_rate": _rate(result_correct, len(evaluated_cards)),
        "score_rate": _rate(score_correct, len(evaluated_cards)),
    }

    def card_phase_key(card: dict) -> str:
        return str((card.get("fixture") or {}).get("phase") or card.get("phase") or "")

    def card_group_key(card: dict) -> str:
        return str((card.get("fixture") or {}).get("group") or card.get("group") or "").strip().upper()

    def build_stat_bucket(bucket_key: str, label: str, bucket_cards: list[dict], *, phase: str = "", group: str = "") -> dict:
        bucket_predicted = [c for c in bucket_cards if c.get("prediction", {}).get("exists")]
        bucket_actual = [c for c in bucket_cards if c.get("actual", {}).get("exists")]
        bucket_evaluated = [c for c in bucket_cards if c.get("evaluation", {}).get("exists")]
        bucket_result_hits = sum(1 for c in bucket_evaluated if c.get("evaluation", {}).get("result_hit") is True)
        bucket_score_hits = sum(1 for c in bucket_evaluated if c.get("evaluation", {}).get("score_hit") is True)
        return {
            "bucket_key": bucket_key,
            "phase": phase,
            "group": group,
            "label": label,
            "total_matches": len(bucket_cards),
            "predicted_matches": len(bucket_predicted),
            "actual_matches": len(bucket_actual),
            "evaluated_matches": len(bucket_evaluated),
            "result_hits": bucket_result_hits,
            "score_hits": bucket_score_hits,
            "result_hit_rate": _rate(bucket_result_hits, len(bucket_evaluated)),
            "score_hit_rate": _rate(bucket_score_hits, len(bucket_evaluated)),
        }

    supported_real_cards = [c for c in real_cards if card_phase_key(c) in FACT_PHASE_ORDER]
    phase_buckets: list[tuple[str, str, list[dict]]] = [("all", STAT_PHASE_LABELS["all"], supported_real_cards)]
    for phase_key in FACT_PHASE_ORDER:
        phase_cards = [c for c in real_cards if card_phase_key(c) == phase_key]
        phase_buckets.append((phase_key, STAT_PHASE_LABELS.get(phase_key, phase_key), phase_cards))

    payload["phase_stats"] = [
        build_stat_bucket(phase_key, label, phase_cards, phase=phase_key if phase_key != "all" else "")
        for phase_key, label, phase_cards in phase_buckets
    ]

    group_phase_cards = [c for c in real_cards if card_phase_key(c) == "group"]
    group_keys = sorted({card_group_key(c) for c in group_phase_cards if card_group_key(c)})
    group_stats = [
        build_stat_bucket("group-summary", "小组赛汇总", group_phase_cards, phase="group")
    ]
    for group_key in group_keys:
        group_cards = [c for c in group_phase_cards if card_group_key(c) == group_key]
        group_stats.append(
            build_stat_bucket(f"group-{group_key}", f"{group_key}组", group_cards, phase="group", group=group_key)
        )
    payload["group_stats"] = group_stats
    return payload


def render_html(payload: dict, *, root: Path, html_path: Path) -> str:
    _decorate_dashboard_payload_for_frontend(payload)
    generated = _safe(payload.get("generated_at", ""))
    edition = _safe(payload.get("edition", "2026"))
    bootstrap = {
        "edition": payload.get("edition", "2026"),
        "api_url": f"/api/dashboard/overview?edition={payload.get('edition', '2026')}",
        "legacy_api_url": f"/api/dashboard?edition={payload.get('edition', '2026')}",
        "match_detail_url": f"/api/dashboard/match?edition={payload.get('edition', '2026')}&match_id=",
        "static_data_url": "./prediction-dashboard.json",
        "poll_interval_ms": 60000,
    }
    bootstrap_json = json.dumps(bootstrap, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{edition} 世界杯 AI 章鱼哥预测看板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Chakra+Petch:wght@600;700&family=Noto+Sans+SC:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#06090f;--surface:rgba(12,18,32,0.75);--surface-solid:#0c1220;--surface-soft:rgba(17,26,41,0.9);
  --text:#e8ecf4;--muted:#64748b;--muted-strong:#8fa3ba;--border:rgba(255,255,255,0.06);
  --cyan:#22d3ee;--cyan-dim:rgba(34,211,238,0.15);--cyan-glow:rgba(34,211,238,0.25);
  --purple:#a78bfa;--purple-dim:rgba(167,139,250,0.15);--purple-glow:rgba(167,139,250,0.25);
  --green:#34d399;--green-dim:rgba(52,211,153,0.15);
  --amber:#fbbf24;--amber-dim:rgba(251,191,36,0.15);
  --red:#f87171;--red-dim:rgba(248,113,113,0.15);
  --blue:#60a5fa;--blue-dim:rgba(96,165,250,0.15);
  --shadow:0 8px 32px rgba(0,0,0,0.4);--shadow-soft:0 18px 42px rgba(0,0,0,0.24);
  --glow-cyan:0 0 20px rgba(34,211,238,0.12),0 0 40px rgba(34,211,238,0.06);
  --glow-purple:0 0 20px rgba(167,139,250,0.12),0 0 40px rgba(167,139,250,0.06);
  --radius:16px;--radius-sm:10px;
  --font:'Noto Sans SC','Space Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
  --font-display:'Noto Sans SC','Space Grotesk',sans-serif;
  --font-number:'Chakra Petch','Space Grotesk','Noto Sans SC',sans-serif;
}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.6;-webkit-font-smoothing:antialiased;
  background-image:
    radial-gradient(ellipse at 15% 10%,rgba(34,211,238,0.04) 0%,transparent 50%),
    radial-gradient(ellipse at 85% 85%,rgba(167,139,250,0.04) 0%,transparent 50%),
    radial-gradient(ellipse at 50% 50%,rgba(96,165,250,0.02) 0%,transparent 60%);
  background-attachment:fixed;
}}
.shell{{max-width:1240px;margin:0 auto;padding:0 20px 60px}}

/* 鈹€鈹€ Hero 鈹€鈹€ */
.hero{{padding:56px 0 42px;text-align:center;max-width:780px;margin:0 auto}}
.hero-eyebrow{{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.12em;
  text-transform:uppercase;color:var(--cyan);background:linear-gradient(135deg,rgba(34,211,238,0.14),rgba(34,211,238,0.06));
  padding:7px 18px;border-radius:999px;margin-bottom:18px;border:1px solid rgba(34,211,238,0.18);
  box-shadow:inset 0 1px 0 rgba(255,255,255,0.05),0 10px 22px rgba(0,0,0,0.16)}}
.hero h1{{font-family:var(--font-display);font-size:clamp(34px,5vw,48px);font-weight:800;letter-spacing:-.04em;line-height:1.08;margin-bottom:12px;
  background:linear-gradient(135deg,#fff 30%,var(--cyan) 70%,var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-wrap:balance}}
.hero-sub{{color:var(--muted-strong);font-size:13px;letter-spacing:.02em;line-height:1.75;max-width:680px;margin:0 auto}}

/* 鈹€鈹€ Metrics 鈹€鈹€ */
.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:34px}}
.metric{{background:linear-gradient(180deg,rgba(15,22,37,0.94),rgba(9,14,25,0.98));border:1px solid rgba(120,156,204,0.12);border-radius:20px;
  padding:18px 18px 16px;text-align:left;box-shadow:var(--shadow-soft);transition:all .25s;
  backdrop-filter:blur(12px);position:relative;overflow:hidden}}
.metric::before{{content:'';position:absolute;top:0;left:18px;right:18px;height:1px;
  background:linear-gradient(90deg,rgba(34,211,238,0),rgba(34,211,238,0.7),rgba(34,211,238,0));opacity:.6}}
.metric::after{{content:'';position:absolute;left:-20px;bottom:-36px;width:120px;height:120px;
  background:radial-gradient(circle,rgba(34,211,238,0.12),transparent 70%);pointer-events:none;opacity:.7}}
.metric:hover{{transform:translateY(-2px);border-color:rgba(34,211,238,0.16);box-shadow:0 22px 40px rgba(0,0,0,0.28),0 0 24px rgba(34,211,238,0.06)}}
.metric-label{{display:block;font-size:11px;color:var(--muted);margin-bottom:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}}
.metric-value{{display:block;font-size:34px;font-weight:700;letter-spacing:-.03em;color:#fff;font-variant-numeric:tabular-nums;font-family:var(--font-number);line-height:1.02}}
.metric-detail{{display:block;font-size:11px;color:var(--muted-strong);margin-top:10px;line-height:1.55;max-width:18em}}

/* 鈹€鈹€ Comparison Stats Bar 鈹€鈹€ */
.cmp-bar{{background:linear-gradient(180deg,rgba(14,21,35,0.95),rgba(9,14,25,0.98));border:1px solid rgba(133,154,205,0.12);border-radius:20px;
  padding:22px 24px 18px;margin-bottom:26px;backdrop-filter:blur(12px);box-shadow:var(--shadow-soft);
  position:relative;overflow:hidden}}
.cmp-bar::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--purple),transparent);opacity:.6}}
.cmp-title{{font-size:14px;font-weight:700;color:#d8c6ff;letter-spacing:.03em;margin-bottom:16px}}
.cmp-row{{display:flex;align-items:center;gap:14px;margin-bottom:10px}}
.cmp-label{{font-size:11px;color:var(--muted-strong);min-width:72px;font-weight:700;letter-spacing:.04em}}
.cmp-track{{flex:1;height:10px;background:rgba(255,255,255,0.04);border-radius:999px;overflow:hidden}}
.cmp-fill{{height:100%;border-radius:99px;transition:width .6s cubic-bezier(.4,0,.2,1)}}
.cmp-fill-res{{background:linear-gradient(90deg,var(--cyan),var(--green))}}
.cmp-fill-score{{background:linear-gradient(90deg,var(--purple),var(--amber))}}
.cmp-val{{font-size:13px;font-weight:700;color:var(--text);min-width:110px;text-align:right;font-variant-numeric:tabular-nums;font-family:var(--font-number)}}

/* 鈹€鈹€ Tabs 鈹€鈹€ */
.tabs{{display:grid;grid-auto-flow:column;grid-auto-columns:max-content;gap:6px;background:rgba(255,255,255,0.03);border:1px solid rgba(148,163,184,0.1);
  border-radius:20px;padding:6px;margin-bottom:28px;width:100%;backdrop-filter:blur(8px);overflow-x:auto;overscroll-behavior-x:contain;
  box-shadow:var(--shadow-soft);scrollbar-width:none}}
.tabs::-webkit-scrollbar{{display:none}}
.tab{{border:0;background:transparent;font-family:var(--font);font-size:13px;font-weight:600;
  color:var(--muted);padding:10px 18px;border-radius:14px;cursor:pointer;transition:all .2s;white-space:nowrap}}
.tab:hover{{color:var(--text)}}
.tab.active{{background:linear-gradient(135deg,rgba(34,211,238,0.2),rgba(34,211,238,0.08));color:#dffcff;
  box-shadow:0 12px 24px rgba(34,211,238,0.08),inset 0 1px 0 rgba(255,255,255,0.05)}}
.tab-panel{{display:none}}.tab-panel.active{{display:block}}

/* 鈹€鈹€ Toolbar 鈹€鈹€ */
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:18px}}
.toolbar-label{{font-size:12px;font-weight:600;color:var(--muted);margin-right:6px;letter-spacing:.05em;text-transform:uppercase}}
.toolbar button,.filter-btn{{border:1px solid var(--border);background:rgba(255,255,255,0.03);font-family:var(--font);
  font-size:11px;font-weight:700;color:var(--muted);padding:7px 14px;border-radius:999px;cursor:pointer;transition:all .2s;
  box-shadow:inset 0 1px 0 rgba(255,255,255,0.03)}}
.toolbar button:hover,.filter-btn:hover{{border-color:rgba(34,211,238,0.22);color:#dffcff;background:rgba(34,211,238,0.06);transform:translateY(-1px)}}
.toolbar button.active,.filter-btn.active{{background:linear-gradient(135deg,rgba(34,211,238,0.18),rgba(34,211,238,0.08));color:#dffcff;border-color:rgba(34,211,238,0.3)}}
.toolbar-status{{margin-left:auto;font-size:11px;color:var(--muted-strong)}}
.toolbar-actions{{display:flex;gap:8px;align-items:center;margin-bottom:18px;justify-content:space-between;flex-wrap:wrap}}
.status-pill{{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:99px;border:1px solid var(--border);font-size:11px;background:rgba(255,255,255,0.03);color:var(--muted)}}
.status-pill.is-loading{{color:var(--cyan);border-color:rgba(34,211,238,0.25);background:rgba(34,211,238,0.08)}}
.status-pill.is-live{{color:var(--green);border-color:rgba(52,211,153,0.25);background:rgba(52,211,153,0.08)}}
.status-pill.is-fallback{{color:var(--amber);border-color:rgba(251,191,36,0.25);background:rgba(251,191,36,0.08)}}
.status-pill.is-error{{color:var(--red);border-color:rgba(248,113,113,0.25);background:rgba(248,113,113,0.08)}}
.panel-placeholder{{background:var(--surface);border:1px dashed var(--border);border-radius:var(--radius);padding:24px;color:var(--muted);text-align:center;box-shadow:var(--shadow);backdrop-filter:blur(12px)}}

/* 鈹€鈹€ Match Cards Grid 鈹€鈹€ */
.cards-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:24px;
  max-width:980px;margin:0 auto;justify-content:center;align-items:start}}
.cards-grid > .empty-state{{grid-column:1/-1}}
.date-toolbar{{align-items:center;padding:14px 16px 4px;border-radius:20px;
  background:linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.012));border:1px solid rgba(148,163,184,0.08);
  box-shadow:var(--shadow-soft)}}

/* 鈹€鈹€ Match Card 鈹€鈹€ */
.match-card{{background:linear-gradient(180deg,rgba(14,22,35,0.97),rgba(7,12,22,0.99));border:1px solid rgba(120,156,204,0.14);border-radius:24px;
  box-shadow:0 18px 45px rgba(0,0,0,0.28),inset 0 1px 0 rgba(255,255,255,0.03);overflow:hidden;transition:all .3s cubic-bezier(.4,0,.2,1);
  cursor:pointer;backdrop-filter:blur(14px);position:relative}}
.match-card::before{{content:'';position:absolute;inset:0;border-radius:24px;
  background:
    radial-gradient(circle at top left,rgba(34,211,238,0.08),transparent 38%),
    radial-gradient(circle at 84% 100%,rgba(251,191,36,0.06),transparent 28%),
    linear-gradient(135deg,rgba(255,255,255,0.016),transparent 55%);
  pointer-events:none}}
.match-card::after{{content:'';position:absolute;inset:0;border-radius:24px;pointer-events:none;
  box-shadow:inset 0 1px 0 rgba(255,255,255,0.04), inset 0 0 0 1px rgba(255,255,255,0.01)}}
.match-card:hover{{transform:translateY(-5px);border-color:rgba(34,211,238,0.2);box-shadow:0 30px 64px rgba(0,0,0,0.34),0 0 24px rgba(34,211,238,0.08)}}
.match-card[data-hit="double-hit"]{{box-shadow:0 18px 45px rgba(0,0,0,0.28),0 0 0 1px rgba(52,211,153,0.08),inset 0 1px 0 rgba(255,255,255,0.03)}}
.match-card[data-hit="double-hit"]:hover{{box-shadow:0 26px 60px rgba(0,0,0,0.34),0 0 0 1px rgba(52,211,153,0.16),0 0 24px rgba(52,211,153,0.08)}}
.match-card[data-hit="result-hit"]{{box-shadow:0 18px 45px rgba(0,0,0,0.28),0 0 0 1px rgba(251,191,36,0.08),inset 0 1px 0 rgba(255,255,255,0.03)}}
.match-card[data-hit="result-hit"]:hover{{box-shadow:0 26px 60px rgba(0,0,0,0.34),0 0 0 1px rgba(251,191,36,0.16),0 0 24px rgba(251,191,36,0.08)}}
.match-card[data-hit="miss"]{{box-shadow:0 18px 45px rgba(0,0,0,0.28),0 0 0 1px rgba(248,113,113,0.08),inset 0 1px 0 rgba(255,255,255,0.03)}}
.match-card[data-hit="miss"]:hover{{box-shadow:0 26px 60px rgba(0,0,0,0.34),0 0 0 1px rgba(248,113,113,0.16),0 0 24px rgba(248,113,113,0.08)}}
.mc-top{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px;padding:18px 22px 16px;border-bottom:1px solid rgba(148,163,184,0.08);font-size:11px;color:var(--muted);position:relative;z-index:1;align-items:start;
  background:linear-gradient(180deg,rgba(19,30,48,0.55),rgba(19,30,48,0.12) 70%,transparent)}}
.mc-meta{{display:flex;flex-direction:column;gap:8px;min-width:0}}
.mc-meta-row{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.mc-id{{display:inline-flex;align-items:center;font-weight:700;color:#6fe4f2;font-size:15px;letter-spacing:.03em;font-family:var(--font-number);font-variant-numeric:tabular-nums;line-height:1.1}}
.mc-group{{display:inline-flex;align-items:center;font-weight:700;background:rgba(148,163,184,0.07);padding:5px 11px;border-radius:999px;font-size:10px;border:1px solid rgba(148,163,184,0.12);color:#cbd5e1;white-space:nowrap}}
.mc-time{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px;color:#8da0b8;font-variant-numeric:tabular-nums;line-height:1.4;text-wrap:balance}}
.mc-badges{{display:flex;align-items:flex-start;justify-content:flex-end;gap:8px;flex-wrap:wrap;max-width:220px}}
.eval-badge{{display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;padding:7px 11px;border-radius:12px;letter-spacing:.02em;white-space:nowrap}}
.eval-double{{background:var(--green-dim);color:var(--green);border:1px solid rgba(52,211,153,0.25)}}
.eval-result{{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(251,191,36,0.25)}}
.eval-miss{{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,113,113,0.25)}}
/* Data source badge */
.source-badge{{display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;padding:7px 10px;border-radius:12px;letter-spacing:.02em;white-space:nowrap}}
.source-official{{background:rgba(52,211,153,0.1);color:var(--green);border:1px solid rgba(52,211,153,0.2)}}
.source-placeholder{{background:rgba(251,191,36,0.12);color:var(--amber);border:1px solid rgba(251,191,36,0.25);animation:pulse-subtle 2s ease-in-out infinite}}
/* Placeholder card styling */
.match-card-placeholder{{opacity:0.72;border-color:rgba(251,191,36,0.15)!important;background:linear-gradient(180deg,rgba(8,12,24,0.97),rgba(20,16,40,0.95))!important}}
.match-card-placeholder .mc-body::after{{content:"";position:absolute;inset:0;background:repeating-linear-gradient(-45deg,transparent,transparent 10px,rgba(251,191,36,0.02) 10px,rgba(251,191,36,0.02) 20px);pointer-events:none;border-radius:0 0 var(--radius) var(--radius)}}
@keyframes pulse-subtle{{0%,100%{{opacity:1}}50%{{opacity:0.7}}}}
/* Placeholder metric in stats */
.metric-placeholder .metric-value{{color:var(--amber)!important;font-size:22px!important}}
.mc-body{{padding:28px 22px 20px;text-align:center;position:relative;z-index:1}}
.mc-team{{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;gap:14px;margin-bottom:22px}}
.team-name{{display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;min-width:0;font-size:24px;font-weight:800;color:#fff;line-height:1.08;letter-spacing:-.02em;text-wrap:balance}}
.rank-tag{{display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:var(--cyan);background:rgba(34,211,238,0.12);padding:4px 9px;border-radius:999px;border:1px solid rgba(34,211,238,0.15);font-variant-numeric:tabular-nums;font-family:var(--font-number)}}
.mc-vs{{display:inline-flex;align-items:center;justify-content:center;font-size:11px;color:#a3b4c9;font-weight:700;letter-spacing:.08em;background:linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.025));border:1px solid rgba(148,163,184,0.1);border-radius:999px;padding:7px 12px;text-transform:uppercase}}
.mc-score{{font-size:50px;font-weight:700;letter-spacing:-.04em;margin:10px 0 6px;font-variant-numeric:tabular-nums;
  font-family:var(--font-number);color:#fff;text-shadow:0 0 30px rgba(34,211,238,0.15)}}
.score-compare{{display:flex;flex-direction:column;gap:10px;margin:22px 0 14px;padding:16px;
  background:linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.015));border:1px solid rgba(148,163,184,0.12);border-radius:20px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,0.04)}}
.compare-head,.compare-row{{display:grid;grid-template-columns:76px minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:center}}
.compare-head{{padding-bottom:9px;border-bottom:1px dashed rgba(148,163,184,0.14)}}
.compare-head span{{font-size:11px;color:var(--muted);font-weight:700;letter-spacing:.06em;text-transform:uppercase;text-align:center}}
.compare-head span:first-child{{color:transparent;text-align:left}}
.compare-row{{padding:2px 0}}
.compare-label{{font-size:13px;color:#8ea1bb;font-weight:700;width:auto;text-align:left;white-space:nowrap}}
.compare-score{{display:flex;align-items:center;justify-content:center;min-height:58px;padding:8px 10px;border-radius:18px;
  border:1px solid rgba(148,163,184,0.12);background:linear-gradient(180deg,rgba(255,255,255,0.045),rgba(255,255,255,0.02));font-size:34px;font-weight:700;font-family:var(--font-number);color:var(--text);
  font-variant-numeric:tabular-nums;letter-spacing:-.02em;text-align:center;box-shadow:inset 0 1px 0 rgba(255,255,255,0.03),inset 0 0 0 1px rgba(255,255,255,0.01)}}
.score-compare-knockout .compare-score{{font-size:24px;min-height:60px;padding:10px 12px}}
.score-compare-knockout .compare-row:last-child .compare-score{{font-size:18px;font-family:var(--font);line-height:1.25;text-wrap:balance;word-break:break-word}}
.compare-actual{{color:#88eff9;background:linear-gradient(180deg,rgba(34,211,238,0.12),rgba(34,211,238,0.055));border-color:rgba(34,211,238,0.18)}}
.ko-summary{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:18px 0 8px}}
.ko-summary-pill{{display:flex;flex-direction:column;gap:6px;align-items:flex-start;padding:12px 13px;border-radius:16px;
  border:1px solid rgba(148,163,184,0.12);background:linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.018));text-align:left;min-height:74px}}
.ko-summary-pill b{{font-size:11px;letter-spacing:.04em;text-transform:none;color:var(--muted)}}
.ko-summary-pill span{{font-size:18px;font-weight:700;color:#fff;line-height:1.3;text-wrap:balance}}
.compare-icon{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px;margin-left:6px}}
.compare-perfect{{background:var(--green-dim);color:var(--green);border:1px solid rgba(52,211,153,0.25)}}
.compare-result{{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(251,191,36,0.25)}}
.compare-miss{{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,113,113,0.25)}}
.mc-result{{margin-top:14px;display:flex;justify-content:center}}
.outcome-badge{{display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;padding:8px 16px;border-radius:999px;letter-spacing:.02em;box-shadow:inset 0 1px 0 rgba(255,255,255,0.04)}}
.outcome-home{{background:var(--green-dim);color:var(--green);border:1px solid rgba(52,211,153,0.25)}}
.outcome-away{{background:var(--blue-dim);color:var(--blue);border:1px solid rgba(96,165,250,0.25)}}
.outcome-draw{{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(251,191,36,0.25)}}
.outcome-none{{background:rgba(148,163,184,.08);color:var(--muted);border:1px solid rgba(148,163,184,.18)}}
.mc-bottom{{padding:18px 22px 24px;border-top:1px solid rgba(148,163,184,0.09);position:relative;z-index:1;background:linear-gradient(180deg,rgba(255,255,255,0.012),rgba(6,10,18,0.16))}}
.conf-row{{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;margin-bottom:14px}}
.conf-label{{font-size:12px;color:var(--muted-strong);font-weight:700;width:auto;letter-spacing:.04em}}
.conf-track{{flex:1;height:8px;background:rgba(255,255,255,0.04);border-radius:999px;overflow:hidden}}
.conf-fill{{height:100%;border-radius:2px;transition:width .4s}}
.conf-high{{width:80%;background:linear-gradient(90deg,var(--green),rgba(52,211,153,0.6));box-shadow:0 0 8px rgba(52,211,153,0.3)}}
.conf-medium{{width:55%;background:linear-gradient(90deg,var(--cyan),rgba(34,211,238,0.6));box-shadow:0 0 8px rgba(34,211,238,0.3)}}
.conf-low{{width:30%;background:linear-gradient(90deg,var(--amber),rgba(251,191,36,0.6));box-shadow:0 0 8px rgba(251,191,36,0.3)}}
.conf-unknown{{width:15%;background:var(--muted)}}
.conf-val{{display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#cbd5e1;min-width:84px;padding:5px 12px;text-align:center;letter-spacing:.04em;
  border:1px solid rgba(148,163,184,0.12);border-radius:12px;background:rgba(255,255,255,0.025)}}
.meta-row{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}}
.inline-metric{{font-size:10px;display:flex;gap:8px;align-items:flex-start;background:linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02));
  padding:12px 13px;border-radius:16px;border:1px solid rgba(148,163,184,0.08);min-width:0;min-height:56px}}
.inline-metric span{{color:var(--muted);flex-shrink:0;letter-spacing:.03em;font-size:11px}}
.inline-metric strong{{font-weight:700;font-size:13px;color:var(--text);min-width:0;overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-height:1.45;white-space:normal}}
.metric-venue{{grid-column:1/-1}}
.metric-venue strong{{-webkit-line-clamp:2}}
.section-label{{display:block;font-size:11px;font-weight:700;color:#7ee9f3;text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:10px;margin-top:0}}
.dist-row{{display:flex;align-items:center;gap:6px;margin-bottom:3px;font-size:11px}}
.dist-score{{font-weight:700;width:28px;color:var(--muted);font-variant-numeric:tabular-nums}}
.dist-track{{flex:1;height:3px;background:rgba(255,255,255,0.04);border-radius:2px;overflow:hidden}}
.dist-fill{{height:100%;background:linear-gradient(90deg,var(--cyan),var(--purple));border-radius:2px}}
.dist-pct{{width:30px;text-align:right;font-weight:700;color:var(--cyan);font-variant-numeric:tabular-nums}}
.watchpoints{{margin-top:10px;padding:14px 16px 4px;border:1px solid rgba(148,163,184,0.08);border-radius:18px;
  background:linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.012))}}
.watchpoints ul{{margin:0;padding-left:16px;font-size:13px;color:#b2c4d9;line-height:1.78}}
.watchpoints li+li{{margin-top:6px}}
.watchpoints li::marker{{color:var(--cyan)}}

.divination-summary-row{{
  display:grid;gap:8px;background:linear-gradient(180deg,rgba(167,139,250,0.16),rgba(167,139,250,0.06));
  border:1px solid rgba(167,139,250,0.28);border-radius:8px;
  padding:8px 12px;margin-top:10px;display:flex;align-items:center;gap:6px
}}
.div-chip{{display:flex;align-items:center;justify-content:center;min-height:28px;padding:4px 6px;
  border:1px solid rgba(167,139,250,0.22);border-radius:6px;background:rgba(8,12,24,0.34);
  color:#d8ccff;font-size:10px;font-weight:700;text-align:center;line-height:1.25;font-variant-numeric:tabular-nums}}
.div-hex{{color:#fff;background:rgba(167,139,250,0.14)}}
.div-hex-interp{{color:var(--muted);font-size:9px;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.div-fortune-summary{{background:rgba(251,191,36,0.12);border-color:rgba(251,191,36,0.3);color:#fbbf24}}
/* Fortune level colors */
.fortune-great{{background:rgba(52,211,153,0.15);border-color:rgba(52,211,153,0.4);color:#34d399;font-weight:700}}
.fortune-good{{background:rgba(74,222,128,0.12);border-color:rgba(74,222,128,0.35);color:#4ade80}}
.fortune-small-good{{background:rgba(167,139,250,0.12);border-color:rgba(167,139,250,0.3);color:#a78bfa}}
.fortune-neutral{{background:rgba(255,255,255,0.05);border-color:rgba(255,255,255,0.12);color:var(--muted)}}
.fortune-small-bad{{background:rgba(251,146,60,0.12);border-color:rgba(251,146,60,0.3);color:#fb923c}}
.fortune-bad{{background:rgba(239,68,68,0.12);border-color:rgba(239,68,68,0.3);color:#ef4444}}
.fortune-terrible{{background:rgba(220,38,38,0.18);border-color:rgba(220,38,38,0.45);color:#dc2626;font-weight:700}}
/* Match-specific narrative */
.div-match-narrative{{
  margin-top:6px;padding:8px 10px;background:rgba(8,12,24,0.28);
  border:1px solid rgba(167,139,250,0.12);border-radius:6px;
  font-size:11px;line-height:1.65;color:var(--text-secondary);
  text-wrap:pretty;display:-webkit-box;-webkit-line-clamp:3;
  -webkit-box-orient:vertical;overflow:hidden
}}
.div-desc{{color:var(--text);background:rgba(8,12,24,0.28);border:1px solid rgba(255,255,255,0.05);
  border-radius:6px;padding:7px 9px;font-size:11px;line-height:1.55;text-wrap:pretty;min-height:42px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}

.scoreline-dist-row{{margin-top:12px;border-top:1px dashed var(--border);padding-top:10px}}
.dist-list{{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}}
.dist-item{{flex:1;display:flex;align-items:center;justify-content:center;gap:6px;
  background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:4px 6px;font-size:11px;transition:all 0.2s;cursor:help;min-width:65px}}
.dist-item:hover{{border-color:var(--cyan);background:var(--cyan-dim)}}
.dist-score{{color:#fff;font-weight:700;font-family:var(--font-display)}}
.dist-prob{{color:var(--cyan);font-weight:600;font-size:10px}}

/* 鈹€鈹€ Schedule Grid 鈹€鈹€ */
.schedule-subtabs{{display:flex;gap:4px;margin-bottom:18px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:var(--radius);padding:4px;width:fit-content;backdrop-filter:blur(8px)}}
.schedule-subtabs .subtab{{border:0;background:transparent;font-family:var(--font);font-size:12px;font-weight:600;color:var(--muted);padding:6px 16px;border-radius:6px;cursor:pointer;transition:all .2s;white-space:nowrap}}
.schedule-subtabs .subtab:hover{{color:var(--text)}}
.schedule-subtabs .subtab.active{{background:rgba(34,211,238,0.1);color:var(--cyan);box-shadow:inset 0 0 10px rgba(34,211,238,0.08)}}
.schedule-subpanel{{display:none}}
.schedule-subpanel.active{{display:block}}
.ko-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px}}
.ko-grid .sch-match{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;backdrop-filter:blur(12px);box-shadow:var(--shadow);transition:all .25s;display:flex;flex-direction:column}}
.ko-grid .sch-match:hover{{border-color:rgba(34,211,238,0.15);box-shadow:var(--glow-cyan)}}
.ko-grid .sch-row-top{{border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}}
.ko-grid .sch-teams{{font-weight:700;font-size:13px;color:var(--text)}}

.schedule-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
.group-block{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  box-shadow:var(--shadow);overflow:hidden;backdrop-filter:blur(12px)}}
.gb-header{{display:flex;align-items:center;gap:10px;padding:16px 18px;
  background:linear-gradient(135deg,rgba(34,211,238,0.12),rgba(167,139,250,0.08));border-bottom:1px solid var(--border)}}
.gb-letter{{font-family:var(--font-display);font-size:24px;font-weight:700;color:#fff}}
.gb-phase{{font-size:11px;font-weight:600;color:var(--muted);letter-spacing:.06em;text-transform:uppercase}}
.gb-teams{{padding:8px 18px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border)}}
.gb-matches{{padding:4px 0}}
.sch-match{{display:flex;flex-direction:column;padding:10px 18px;border-bottom:1px solid var(--border)}}
.sch-match:last-child{{border-bottom:0}}
.sch-match.match-played{{background:rgba(255,255,255,0.01)}}
.sch-row-top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}}
.sch-teams{{flex:1;font-weight:600;font-size:12px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sch-time{{color:var(--muted);font-size:10px;font-variant-numeric:tabular-nums;flex-shrink:0;margin-left:8px}}
.sch-row-bottom{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}}
.sch-tag{{display:inline-flex;align-items:center;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;white-space:nowrap}}
.sch-tag-pred{{color:#c084fc;background:rgba(167,139,250,0.08);border:1px solid rgba(167,139,250,0.2)}}
.sch-tag-actual{{color:#38bdf8;background:rgba(34, 211, 238, 0.08);border:1px solid rgba(34, 211, 238, 0.2)}}
.sch-tag-truth{{color:#e2e8f0;background:rgba(148,163,184,0.08);border:1px solid rgba(148,163,184,0.18)}}
.sch-tag-none{{color:var(--muted);background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05)}}
.sch-tag-pending{{color:var(--muted);background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05);font-style:italic}}
.sch-tag-perfect{{color:#4ade80;background:rgba(74, 222, 128, 0.08);border:1px solid rgba(74, 222, 128, 0.2)}}
.sch-tag-result{{color:#fbbf24;background:rgba(251, 191, 36, 0.08);border:1px solid rgba(251, 191, 36, 0.2)}}
.sch-tag-miss{{color:#f87171;background:rgba(248, 113, 113, 0.08);border:1px solid rgba(248, 113, 113, 0.2)}}


/* 鈹€鈹€ Tuning Panel 鈹€鈹€ */
.tuning-container{{display:grid;grid-template-columns:1.5fr 1fr;gap:20px}}
.tuning-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}}
.tuning-card h2{{font-size:16px;font-weight:700;margin-bottom:18px;color:#fff;display:flex;align-items:center;gap:8px}}
.tuning-instruction{{font-size:12px;color:var(--muted);background:rgba(34,211,238,0.04);border:1px solid rgba(34,211,238,0.15);border-radius:var(--radius-sm);padding:12px 16px;margin-bottom:20px;line-height:1.6}}
.slider-group{{margin-bottom:18px}}
.slider-label-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:12px;font-weight:600;color:var(--text)}}
.slider-desc{{font-size:10px;color:var(--muted);margin-bottom:4px}}
.slider-input-row{{display:flex;align-items:center;gap:12px}}
.slider-input-row input[type="range"]{{flex:1;accent-color:var(--cyan);background:rgba(255,255,255,0.06);height:6px;border-radius:99px;border:none;outline:none}}
.slider-val-box{{font-size:12px;font-weight:700;color:var(--cyan);min-width:48px;text-align:right;font-variant-numeric:tabular-nums}}
.json-preview-box{{background:rgba(0,0,0,0.3);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px;font-family:monospace;font-size:11px;color:#cbd5e1;overflow-x:auto;max-height:380px;margin-bottom:14px;white-space:pre-wrap}}
.tuning-actions{{display:flex;gap:10px}}
.tuning-btn{{flex:1;border:none;background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;font-family:var(--font);font-size:12px;font-weight:700;padding:10px 18px;border-radius:8px;cursor:pointer;transition:all .2s;text-align:center;text-decoration:none;display:inline-block}}
.tuning-btn:hover{{transform:translateY(-1px);box-shadow:0 0 16px rgba(34,211,238,0.25)}}
.tuning-btn-secondary{{background:rgba(255,255,255,0.04);border:1px solid var(--border);color:var(--text)}}
.tuning-btn-secondary:hover{{background:rgba(255,255,255,0.08);border-color:var(--muted);box-shadow:none}}

/* 鈹€鈹€ Charts 鈹€鈹€ */
.charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.chart-box{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}}
.chart-box h3{{font-size:13px;font-weight:700;margin-bottom:14px;padding-left:10px;
  border-left:3px solid var(--cyan);color:#fff;letter-spacing:.02em}}
.chart-dom{{width:100%;height:300px}}

/* 鈹€鈹€ Daily Stats (in calibration) 鈹€鈹€ */
.panel{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}}
.panel h2{{font-size:15px;font-weight:700;margin-bottom:14px;padding-left:10px;border-left:3px solid var(--purple);color:#fff}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.stat-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:16px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}}
.stat-head{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);
  padding-bottom:8px;margin-bottom:8px}}
.stat-head h3{{font-size:13px;font-weight:700;color:#fff}}.stat-head span{{font-size:11px;color:var(--muted)}}
.stat-body{{display:flex;gap:14px;font-size:11px;color:var(--muted)}}
.stat-error{{margin-top:8px;font-size:10px;color:var(--red);background:var(--red-dim);
  padding:6px 10px;border-radius:var(--radius-sm);border:1px solid rgba(248,113,113,0.15)}}
.phase-stats-section{{margin-bottom:18px}}
.daily-stats-section{{margin-top:18px}}
.phase-stats-title{{font-size:12px;font-weight:700;color:var(--cyan);letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px}}
.phase-stats-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
.phase-stat-card{{background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:var(--radius);
  padding:14px 16px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}}
.phase-stat-head{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
.phase-stat-head h3{{font-size:13px;font-weight:700;color:#fff}}
.phase-stat-head span{{font-size:11px;color:var(--muted)}}
.phase-stat-grid{{display:grid;grid-template-columns:1fr;gap:6px;font-size:11px;color:var(--muted)}}
.phase-stat-grid span{{display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.02);
  border:1px solid rgba(255,255,255,0.04);border-radius:6px;padding:6px 8px}}

/* 鈹€鈹€ Drawer 鈹€鈹€ */
.overlay{{position:fixed;inset:0;background:rgba(0,0,0,0.55);backdrop-filter:blur(6px);
  z-index:100;opacity:0;pointer-events:none;transition:opacity .25s}}
.overlay.open{{opacity:1;pointer-events:auto}}
.drawer{{position:fixed;top:0;right:-500px;width:min(500px,100%);height:100%;
  background:rgba(8,12,24,0.97);backdrop-filter:blur(20px);
  box-shadow:-10px 0 50px rgba(0,0,0,0.5);z-index:101;
  transition:right .3s cubic-bezier(.4,0,.2,1);overflow-y:auto;border-left:1px solid var(--border)}}
.drawer.open{{right:0}}
.drawer-head{{padding:24px;border-bottom:1px solid var(--border);position:sticky;top:0;
  background:rgba(8,12,24,0.98);z-index:1}}
.drawer-head h2{{font-family:var(--font-display);font-size:20px;font-weight:700;margin-top:4px;color:#fff}}
.drawer-close{{position:absolute;top:20px;right:20px;border:0;background:rgba(255,255,255,0.05);
  width:32px;height:32px;border-radius:50%;cursor:pointer;display:flex;align-items:center;
  justify-content:center;font-size:18px;color:var(--muted);border:1px solid var(--border)}}
.drawer-close:hover{{color:#fff;background:rgba(255,255,255,0.1)}}
.drawer-body{{padding:24px}}
.d-section{{margin-bottom:24px}}
.d-section h3{{font-size:12px;font-weight:700;color:var(--cyan);margin-bottom:10px;padding-bottom:6px;
  border-bottom:1px solid var(--border);letter-spacing:.06em;text-transform:uppercase}}
.xg-bar{{margin-bottom:12px}}
.xg-labels{{display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;color:var(--muted)}}
.xg-labels b{{color:#fff}}
.xg-track{{height:8px;background:rgba(255,255,255,0.04);border-radius:4px;overflow:hidden;display:flex}}
.xg-home{{background:linear-gradient(90deg,var(--cyan),rgba(34,211,238,0.6));height:100%}}
.xg-away{{background:linear-gradient(90deg,var(--purple),rgba(167,139,250,0.6));height:100%}}
.layer-item{{background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px;margin-bottom:6px}}
.layer-head{{display:flex;justify-content:space-between;font-size:11px;font-weight:700;margin-bottom:3px}}
.layer-head span:last-child{{color:var(--muted)}}
.layer-verdict{{font-size:11px;color:var(--muted);line-height:1.5}}
.form-dots{{display:flex;gap:4px}}
.form-dot{{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700}}
.form-w{{background:var(--green-dim);color:var(--green);border:1px solid rgba(52,211,153,0.2)}}
.form-l{{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,113,113,0.2)}}
.form-d{{background:rgba(255,255,255,0.03);color:var(--muted);border:1px solid var(--border)}}

.empty-state{{text-align:center;color:var(--muted);padding:40px;font-size:13px}}
.hidden{{display:none!important}}
.footer{{text-align:center;color:var(--muted);font-size:11px;margin-top:48px;line-height:1.7;letter-spacing:.02em}}

/* 鈹€鈹€ Governance Styles 鈹€鈹€ */
.gov-layout {{
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 20px;
  margin-bottom: 24px;
}}
.gov-col {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(12px);
  position: relative;
  overflow: hidden;
}}
.gov-col::before {{
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--cyan), transparent);
  opacity: 0.5;
}}
.gov-col.actions-col::before {{
  background: linear-gradient(90deg, transparent, var(--purple), transparent);
}}
.gov-col h2 {{
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  margin-bottom: 18px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.action-list, .issue-list {{
  display: flex;
  flex-direction: column;
  gap: 12px;
}}
.action-item {{
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: all 0.2s;
}}
.action-item:hover {{
  border-color: rgba(167, 139, 250, 0.3);
  background: rgba(167, 139, 250, 0.02);
}}
.action-desc {{
  font-size: 12px;
  color: var(--text);
  flex: 1;
  line-height: 1.5;
}}
.action-time {{
  font-size: 10px;
  color: var(--muted);
  white-space: nowrap;
}}
.issue-item {{
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  transition: all 0.2s;
}}
.issue-item:hover {{
  border-color: rgba(34, 211, 238, 0.3);
  background: rgba(34, 211, 238, 0.02);
}}
.issue-info {{
  display: flex;
  align-items: center;
  gap: 10px;
}}
.issue-tag {{
  font-family: monospace;
  font-size: 12px;
  color: var(--cyan);
  background: var(--cyan-dim);
  padding: 2px 6px;
  border-radius: 4px;
}}
.issue-count {{
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
}}
.badge {{
  font-size: 9px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  white-space: nowrap;
}}
.badge-p0 {{ background: rgba(248, 113, 113, 0.15); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.25); }}
.badge-p1 {{ background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.25); }}
.badge-p2 {{ background: rgba(96, 165, 250, 0.15); color: #60a5fa; border: 1px solid rgba(96, 165, 250, 0.25); }}
.badge-p3 {{ background: rgba(100, 116, 139, 0.15); color: #94a3b8; border: 1px solid rgba(100, 116, 139, 0.25); }}
.badge-high {{ background: rgba(248, 113, 113, 0.15); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.25); }}
.badge-medium {{ background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.25); }}
.badge-low {{ background: rgba(52, 211, 153, 0.15); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.25); }}

@media(max-width:900px){{
  .metrics{{grid-template-columns:repeat(2,1fr)}}
  .schedule-grid{{grid-template-columns:1fr 1fr}}
  .charts-row{{grid-template-columns:1fr}}
  .tabs{{width:100%}}
  .source-stack{{justify-content:flex-start}}
  .mc-top{{grid-template-columns:1fr}}
  .mc-badges{{justify-content:flex-start;max-width:none}}
}}
@media(max-width:600px){{
  .metrics{{grid-template-columns:repeat(2,1fr)}}
  .schedule-grid{{grid-template-columns:1fr}}
  .hero{{padding:44px 0 34px}}
  .hero h1{{font-size:30px}}
  .hero-sub{{font-size:12px}}
  .tabs{{padding:5px}}
  .tab{{padding:10px 15px;font-size:12px}}
  .cmp-bar{{padding:18px 18px 16px}}
  .mc-score{{font-size:34px}}
  .mc-team{{grid-template-columns:1fr;gap:10px}}
  .team-name{{font-size:20px}}
  .mc-vs{{justify-self:center}}
  .mc-body{{padding:24px 16px 18px}}
  .mc-bottom{{padding:16px 16px 20px}}
  .date-toolbar{{padding:12px 12px 2px}}
  .compare-head,.compare-row{{grid-template-columns:64px minmax(0,1fr) minmax(0,1fr);gap:8px}}
  .compare-score{{font-size:24px;min-height:48px}}
  .score-compare-knockout .compare-score{{font-size:18px;min-height:52px}}
  .score-compare-knockout .compare-row:last-child .compare-score{{font-size:15px}}
  .meta-row{{grid-template-columns:1fr}}
  .ko-summary{{grid-template-columns:1fr 1fr}}
  .date-toolbar button{{min-width:104px}}
  .div-head{{grid-template-columns:1fr}}
}}

/* ── Observation Tab ── */
.obs-layout{{display:flex;flex-direction:column;gap:20px}}
.obs-health-row{{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:14px}}
.obs-health-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px;text-align:center;backdrop-filter:blur(12px);box-shadow:var(--shadow);
  position:relative;overflow:hidden}}
.obs-health-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--cyan),transparent);opacity:.5}}
.obs-health-card.obs-health-mini::before{{background:linear-gradient(90deg,transparent,var(--purple),transparent)}}
.obs-health-label{{font-size:11px;font-weight:600;color:var(--muted);letter-spacing:.06em;
  text-transform:uppercase;margin-bottom:8px}}
.obs-health-score{{font-size:42px;font-weight:700;font-family:var(--font-display);
  color:var(--cyan);font-variant-numeric:tabular-nums;line-height:1.1}}
.obs-health-sub{{font-size:10px;color:var(--muted);margin-top:6px;letter-spacing:.03em}}
.obs-charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.obs-chart-box{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:18px 20px;backdrop-filter:blur(12px);box-shadow:var(--shadow)}}
.obs-chart-box h3{{font-size:13px;font-weight:700;color:var(--purple);margin-bottom:12px;letter-spacing:.04em}}
.obs-chart-dom{{width:100%;height:280px}}
@media(max-width:900px){{
  .obs-health-row{{grid-template-columns:1fr 1fr}}
  .obs-charts-row{{grid-template-columns:1fr}}
}}
@media(max-width:600px){{
  .obs-health-row{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<script type="application/json" id="dashBootstrap">{bootstrap_json}</script>

<div class="shell">
  <div class="hero">
    <span class="hero-eyebrow">2026 FIFA 世界杯</span>
    <h1>AI 章鱼哥预测看板</h1>
    <p class="hero-sub">数据模型 + 天纪气运修正 | 生成于 <span id="generatedAt">{generated or '--'}</span></p>
  </div>

  <div class="toolbar-actions">
    <div id="loadStatus" class="status-pill is-loading">正在加载数据...</div>
    <button class="filter-btn" id="btn-refresh-data">刷新数据</button>
  </div>

  <div class="metrics" id="metricsRoot">
    <div class="panel-placeholder">正在加载指标数据...</div>
  </div>

  <div id="comparisonRoot"></div>

  <div class="tabs">
    <button class="tab active" data-tab="predictions">对局预测</button>
    <button class="tab" data-tab="schedule">赛程安排</button>
    <button class="tab" data-tab="stats">准确率校准</button>
    <button class="tab" data-tab="governance">系统治理</button>
    <button class="tab" data-tab="tuning">决策超参</button>
    <button class="tab" data-tab="observation">模型观测</button>
  </div>

  <div id="p-predictions" class="tab-panel active">
    <div class="toolbar date-toolbar" id="dateToolbar">
      <span class="toolbar-label">日期</span>
      <span class="toolbar-status" id="dateToolbarStatus">等待数据</span>
    </div>
    <div class="cards-grid" id="cardsRoot">
      <div class="panel-placeholder">正在加载预测卡片...</div>
    </div>
  </div>

  <div id="p-schedule" class="tab-panel">
    <div id="scheduleRoot" class="panel-placeholder">正在加载赛程...</div>
    <div class="panel schedule-stats-panel">
      <h2>当前赛程统计</h2>
      <div id="scheduleStatsRoot">
        <p class="empty-state">正在加载赛程统计...</p>
      </div>
    </div>
  </div>

  <div id="p-stats" class="tab-panel">
    <div class="charts-row">
      <div class="chart-box">
        <h3>准确率走势</h3>
        <div id="accChart" class="chart-dom"></div>
      </div>
      <div class="chart-box">
        <h3>置信度校准</h3>
        <div id="calChart" class="chart-dom"></div>
      </div>
    </div>
    <div class="chart-box">
      <h3>卦象命中率统计</h3>
      <div id="hexChart" class="chart-dom"></div>
    </div>
  </div>

  <div id="p-governance" class="tab-panel">
    <div class="gov-layout">
      <div class="gov-col issues-col">
        <h2>模型诊断与感知</h2>
        <div class="issue-list" id="issuesRoot">
          <p class="empty-state">正在加载治理反馈...</p>
        </div>
      </div>
      <div class="gov-col actions-col">
        <h2>治理行动与优化计划</h2>
        <div class="action-list" id="actionsRoot">
          <p class="empty-state">正在加载行动项...</p>
        </div>
      </div>
    </div>
    <div class="panel" style="margin-top:16px">
      <h2>统计总览</h2>
      <div id="statsRoot">
        <p class="empty-state">正在加载每日统计...</p>
      </div>
    </div>
  </div>

  <div id="p-tuning" class="tab-panel">
    <div class="tuning-container">
      <div class="tuning-card">
        <h2 style="display:flex; align-items:center; width:100%;">物理层 & 天纪气运权重调节 <span id="sync-status" style="font-size:11px; font-weight:normal; margin-left:auto; display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:4px;"></span></h2>
        <div class="tuning-instruction">
          <strong>操作指南</strong><br>
          通过滑块调整各层决策指标的比重。修改完成后，点击右侧的 <strong>下载 model-hyperparameters.json</strong> 按钮，将文件保存到项目配置位置，下一次预测 Agent 运行时会自动加载新的超参配置。
        </div>

        <div style="border-bottom:1px solid var(--border); padding-bottom:10px; margin-bottom:16px;">
          <h3 style="font-size:13px; color:var(--cyan); margin-bottom:12px;">大类权重配置 (阶段自适应权重，比赛阶段决定数据与天纪的混合比例)</h3>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>物理硬实力模型权重 (Data Weight)</span>
              <span class="slider-val-box" id="val-data-weight">0.60</span>
            </div>
            <div class="slider-desc">控制物理层面对预测结果的影响比重。实际预测中权重随比赛阶段动态调整。</div>
            <div class="slider-input-row">
              <input type="range" id="sl-data-weight" min="0" max="1" step="0.05" value="0.60">
            </div>
          </div>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>天纪气运修正权重 (Divination Weight)</span>
              <span class="slider-val-box" id="val-div-weight">0.40</span>
            </div>
            <div class="slider-desc">控制天纪气运修正对预测结果的干预程度。</div>
            <div class="slider-input-row">
              <input type="range" id="sl-div-weight" min="0" max="1" step="0.05" value="0.40">
            </div>
          </div>
        </div>

        <div style="margin-bottom:24px; background:rgba(0,212,255,0.05); border:1px solid var(--border); border-radius:8px; padding:12px 16px;" id="stage-weight-info">
          <h4 style="font-size:12px; color:var(--cyan); margin:0 0 8px 0;">📊 阶段自适应权重策略</h4>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px 16px; font-size:11px; color:var(--gold);">
            <div>🏟 小组赛 (G): <span style="color:#e040fb;">天纪 65%</span> / 数据 35%</div>
            <div>🏟 小组赛: <span style="color:#e040fb;">天纪 65%</span> / 数据 35%</div>
            <div>🏟 1/16决赛: <span style="color:#e040fb;">天纪 55%</span> / 数据 45%</div>
            <div>🏆 1/8决赛: 数据 55% / <span style="color:#e040fb;">天纪 45%</span></div>
            <div>🏆 1/4决赛: 数据 70% / <span style="color:#e040fb;">天纪 30%</span></div>
          </div>
          <p style="font-size:10px; color:var(--text-secondary); margin:8px 0 0 0;">
            当前看板仅按已公开轮次展示阶段分类。小组赛冷门频发，天纪卦象主导判断；进入淘汰赛后，物理数据权重逐步提升。
            以上滑块仅配置<b>组件权重</b>（排名/阵容/历史等），数据/天纪混合比例仍由<b>比赛阶段</b>自动决定。
          </p>
        </div>

        <div>
          <h3 style="font-size:13px; color:var(--purple); margin-bottom:12px;">物理层各评估维度比重 (相对比例，自动归一化)</h3>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>FIFA 官方排名实力权重</span>
              <span class="slider-val-box" id="val-ranking">0.30</span>
            </div>
            <div class="slider-input-row">
              <input type="range" id="sl-ranking" min="0" max="1" step="0.05" value="0.30">
            </div>
          </div>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>阵容深度评估权重</span>
              <span class="slider-val-box" id="val-squad">0.20</span>
            </div>
            <div class="slider-input-row">
              <input type="range" id="sl-squad" min="0" max="1" step="0.05" value="0.20">
            </div>
          </div>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>历史交锋 (H2H) 权重</span>
              <span class="slider-val-box" id="val-history">0.20</span>
            </div>
            <div class="slider-input-row">
              <input type="range" id="sl-history" min="0" max="1" step="0.05" value="0.20">
            </div>
          </div>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>体能与旅行休整权重</span>
              <span class="slider-val-box" id="val-rest">0.15</span>
            </div>
            <div class="slider-input-row">
              <input type="range" id="sl-rest" min="0" max="1" step="0.05" value="0.15">
            </div>
          </div>

          <div class="slider-group">
            <div class="slider-label-row">
              <span>外部情报完整度权重</span>
              <span class="slider-val-box" id="val-evidence">0.15</span>
            </div>
            <div class="slider-input-row">
              <input type="range" id="sl-evidence" min="0" max="1" step="0.05" value="0.15">
            </div>
          </div>
        </div>
      </div>

      <div class="tuning-card" style="display:flex; flex-direction:column;">
        <h2>配置文件实时预览</h2>
        <div style="font-size:11px; color:var(--muted); margin-bottom:8px;">生成的 JSON 配置：</div>
        <div class="json-preview-box" id="json-preview">{{}}</div>

        <div style="margin-top:auto;">
          <div class="tuning-actions">
            <a href="#" class="tuning-btn" id="btn-download-config">下载配置文件</a>
            <button class="tuning-btn tuning-btn-secondary" id="btn-copy-config">复制配置内容</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div id="p-observation" class="tab-panel">
    <div class="obs-layout">

      <div class="obs-health-row">
        <div class="obs-health-card">
          <div class="obs-health-label">模型健康指数</div>
          <div class="obs-health-score" id="obsHealthScore">--</div>
          <div class="obs-health-sub" id="obsHealthSub">综合命中率 · Brier · 置信校准</div>
        </div>
        <div class="obs-health-card obs-health-mini">
          <div class="obs-health-label">最新赛果命中率</div>
          <div class="obs-health-score" id="obsLatestRHR" style="font-size:28px">--</div>
        </div>
        <div class="obs-health-card obs-health-mini">
          <div class="obs-health-label">最新 Brier 分数</div>
          <div class="obs-health-score" id="obsLatestBrier" style="font-size:28px">--</div>
        </div>
        <div class="obs-health-card obs-health-mini">
          <div class="obs-health-label">累计评估场次</div>
          <div class="obs-health-score" id="obsTotalMatches" style="font-size:28px">--</div>
        </div>
      </div>

      <div class="obs-charts-row">
        <div class="obs-chart-box">
          <h3>命中率趋势</h3>
          <div id="obsHitTrendChart" class="obs-chart-dom"></div>
        </div>
        <div class="obs-chart-box">
          <h3>Brier 分数漂移</h3>
          <div id="obsBrierChart" class="obs-chart-dom"></div>
        </div>
      </div>

      <div class="obs-charts-row">
        <div class="obs-chart-box">
          <h3>置信度校准</h3>
          <div id="obsCalibChart" class="obs-chart-dom"></div>
        </div>
        <div class="obs-chart-box">
          <h3>误差分布</h3>
          <div id="obsErrorChart" class="obs-chart-dom"></div>
        </div>
      </div>

    </div>
  </div>

  <p class="footer">仅供娱乐参考，不构成任何投注建议。<br><span id="footerDisclaimer">数据加载中...</span></p>
</div>

<div class="overlay" id="overlay"></div>
<div class="drawer" id="drawer">
  <div class="drawer-head">
    <button class="drawer-close" id="drawerClose">&times;</button>
    <span class="hero-eyebrow" id="dId"></span>
    <h2 id="dTitle"></h2>
  </div>
  <div class="drawer-body" id="dBody"></div>
</div>

<script>
var BOOTSTRAP=JSON.parse(document.getElementById('dashBootstrap').textContent);
var D={{}};
var uiDefaults={{}};
var curDate='all';
var curDateSet=['all'];
var curScheduleTab='group';
var chartsOk=false;
var obsChartsOk=false;
var refreshTimer=null;
var dashboardInitialized=false;
var tuningInitialized=false;

function escapeHtml(text){{
  var s=String(text===undefined||text===null?'':text);
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

function metricHtml(label,value,detail,extraCls){{
  return '<div class="metric'+(extraCls?' '+extraCls:'')+'">'
    +'<span class="metric-label">'+escapeHtml(label)+'</span>'
    +'<span class="metric-value">'+escapeHtml(value)+'</span>'
    +'<span class="metric-detail">'+escapeHtml(detail||'')+'</span>'
    +'</div>';
}}

function buildDateButton(date,activeDate){{
  return '<button'+(date===activeDate?' class="active"':'')+' data-filter-date="'+escapeHtml(date)+'">'+escapeHtml(date)+'</button>';
}}

var PHASE_LABELS_MAP={{
  all:'总数据',
  group:'小组赛',
  round_of_32:'1/16决赛',
  round_of_16:'1/8决赛',
  quarter_final:'1/4决赛',
  semi_final:'半决赛',
  third_place:'三四名',
  final:'决赛'
}};

var SUBTAB_PHASE_MAP={{
  group:'group',
  r32:'round_of_32',
  r16:'round_of_16',
  qf:'quarter_final'
}};

function pct(value){{
  return (Number(value||0)*100).toFixed(1)+'%';
}}

function phaseDisplayLabel(phase){{
  var key=String(phase||'').trim();
  if(!key)return '';
  return PHASE_LABELS_MAP[key]||key;
}}

function cardDisplayDate(card){{
  return ((card.fixture||{{}}).beijing_date)||card.date||String(card.kickoff_at||'').slice(0,10)||'';
}}

function cardPhaseKey(card){{
  return String(((card.fixture||{{}}).phase)||card.phase||'');
}}

function cardGroupKey(card){{
  return String(((card.fixture||{{}}).group)||card.group||'').trim().toUpperCase();
}}

function isRealCard(card){{
  var source=((card.fixture||{{}}).data_source)||card.data_source||'official';
  return source!=='placeholder';
}}

function filterCardsByDate(cards,date){{
  if(!date||date==='all')return cards.slice();
  var filtered=[];
  for(var i=0;i<cards.length;i++){{
    if(cardDisplayDate(cards[i])===date)filtered.push(cards[i]);
  }}
  return filtered;
}}

function filterCardsByPhase(cards,phaseKey){{
  if(!phaseKey||phaseKey==='all')return cards.slice();
  var filtered=[];
  for(var i=0;i<cards.length;i++){{
    if(cardPhaseKey(cards[i])===phaseKey)filtered.push(cards[i]);
  }}
  return filtered;
}}

function buildStatBucket(bucketKey,label,bucketCards,phaseKey,groupKey){{
  var predicted=0, actual=0, evaluated=0, resultHits=0, scoreHits=0;
  for(var i=0;i<bucketCards.length;i++){{
    var card=bucketCards[i]||{{}};
    var pred=card.prediction||{{}};
    var act=card.actual||{{}};
    var ev=card.evaluation||{{}};
    if(pred.exists)predicted++;
    if(act.exists)actual++;
    if(ev.exists)evaluated++;
    if(ev.result_hit===true)resultHits++;
    if(ev.score_hit===true)scoreHits++;
  }}
  return {{
    bucket_key:bucketKey||'',
    phase:phaseKey||'',
    group:groupKey||'',
    label:label||'未命名',
    total_matches:bucketCards.length,
    predicted_matches:predicted,
    actual_matches:actual,
    evaluated_matches:evaluated,
    result_hits:resultHits,
    score_hits:scoreHits,
    result_hit_rate:evaluated?resultHits/evaluated:0,
    score_hit_rate:evaluated?scoreHits/evaluated:0
  }};
}}

function currentStatsContext(){{
  var allCards=(D.cards||[]).filter(isRealCard);
  var phaseKey=SUBTAB_PHASE_MAP[curScheduleTab]||'group';
  var phaseLabel=PHASE_LABELS_MAP[phaseKey]||phaseKey||'当前阶段';
  var dateCards=filterCardsByDate(allCards,curDate);
  var phaseCards=filterCardsByPhase(allCards,phaseKey);
  var filteredCards=filterCardsByPhase(dateCards,phaseKey);
  var activeCards=filteredCards.length ? filteredCards : phaseCards;
  var labelParts=[];
  if(curDate&&curDate!=='all')labelParts.push(curDate);
  labelParts.push(phaseLabel);
  return {{
    allCards:allCards,
    phaseKey:phaseKey,
    phaseLabel:phaseLabel,
    dateCards:dateCards,
    phaseCards:phaseCards,
    filteredCards:filteredCards,
    activeCards:activeCards,
    activeLabel:labelParts.join(' / '),
    isEmptyFilter:filteredCards.length===0 && curDate && curDate!=='all'
  }};
}}

function renderDynamicMetrics(){{
  var root=document.getElementById('metricsRoot');
  if(!root)return;
  var ctx=currentStatsContext();
  if(!ctx.allCards.length){{
    root.innerHTML='<div class="panel-placeholder">暂无指标数据。</div>';
    return;
  }}
  var stat=buildStatBucket('active-metrics',ctx.activeLabel,ctx.activeCards,ctx.phaseKey,'');
  var total=buildStatBucket('all-metrics','总数据',ctx.allCards,'','');
  var scopeDetail=ctx.activeLabel;
  if(ctx.isEmptyFilter)scopeDetail='当前日期无本阶段比赛，显示 '+ctx.phaseLabel+' 累计';
  var brier='N/A';
  var latest=(D.daily_stats||[])[0]||null;
  if(latest && latest.brier_score_result!==null && latest.brier_score_result!==undefined){{
    brier=Number(latest.brier_score_result).toFixed(4);
  }} else if((D.summary||{{}}).avg_brier_score){{
    brier=Number((D.summary||{{}}).avg_brier_score).toFixed(4);
  }}
  root.innerHTML=
    metricHtml('当前视图', String(stat.total_matches||0), scopeDetail)
    +metricHtml('已预测', String(stat.predicted_matches||0), '总数据 '+String(total.predicted_matches||0)+' 场')
    +metricHtml('完成复盘', String(stat.evaluated_matches||0), '已完赛 '+String(stat.actual_matches||0)+' 场')
    +metricHtml('赛果命中', pct(stat.result_hit_rate||0), String(stat.result_hits||0)+' / '+String(stat.evaluated_matches||0))
    +metricHtml('比分命中', pct(stat.score_hit_rate||0), String(stat.score_hits||0)+' / '+String(stat.evaluated_matches||0))
    +metricHtml('Brier', brier, '最近逐日校准');
}}

function renderPhaseStatCardJs(stat){{
  if(!stat)return '';
  return '<div class="phase-stat-card">'
    +'<div class="phase-stat-head"><h3>'+escapeHtml(stat.label||'')+'</h3><span>'+escapeHtml(String(stat.total_matches||0)+' 场')+'</span></div>'
    +'<div class="phase-stat-grid">'
    +'<span>已预测 '+escapeHtml(stat.predicted_matches||0)+'</span>'
    +'<span>已完赛 '+escapeHtml(stat.actual_matches||0)+'</span>'
    +'<span>已评估 '+escapeHtml(stat.evaluated_matches||0)+'</span>'
    +'<span>赛果 '+escapeHtml(stat.result_hits||0)+' ('+escapeHtml(pct(stat.result_hit_rate||0))+')</span>'
    +'<span>比分 '+escapeHtml(stat.score_hits||0)+' ('+escapeHtml(pct(stat.score_hit_rate||0))+')</span>'
    +'</div>'
    +'</div>';
}}

function renderStatsCardGridJs(stats){{
  if(!stats||!stats.length)return '<p class="empty-state">暂无统计数据。</p>';
  var html='';
  for(var i=0;i<stats.length;i++)html+=renderPhaseStatCardJs(stats[i]);
  return html || '<p class="empty-state">暂无统计数据。</p>';
}}

function renderStatsSectionJs(title,stats){{
  return '<div class="phase-stats-section">'
    +'<div class="phase-stats-title">'+escapeHtml(title)+'</div>'
    +'<div class="phase-stats-grid">'+renderStatsCardGridJs(stats)+'</div>'
    +'</div>';
}}

function renderDailyStatJs(stat){{
  if(!stat)return '';
  var date=stat.stat_date||'--';
  var evaluated=Number(stat.matches_evaluated||0);
  var hits=Number(stat.result_hits||0);
  var scoreHits=Number(stat.score_hits||0);
  var brier=(stat.brier_score_result!==null&&stat.brier_score_result!==undefined)?Number(stat.brier_score_result).toFixed(4):'N/A';
  var topError=stat.top_error?'<div class="stat-error">'+escapeHtml(stat.top_error)+'</div>':'';
  return '<div class="stat-card">'
    +'<div class="stat-head"><h3>'+escapeHtml(date)+'</h3><span>Brier: '+escapeHtml(brier)+'</span></div>'
    +'<div class="stat-body">'
    +'<span>评估 '+escapeHtml(evaluated)+' 场</span>'
    +'<span>赛果 '+escapeHtml(hits)+' ('+escapeHtml(pct(evaluated?hits/evaluated:0))+')</span>'
    +'<span>比分 '+escapeHtml(scoreHits)+' ('+escapeHtml(pct(evaluated?scoreHits/evaluated:0))+')</span>'
    +'</div>'
    +topError
    +'</div>';
}}

function applyScheduleTabSelection(){{
  var activeTab=curScheduleTab||'group';
  var allSubtabs=document.querySelectorAll('.subtab');
  for(var i=0;i<allSubtabs.length;i++){{
    var isActive=allSubtabs[i].getAttribute('data-subtab')===activeTab;
    allSubtabs[i].classList.toggle('active',isActive);
  }}
  var allSubpanels=document.querySelectorAll('.schedule-subpanel');
  for(var j=0;j<allSubpanels.length;j++)allSubpanels[j].classList.remove('active');
  var panel=document.getElementById('sch-'+activeTab);
  if(panel)panel.classList.add('active');
}}

function renderDynamicStats(){{
  var roots=[
    document.getElementById('statsRoot'),
    document.getElementById('scheduleStatsRoot')
  ].filter(Boolean);
  if(!roots.length)return;
  renderDynamicMetrics();
  var allCards=(D.cards||[]).filter(isRealCard);
  if(!allCards.length){{
    for(var emptyIdx=0;emptyIdx<roots.length;emptyIdx++){{
      roots[emptyIdx].innerHTML='<p class="empty-state">暂无统计数据。</p>';
    }}
    return;
  }}
  var phaseKey=SUBTAB_PHASE_MAP[curScheduleTab]||'group';
  var phaseLabel=PHASE_LABELS_MAP[phaseKey]||phaseKey||'当前阶段';
  var dateCards=filterCardsByDate(allCards,curDate);
  var phaseCards=filterCardsByPhase(allCards,phaseKey);
  var phaseDateCards=filterCardsByPhase(dateCards,phaseKey);
  var html='';

  html+=renderStatsSectionJs('总数据', [
    buildStatBucket('all','总数据',allCards,'','')
  ]);

  if(curDate && curDate!=='all'){{
    html+=renderStatsSectionJs('当前比赛日', [
      buildStatBucket('date-'+curDate, curDate, dateCards, '', '')
    ]);
  }}

  html+=renderStatsSectionJs('当前阶段累计', [
    buildStatBucket('phase-'+phaseKey, phaseLabel, phaseCards, phaseKey, '')
  ]);

  if(curDate && curDate!=='all'){{
    html+=renderStatsSectionJs('当前筛选视图', [
      buildStatBucket('filtered-'+curDate+'-'+phaseKey, curDate+' / '+phaseLabel, phaseDateCards, phaseKey, '')
    ]);
  }}

  if(phaseKey==='group'){{
    var groupStats=[buildStatBucket('group-summary','小组赛汇总',phaseCards,'group','')];
    var groupMap={{}};
    for(var i=0;i<phaseCards.length;i++){{
      var gk=cardGroupKey(phaseCards[i]);
      if(!gk)continue;
      if(!groupMap[gk])groupMap[gk]=[];
      groupMap[gk].push(phaseCards[i]);
    }}
    var groupKeys=Object.keys(groupMap).sort();
    for(var k=0;k<groupKeys.length;k++){{
      var key=groupKeys[k];
      groupStats.push(buildStatBucket('group-'+key, key+'组', groupMap[key], 'group', key));
    }}
    html+=renderStatsSectionJs('小组赛 / 分组汇总', groupStats);
  }}

  var dailyHtml='';
  var dailyStats=D.daily_stats||[];
  for(var d=0;d<dailyStats.length;d++)dailyHtml+=renderDailyStatJs(dailyStats[d]);
  html+='<div class="daily-stats-section">'
    +'<div class="phase-stats-title">逐日统计日志</div>'
    +'<div class="stats-grid">'+(dailyHtml||'<p class="empty-state">暂无每日统计。</p>')+'</div>'
    +'</div>';

  for(var rootIdx=0;rootIdx<roots.length;rootIdx++){{
    roots[rootIdx].innerHTML=html;
  }}
}}

function cardStateLabel(card){{
  var st=card.display_state||'fixture';
  if(st==='evaluated')return card.evaluation&&card.evaluation.label?card.evaluation.label:'已评估';
  if(st==='predicted')return '已预测';
  if(st==='actual_only')return '已完赛';
  if(st==='placeholder')return '占位';
  return '待预测';
}}

function cardResultHtml(card){{
  var act=(card.actual||{{}});
  var st=card.display_state||'fixture';
  if(st!=='evaluated')return '';
  var resultLabel=act.result_label||'';
  if(!resultLabel)return '';
  var result=act.result||'';
  var resultCls='outcome-none';
  if(result==='home_win')resultCls='outcome-home';
  else if(result==='away_win')resultCls='outcome-away';
  else if(result==='draw')resultCls='outcome-draw';
  return '<div class="mc-result"><span class="outcome-badge '+resultCls+'">真实结果 '+escapeHtml(resultLabel)+'</span></div>';
}}

function compareHeadHtml(){{
  return '<div class="compare-head"><span>项目</span><span>预测</span><span>实际</span></div>';
}}

function cardScoreDisplay(card){{
  var pred=(card.prediction||{{}});
  var act=(card.actual||{{}});
  var st=card.display_state||'fixture';
  var phase=(card.phase||(card.fixture||{{}}).phase||'');
  var isKo=['round_of_32','round_of_16','quarter_final','semi_final','third_place','final'].indexOf(String(phase||''))>=0;
  var koPred=pred.knockout||card.knockout_prediction||null;
  var koAct=act.knockout||card.knockout_actual||null;
  if(st==='actual_only'){{
    if(isKo&&koAct){{
      var aReg=((koAct.regular_time||{{}}).score_text)||act.score_text||'-:-';
      var aEt=(koAct.extra_time&&koAct.extra_time.played)?(koAct.extra_time.score_text||'-:-'):'否';
      var aPen=(koAct.penalties&&koAct.penalties.played)?(koAct.penalties.score_text||'-:-'):'否';
      var aAdv=((koAct.advance||{{}}).winner_name)||'待定';
      return '<div class="ko-summary">'
        +'<div class="ko-summary-pill"><b>实际90分</b><span>'+escapeHtml(aReg)+'</span></div>'
        +'<div class="ko-summary-pill"><b>实际加时</b><span>'+escapeHtml(aEt)+'</span></div>'
        +'<div class="ko-summary-pill"><b>实际点球</b><span>'+escapeHtml(aPen)+'</span></div>'
        +'<div class="ko-summary-pill"><b>实际晋级</b><span>'+escapeHtml(aAdv)+'</span></div>'
        +'</div>';
    }}
    return '<div class="mc-score">实际 '+escapeHtml(act.score_text||'-:-')+'</div>';
  }}
  if((st==='fixture'||st==='placeholder') && !(isKo&&koPred&&pred.exists))return '<div class="mc-score">待预测</div>';
  if(st==='evaluated' && isKo && koPred){{
    var pReg=((koPred.regular_time||{{}}).score_text)||pred.score_text||card.score_text||'-:-';
    var aReg=((koAct&&koAct.regular_time?koAct.regular_time:{{}}).score_text)||act.score_text||'-:-';
    var pEt=(koPred.extra_time&&koPred.extra_time.played)?(koPred.extra_time.score_text||'-:-'):'否';
    var aEt=(koAct&&koAct.extra_time&&koAct.extra_time.played)?(koAct.extra_time.score_text||'-:-'):'否';
    var pPen=(koPred.penalties&&koPred.penalties.played)?(koPred.penalties.score_text||'-:-'):'否';
    var aPen=(koAct&&koAct.penalties&&koAct.penalties.played)?(koAct.penalties.score_text||'-:-'):'否';
    var pAdv=((koPred.advance||{{}}).winner_name)||'待定';
    var aAdv=((koAct&&koAct.advance?koAct.advance:{{}}).winner_name)||'待定';
    return '<div class="score-compare score-compare-knockout">'
      +compareHeadHtml()
      +'<div class="compare-row"><span class="compare-label">90分钟</span><span class="compare-score">'+escapeHtml(pReg)+'</span><span class="compare-score compare-actual">'+escapeHtml(aReg)+'</span></div>'
      +'<div class="compare-row"><span class="compare-label">加时</span><span class="compare-score">'+escapeHtml(pEt)+'</span><span class="compare-score compare-actual">'+escapeHtml(aEt)+'</span></div>'
      +'<div class="compare-row"><span class="compare-label">点球</span><span class="compare-score">'+escapeHtml(pPen)+'</span><span class="compare-score compare-actual">'+escapeHtml(aPen)+'</span></div>'
      +'<div class="compare-row"><span class="compare-label">晋级</span><span class="compare-score">'+escapeHtml(pAdv)+'</span><span class="compare-score compare-actual">'+escapeHtml(aAdv)+'</span></div>'
      +'</div>';
  }}
  if(st==='evaluated'){{
    return '<div class="score-compare">'
      +compareHeadHtml()
      +'<div class="compare-row"><span class="compare-label">90分钟</span><span class="compare-score">'+escapeHtml(pred.score_text||card.score_text||'-:-')+'</span><span class="compare-score compare-actual">'+escapeHtml(act.score_text||'-:-')+'</span></div>'
      +'</div>';
  }}
  if(isKo && koPred && pred.exists){{
    var koReg=((koPred.regular_time||{{}}).score_text)||pred.score_text||card.score_text||'-:-';
    var koEt=(koPred.extra_time&&koPred.extra_time.played)?'是':'否';
    var koPen=(koPred.penalties&&koPred.penalties.played)?'是':'否';
    var koAdv=((koPred.advance||{{}}).winner_name)||'待定';
    return '<div class="ko-summary">'
      +'<div class="ko-summary-pill"><b>90分钟</b><span>'+escapeHtml(koReg)+'</span></div>'
      +'<div class="ko-summary-pill"><b>加时</b><span>'+escapeHtml(koEt)+'</span></div>'
      +'<div class="ko-summary-pill"><b>点球</b><span>'+escapeHtml(koPen)+'</span></div>'
      +'<div class="ko-summary-pill"><b>晋级</b><span>'+escapeHtml(koAdv)+'</span></div>'
      +'</div>';
  }}
  return '<div class="mc-score">'+escapeHtml(pred.score_text||card.score_text||'-:-')+'</div>';
}}

function renderCard(card){{
  var fixture=card.fixture||{{}};
  var pred=card.prediction||{{}};
  var evaln=card.evaluation||{{}};
  var hitClass=evaln.hit_class||card.hit_class||'pending';
  if(hitClass==='hit')hitClass='double-hit';
  var dataSource=fixture.data_source||card.data_source||'official';
  var phase=fixture.phase||card.phase||'';
  var phaseLabel=phaseDisplayLabel(phase);
  var group=fixture.group||card.group||'';
  var dateStr=fixture.beijing_date||card.beijing_date||card.date||'';
  var timeStr=fixture.beijing_time||card.beijing_time||'';
  var hRank=fixture.home_ranking||card.home_ranking;
  var aRank=fixture.away_ranking||card.away_ranking;
  var hRankHtml=hRank?'<span class="rank-tag">#'+escapeHtml(hRank)+'</span>':'';
  var aRankHtml=aRank?'<span class="rank-tag">#'+escapeHtml(aRank)+'</span>':'';
  var evalBadge='';
  if(hitClass==='double-hit')evalBadge='<span class="eval-badge eval-double">双中</span>';
  else if(hitClass==='result-hit')evalBadge='<span class="eval-badge eval-result">中赛果</span>';
  else if(hitClass==='miss')evalBadge='<span class="eval-badge eval-miss">偏差</span>';
  var sourceBadge='';
  if(dataSource==='placeholder')sourceBadge='<span class="source-badge source-placeholder" title="淘汰赛队伍待确认，数据为占位">占位</span>';
  else if(phase&&phase.indexOf('group')===0)sourceBadge='<span class="source-badge source-official" title="官方赛程">小组赛</span>';
  else if(phaseLabel)sourceBadge='<span class="source-badge source-official" title="官方赛程">'+escapeHtml(phaseLabel)+'</span>';
  var groupBadge=group?'<span class="mc-group">'+escapeHtml(group)+'组</span>':'';
  var timeHtml=(dateStr||timeStr)?'<div class="mc-time">'+escapeHtml(dateStr)+(timeStr?' '+escapeHtml(timeStr):'')+'</div>':'';

  var xgHtml='';
  if(pred.exists && pred.expected_goals_proxy && pred.expected_goals_proxy.home!==undefined && pred.expected_goals_proxy.away!==undefined){{
    xgHtml='<div class="inline-metric metric-xg"><span>xG</span><strong>'+escapeHtml(Number(pred.expected_goals_proxy.home).toFixed(1))+' vs '+escapeHtml(Number(pred.expected_goals_proxy.away).toFixed(1))+'</strong></div>';
  }}
  var csHtml='';
  if(pred.exists && pred.clean_sheet_probability && pred.clean_sheet_probability.home!==undefined && pred.clean_sheet_probability.away!==undefined){{
    csHtml='<div class="inline-metric metric-clean"><span>零封</span><strong>'+escapeHtml(Math.round((pred.clean_sheet_probability.home||0)*100))+'% / '+escapeHtml(Math.round((pred.clean_sheet_probability.away||0)*100))+'%</strong></div>';
  }}
  var watchHtml='';
  if(pred.exists && pred.watch_points && pred.watch_points.length){{
    var items='';
    for(var i=0;i<Math.min(pred.watch_points.length,3);i++)items+='<li>'+escapeHtml(pred.watch_points[i])+'</li>';
    watchHtml='<div class="watchpoints"><span class="section-label">本场看点</span><ul>'+items+'</ul></div>';
  }}
  return '<article class="match-card'+(dataSource==='placeholder'?' match-card-placeholder':'')+'" data-hit="'+escapeHtml(hitClass)+'" data-match-id="'+escapeHtml(card.match_id||fixture.match_id||'')+'" data-date="'+escapeHtml(dateStr)+'" data-phase="'+escapeHtml(phase)+'" data-source="'+escapeHtml(dataSource)+'">'
    +'<div class="mc-top"><div class="mc-meta"><div class="mc-meta-row"><span class="mc-id">'+escapeHtml(card.match_id||fixture.match_id||'')+'</span>'+groupBadge+'</div>'+timeHtml+'</div><div class="mc-badges">'+evalBadge+sourceBadge+'</div></div>'
    +'<div class="mc-body"><div class="mc-team"><span class="team-name">'+escapeHtml(fixture.home_name||card.home_name||'')+hRankHtml+'</span><span class="mc-vs">vs</span><span class="team-name">'+escapeHtml(fixture.away_name||card.away_name||'')+aRankHtml+'</span></div>'
    +cardScoreDisplay(card)
    +cardResultHtml(card)+'</div>'
    +'<div class="mc-bottom"><div class="conf-row"><span class="conf-label">状态</span><div class="conf-track"><div class="conf-fill conf-'+escapeHtml(pred.confidence||card.confidence||'none')+'"></div></div><span class="conf-val">'+escapeHtml(cardStateLabel(card))+'</span></div>'
    +'<div class="meta-row">'+xgHtml+csHtml+'<div class="inline-metric metric-venue"><span>场馆</span><strong>'+escapeHtml(fixture.venue||card.venue||'')+'</strong></div></div>'
    +watchHtml
    +'</div></article>';
}}

function renderMetrics(){{
  var root=document.getElementById('metricsRoot');
  root.innerHTML=(D.rendered&&D.rendered.metrics_html)||'<div class="panel-placeholder">暂无指标数据。</div>';
}}

function renderComparisonBar(){{
  var root=document.getElementById('comparisonRoot');
  root.innerHTML=(D.rendered&&D.rendered.comparison_bar_html)||'';
}}

function renderDateToolbar(){{
  var toolbar=document.getElementById('dateToolbar');
  var rendered=(D.rendered&&D.rendered.date_buttons_html)||'';
  var dates=((D.summary||{{}}).dates||[]).length;
  toolbar.innerHTML='<span class="toolbar-label">日期</span>'+rendered+'<span class="toolbar-status" id="dateToolbarStatus">'+escapeHtml(dates?('已载入 '+dates+' 个比赛日'):'暂无比赛日')+'</span>';
}}

function renderCards(){{
  var root=document.getElementById('cardsRoot');
  var cards=D.cards||[];
  if(!cards.length){{
    root.innerHTML='<p class="empty-state">暂无预测数据报告。</p>';
    return;
  }}
  var html='';
  for(var i=0;i<cards.length;i++)html+=renderCard(cards[i]);
  root.innerHTML=html;
}}

function renderSchedule(){{
  var root=document.getElementById('scheduleRoot');
  var rendered=(D.rendered&&D.rendered.schedule_html)||'';
  root.className=rendered ? '' : 'panel-placeholder';
  root.innerHTML=rendered || '暂无赛程数据。';
  applyScheduleTabSelection();
}}

function renderGovernance(){{
  var issuesRoot=document.getElementById('issuesRoot');
  var actionsRoot=document.getElementById('actionsRoot');
  issuesRoot.innerHTML=(D.rendered&&D.rendered.issues_html)||'<p class="empty-state">暂无活跃的模型异常反馈。</p>';
  actionsRoot.innerHTML=(D.rendered&&D.rendered.actions_html)||'<p class="empty-state">暂无待处理的模型治理行动项。</p>';
  renderDynamicStats();
}}

function applyPayloadMeta(){{
  document.getElementById('generatedAt').textContent=D.generated_at||'--';
  document.getElementById('footerDisclaimer').textContent=D.disclaimer||'仅供娱乐参考，不构成任何投注建议。';
}}

function renderDashboard(){{
  uiDefaults=D.ui_defaults||{{}};
  curDate=uiDefaults.active_date||curDate||'all';
  curDateSet=curDate&&curDate!=='all'?[curDate]:['all'];
  renderMetrics();
  renderComparisonBar();
  renderDateToolbar();
  renderCards();
  renderSchedule();
  renderGovernance();
  applyPayloadMeta();
  bindDynamicHandlers();
  chartsOk=false;
  obsChartsOk=false;
  if(document.querySelector('.tab.active[data-tab="stats"]'))initCharts();
  if(document.querySelector('.tab.active[data-tab="observation"]'))initObsCharts();
  applyFilters();
  initTuning();
}}

function updateLoadStatus(mode,message){{
  var el=document.getElementById('loadStatus');
  el.className='status-pill';
  if(mode==='loading')el.classList.add('is-loading');
  if(mode==='live')el.classList.add('is-live');
  if(mode==='fallback')el.classList.add('is-fallback');
  if(mode==='error')el.classList.add('is-error');
  el.textContent=message;
}}

function fetchJson(url){{
  return fetch(url, {{cache:'no-store'}}).then(function(res){{
    if(!res.ok)throw new Error('HTTP '+res.status);
    return res.json();
  }});
}}

function isLiveApiMode(){{
  return !!(D.api_capabilities && D.api_capabilities.match_detail);
}}

function setDrawerLoading(matchId,titleText){{
  document.getElementById('dId').textContent=matchId||'';
  document.getElementById('dTitle').textContent=titleText||'正在加载...';
  document.getElementById('dBody').innerHTML='<div class="d-section"><h3>详情加载中</h3><div style="font-size:12px;color:var(--muted)">正在读取最新比赛详情...</div></div>';
  document.getElementById('overlay').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}}

function setDrawerError(matchId,titleText,message){{
  document.getElementById('dId').textContent=matchId||'';
  document.getElementById('dTitle').textContent=titleText||'详情加载失败';
  document.getElementById('dBody').innerHTML='<div class="d-section"><h3>详情加载失败</h3><div style="font-size:12px;color:var(--red)">'+escapeHtml(message||'无法读取比赛详情。')+'</div></div>';
  document.getElementById('overlay').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}}

function findCardByMatchId(matchId){{
  for(var j=0;j<(D.cards||[]).length;j++){{
    if(D.cards[j].match_id===matchId)return D.cards[j];
  }}
  return null;
}}

function fetchMatchDetail(matchId){{
  if(!matchId) return Promise.reject(new Error('missing match_id'));
  return fetchJson(BOOTSTRAP.match_detail_url+encodeURIComponent(matchId));
}}

function loadDashboardData(sourceHint){{
  updateLoadStatus('loading', sourceHint==='manual' ? '正在刷新数据...' : '正在加载数据...');
  return fetchJson(BOOTSTRAP.api_url).then(function(data){{
    D=data||{{}};
    renderDashboard();
    updateLoadStatus('live','实时接口已连接');
    return 'api';
  }}).catch(function(){{
    return fetchJson(BOOTSTRAP.legacy_api_url).then(function(data){{
      D=data||{{}};
      renderDashboard();
      updateLoadStatus('live','实时接口已连接');
      return 'api-legacy';
    }});
  }}).catch(function(){{
    return fetchJson(BOOTSTRAP.static_data_url).then(function(data){{
      D=data||{{}};
      renderDashboard();
      updateLoadStatus('fallback','静态只读模式');
      return 'static';
    }});
  }}).catch(function(err){{
    updateLoadStatus('error','数据加载失败: '+err.message);
    throw err;
  }});
}}

function scheduleRefresh(){{
  if(refreshTimer)clearInterval(refreshTimer);
  refreshTimer=setInterval(function(){{
    if(document.hidden)return;
    loadDashboardData('poll').catch(function(){{}});
  }}, BOOTSTRAP.poll_interval_ms||60000);
}}

function init(){{
  if(dashboardInitialized)return;
  dashboardInitialized=true;
  var tabs=document.querySelectorAll('.tab');
  for(var i=0;i<tabs.length;i++){{
    tabs[i].addEventListener('click',function(e){{
      var t=e.currentTarget;
      var allTabs=document.querySelectorAll('.tab');
      for(var j=0;j<allTabs.length;j++)allTabs[j].classList.remove('active');
      var allPanels=document.querySelectorAll('.tab-panel');
      for(var j=0;j<allPanels.length;j++)allPanels[j].classList.remove('active');
      t.classList.add('active');
      var p=document.getElementById('p-'+t.getAttribute('data-tab'));
      if(p)p.classList.add('active');
      if(t.getAttribute('data-tab')==='stats')initCharts();
      if(t.getAttribute('data-tab')==='observation')initObsCharts();
    }});
  }}

  document.getElementById('btn-refresh-data').addEventListener('click',function(){{
    loadDashboardData('manual').catch(function(){{}});
  }});

  document.addEventListener('visibilitychange',function(){{
    if(!document.hidden)loadDashboardData('visibility').catch(function(){{}});
  }});

  bindStaticHandlers();
  loadDashboardData('initial').then(function(){{scheduleRefresh();}}).catch(function(){{scheduleRefresh();}});
}}

function bindStaticHandlers(){{
  var subtabs=document.querySelectorAll('.subtab');
  for(var i=0;i<subtabs.length;i++){{
    subtabs[i].addEventListener('click',function(e){{
      var t=e.currentTarget;
      curScheduleTab=t.getAttribute('data-subtab')||'group';
      applyScheduleTabSelection();
      renderDynamicStats();
    }});
  }}

  var ov=document.getElementById('overlay');
  ov.addEventListener('click',closeDr);
  document.getElementById('drawerClose').addEventListener('click',closeDr);
}}

function bindDynamicHandlers(){{
  var subtabs=document.querySelectorAll('.subtab');
  for(var i=0;i<subtabs.length;i++){{
    subtabs[i].onclick=function(e){{
      var t=e.currentTarget;
      curScheduleTab=t.getAttribute('data-subtab')||'group';
      applyScheduleTabSelection();
      renderDynamicStats();
    }};
  }}

  var dateBtns=document.querySelectorAll('.toolbar button[data-filter-date]');
  for(var d=0;d<dateBtns.length;d++){{
    dateBtns[d].onclick=function(e){{
      var b=e.currentTarget;
      var allDb=document.querySelectorAll('.toolbar button[data-filter-date]');
      for(var j=0;j<allDb.length;j++)allDb[j].classList.remove('active');
      b.classList.add('active');
      curDate=b.getAttribute('data-filter-date')||'all';
      curDateSet=curDate==='all' ? ['all'] : [curDate];
      applyFilters();
    }};
  }}

  var cards=document.querySelectorAll('.match-card');
  for(var c=0;c<cards.length;c++){{
    cards[c].onclick=function(e){{
      var matchId=e.currentTarget.getAttribute('data-match-id')||'';
      var card=findCardByMatchId(matchId);
      if(!card)return;
      if(isLiveApiMode()){{
        setDrawerLoading(matchId,(card.home_name||'')+' vs '+(card.away_name||''));
        fetchMatchDetail(matchId).then(function(detail){{
          openDr(detail||card);
        }}).catch(function(err){{
          setDrawerError(matchId,(card.home_name||'')+' vs '+(card.away_name||''),err&&err.message?err.message:'无法读取比赛详情');
        }});
        return;
      }}
      openDr(card);
    }};
  }}
}}

function initTuning(){{
  if(tuningInitialized && D.hyperparameters){{
    var existingCw = D.hyperparameters.component_weights || {{}};
    document.getElementById('sl-data-weight').value = D.hyperparameters.data_weight || 0.60;
    document.getElementById('sl-div-weight').value = D.hyperparameters.divination_weight || 0.40;
    document.getElementById('sl-ranking').value = existingCw.ranking_strength || 0.30;
    document.getElementById('sl-squad').value = existingCw.squad_depth || 0.20;
    document.getElementById('sl-history').value = existingCw.historical_proxy || 0.20;
    document.getElementById('sl-rest').value = existingCw.rest_travel || 0.15;
    document.getElementById('sl-evidence').value = existingCw.evidence_completeness || 0.15;
    document.getElementById('btn-copy-config').onclick = function(){{
      var text = document.getElementById('json-preview').textContent;
      navigator.clipboard.writeText(text).then(function(){{ alert('配置已复制到剪贴板'); }});
    }};
  }}
  var dataWeightSlider = document.getElementById('sl-data-weight');
  var divWeightSlider = document.getElementById('sl-div-weight');

  var slRanking = document.getElementById('sl-ranking');
  var slSquad = document.getElementById('sl-squad');
  var slHistory = document.getElementById('sl-history');
  var slRest = document.getElementById('sl-rest');
  var slEvidence = document.getElementById('sl-evidence');

  var isLocalFile = location.protocol === 'file:';
  var statusEl = document.getElementById('sync-status');
  var isStaticMode = isLocalFile || location.protocol !== 'http:' && location.protocol !== 'https:';
  if(!isLocalFile && window.location.pathname && window.location.pathname.indexOf('/dashboard/')>=0 && document.getElementById('loadStatus').textContent.indexOf('静态只读模式')===0){{
    isStaticMode = true;
  }}

  function updateStatusStyle() {{
    if (isStaticMode) {{
      statusEl.innerHTML = '静态只读模式';
      statusEl.style.background = 'rgba(251,191,36,0.15)';
      statusEl.style.border = '1px solid rgba(251,191,36,0.3)';
      statusEl.style.color = 'var(--amber)';
      var dlBtn = document.getElementById('btn-download-config');
      if (dlBtn) dlBtn.textContent = '下载配置文件';
    }} else {{
      statusEl.innerHTML = '实时同步已就绪';
      statusEl.style.background = 'rgba(52,211,153,0.15)';
      statusEl.style.border = '1px solid rgba(52,211,153,0.3)';
      statusEl.style.color = 'var(--green)';
      var dlBtn = document.getElementById('btn-download-config');
      if (dlBtn) dlBtn.textContent = '导出配置文件';
    }}
  }}

  updateStatusStyle();

  var debounceTimeout;
  function saveToServer(configObj){{
    if (isStaticMode) return;
    clearTimeout(debounceTimeout);
    statusEl.style.opacity = '0.6';
    statusEl.innerHTML = '正在同步...';
    debounceTimeout = setTimeout(function(){{
      fetch('/api/save-config', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(configObj)
      }}).then(function(res){{
        if (!res.ok) {{
          throw new Error('Server returned ' + res.status);
        }}
        return res.json();
      }}).then(function(data){{
        statusEl.style.opacity = '1.0';
        if (data.status === 'success'){{
          statusEl.innerHTML = '实时同步成功';
          statusEl.style.background = 'rgba(52,211,153,0.15)';
          statusEl.style.border = '1px solid rgba(52,211,153,0.3)';
          statusEl.style.color = 'var(--green)';
        }} else {{
          statusEl.innerHTML = '同步失败: ' + data.message;
        }}
      }}).catch(function(err){{
        statusEl.style.opacity = '1.0';
        isStaticMode = true;
        updateStatusStyle();
      }});
    }}, 400);
  }}

  function updateJSON(){{
    var dw = parseFloat(dataWeightSlider.value);
    var divw = parseFloat(divWeightSlider.value);

    var r = parseFloat(slRanking.value);
    var sq = parseFloat(slSquad.value);
    var h = parseFloat(slHistory.value);
    var rs = parseFloat(slRest.value);
    var ev = parseFloat(slEvidence.value);

    // Normalize components to sum to 1.0
    var sum = r + sq + h + rs + ev || 1;
    var norm_r = parseFloat((r / sum).toFixed(4));
    var norm_sq = parseFloat((sq / sum).toFixed(4));
    var norm_h = parseFloat((h / sum).toFixed(4));
    var norm_rs = parseFloat((rs / sum).toFixed(4));
    var norm_ev = parseFloat((ev / sum).toFixed(4));

    // Ensure exact sum is 1.0 (deal with rounding)
    var diff = 1.0 - (norm_r + norm_sq + norm_h + norm_rs + norm_ev);
    norm_r = parseFloat((norm_r + diff).toFixed(4));

    document.getElementById('val-data-weight').textContent = dw.toFixed(2);
    document.getElementById('val-div-weight').textContent = divw.toFixed(2);

    document.getElementById('val-ranking').textContent = r.toFixed(2) + ' (比例: ' + norm_r.toFixed(2) + ')';
    document.getElementById('val-squad').textContent = sq.toFixed(2) + ' (比例: ' + norm_sq.toFixed(2) + ')';
    document.getElementById('val-history').textContent = h.toFixed(2) + ' (比例: ' + norm_h.toFixed(2) + ')';
    document.getElementById('val-rest').textContent = rs.toFixed(2) + ' (比例: ' + norm_rs.toFixed(2) + ')';
    document.getElementById('val-evidence').textContent = ev.toFixed(2) + ' (比例: ' + norm_ev.toFixed(2) + ')';

    var config = {{
      "data_weight": dw,
      "divination_weight": divw,
      "component_weights": {{
        "ranking_strength": norm_r,
        "squad_depth": norm_sq,
        "historical_proxy": norm_h,
        "rest_travel": norm_rs,
        "evidence_completeness": norm_ev
      }}
    }};

    var jsonText = JSON.stringify(config, null, 2);
    document.getElementById('json-preview').textContent = jsonText;

    // Download link setup
    var blob = new Blob([jsonText], {{type: 'application/json'}});
    var url = URL.createObjectURL(blob);
    var dlLink = document.getElementById('btn-download-config');
    if (dlLink) {{
      dlLink.href = url;
      dlLink.download = 'model-hyperparameters.json';
    }}

    // Sync to backend in real-time
    saveToServer(config);
  }}

  // Link Parent Weights (sum to 1.0)
  dataWeightSlider.addEventListener('input', function(){{
    divWeightSlider.value = (1.0 - parseFloat(dataWeightSlider.value)).toFixed(2);
    updateJSON();
  }});

  divWeightSlider.addEventListener('input', function(){{
    dataWeightSlider.value = (1.0 - parseFloat(divWeightSlider.value)).toFixed(2);
    updateJSON();
  }});

  [slRanking, slSquad, slHistory, slRest, slEvidence].forEach(function(slider){{
    slider.addEventListener('input', updateJSON);
  }});

  document.getElementById('btn-copy-config').onclick = function(){{
    var text = document.getElementById('json-preview').textContent;
    navigator.clipboard.writeText(text).then(function(){{
      alert('配置已复制到剪贴板');
    }});
  }};

  // Prepopulate from D if exists
  if(D.hyperparameters){{
    dataWeightSlider.value = D.hyperparameters.data_weight || 0.60;
    divWeightSlider.value = D.hyperparameters.divination_weight || 0.40;
    var cw = D.hyperparameters.component_weights || {{}};
    slRanking.value = cw.ranking_strength || 0.30;
    slSquad.value = cw.squad_depth || 0.20;
    slHistory.value = cw.historical_proxy || 0.20;
    slRest.value = cw.rest_travel || 0.15;
    slEvidence.value = cw.evidence_completeness || 0.15;
  }}

  updateJSON();
  tuningInitialized = true;
}}

function applyFilters(){{
  var cards=document.querySelectorAll('.match-card');
  for(var i=0;i<cards.length;i++){{
    var d=cards[i].getAttribute('data-date')||'';
    var ok=curDate==='all'||curDateSet.indexOf('all')>=0||curDateSet.indexOf(d)>=0;
    if(ok){{
      cards[i].classList.remove('hidden');
    }} else cards[i].classList.add('hidden');
  }}
  renderDynamicStats();
}}

function buildPredictionIndex(){{
  var predIndex={{}};
  var cards=D.cards||[];
  for(var i=0;i<cards.length;i++){{
    var c=cards[i];
    var scoreText=c.score_text||'';
    var parts=scoreText.split('-');
    predIndex[c.match_id]= {{
      predicted_result: c.predicted_result||'',
      score: {{
        home: parts.length>1 ? parts[0] : null,
        away: parts.length>1 ? parts[parts.length-1] : null
      }}
    }};
  }}
  return predIndex;
}}

function closeDr(){{
  document.getElementById('overlay').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
}}

function renderWeather(ctx, venueName){{
  if(!ctx || !ctx.venue_context)return '<div class="d-section"><h3>比赛环境 & 气候</h3><div style="font-size:11px;color:var(--muted)">场馆：'+(venueName||'未知')+' | 天气：暂无气象建模数据</div></div>';
  var vc = ctx.venue_context;
  var temp = vc.june_temp_c !== undefined ? vc.june_temp_c + ' C' : '未知';
  var alt = vc.altitude_m !== undefined ? vc.altitude_m + ' m' : '未知';
  var cli = vc.climate_profile || '未知';

  var cliMap = {{
    'high_altitude_mild': 'high altitude mild',
    'warm_highland': 'warm highland',
    'hot_semidry': 'hot semidry',
    'temperate_lakeside': 'temperate lakeside',
    'warm_humid': 'warm humid',
    'mild_marine': 'mild marine',
    'mild_coastal': 'mild coastal',
    'cool_marine': 'cool marine',
    'warm_temperate': 'warm temperate',
    'temperate': 'temperate',
    'hot_humid': 'hot humid',
    'hot_inland': 'hot inland',
    'warm_inland': 'warm inland'
  }};
  var cliZh = cliMap[cli] || cli;

  return '<div class="d-section"><h3>比赛环境 & 气候</h3>'
    +'<div style="font-size:11px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px;">'
    +'<div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span>场馆城市</span><strong style="color:#fff">'+vc.city+', '+vc.country+'</strong></div>'
    +'<div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span>6月均温</span><strong style="color:var(--cyan)">'+temp+'</strong></div>'
    +'<div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span>场馆海拔</span><strong style="color:var(--purple)">'+alt+'</strong></div>'
    +'<div style="display:flex;justify-content:space-between;"><span>气候特征</span><strong style="color:var(--green)">'+cliZh+'</strong></div>'
    +'</div></div>';
}}

function renderRoster(players){{
  if(!players||!players.length)return '<div style="font-size:11px;color:var(--muted)">暂无阵容数据</div>';
  var h='<div class="roster-list" style="font-size:11px; max-height:200px; overflow-y:auto; padding-right:4px;">';
  for(var i=0;i<players.length;i++){{
    var p=players[i];
    h+='<div style="display:flex; justify-content:space-between; margin-bottom:4px; padding-bottom:2px; border-bottom:1px solid rgba(255,255,255,0.02);">'
      +'<span style="color:var(--cyan); font-weight:bold; min-width:18px;">'+p.shirt_number+'</span>'
      +'<span style="color:var(--muted); min-width:24px; margin-right:6px;">['+p.position+']</span>'
      +'<span style="color:#fff; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="'+p.name+'">'+p.name+'</span>'
      +'</div>';
  }}
  h+='</div>';
  return h;
}}

function renderInjuries(injuries, suspensions, teamName){{
  var list = [];
  if(injuries && injuries.length){{
    for(var i=0;i<injuries.length;i++){{
      var inj = injuries[i];
      var sevLabel = inj.severity ? ' ('+inj.severity+')' : '';
      list.push('<span style="color:var(--red)">[伤]</span> ' + inj.player_name + sevLabel + (inj.type ? ' - ' + inj.type : ''));
    }}
  }}
  if(suspensions && suspensions.length){{
    for(var i=0;i<suspensions.length;i++){{
      var susp = suspensions[i];
      list.push('<span style="color:var(--amber)">[停]</span> ' + susp.player_name + (susp.reason ? ' - ' + susp.reason : ''));
    }}
  }}
  if(!list.length)return '<div style="font-size:11px;color:var(--muted);margin-bottom:6px">'+teamName+': 暂无伤停信息</div>';

  var r = '<div style="margin-bottom:8px;"><div style="font-size:11px;color:var(--muted);margin-bottom:2px;font-weight:bold;">'+teamName+'</div>';
  for(var i=0; i<list.length; i++){{
    r += '<div style="font-size:11px;color:#e8ecf4;margin-bottom:2px;">'+list[i]+'</div>';
  }}
  r += '</div>';
  return r;
}}

function renderNews(newsList){{
  if(!newsList||!newsList.length)return '<div style="font-size:11px;color:var(--muted)">暂无相关动态新闻</div>';
  var r='<div style="font-size:11px; max-height:150px; overflow-y:auto; padding-right:4px;">';
  for(var i=0;i<newsList.length;i++){{
    var n=newsList[i];
    var sentimentIcon = n.sentiment === 'positive' ? '+' : n.sentiment === 'negative' ? '-' : '=';
    r+='<div style="margin-bottom:6px; padding-bottom:4px; border-bottom:1px solid rgba(255,255,255,0.02);">'
      +'<div style="font-weight:bold;color:#fff;margin-bottom:2px;">'+sentimentIcon+' '+n.headline+'</div>'
      +'<div style="color:var(--muted)">'+n.detail+'</div>'
      +'</div>';
  }}
  r+='</div>';
  return r;
}}

function renderH2H(h2h){{
  if(!h2h||!h2h.length)return '<div style="font-size:11px;color:var(--muted);margin-bottom:8px">暂无历史交锋数据</div>';
  var r='<div style="margin-bottom:12px;">';
  for(var i=0;i<Math.min(h2h.length,5);i++){{
    var m=h2h[i];
    var penStr = (m.home_pen !== null && m.away_pen !== null) ? ' ('+m.home_pen+'-'+m.away_pen+' 点球)' : '';
    r+='<div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px; padding-bottom:2px; border-bottom:1px solid rgba(255,255,255,0.02);">'
      +'<span style="color:var(--muted)">'+m.year+' ['+m.stage+']</span>'
      +'<span style="color:#fff; text-align:center;">'+m.home_team+' <b>'+m.home_goals+'-'+m.away_goals+'</b> '+m.away_team+penStr+'</span>'
      +'</div>';
  }}
  r+='</div>';
  return r;
}}

function openDr(c){{
  document.getElementById('dId').textContent=c.match_id+' / '+(c.group||c.phase||'');
  document.getElementById('dTitle').textContent=c.home_name+' vs '+c.away_name;
  var h='';

  if(c.expected_goals_proxy){{
    var hx=c.expected_goals_proxy.home||0;
    var ax=c.expected_goals_proxy.away||0;
    var s=hx+ax||1;
    h+='<div class="d-section"><h3>预期进球 (xG)</h3><div class="xg-bar">'
      +'<div class="xg-labels"><span>'+c.home_name+' <b>'+hx.toFixed(1)+'</b></span>'
      +'<span>'+c.away_name+' <b>'+ax.toFixed(1)+'</b></span></div>'
      +'<div class="xg-track"><div class="xg-home" style="width:'+(hx/s*100).toFixed(1)+'%"></div>'
      +'<div class="xg-away" style="width:'+(ax/s*100).toFixed(1)+'%"></div></div>'
      +'</div></div>';
  }}

  if(c.scoreline_distribution&&c.scoreline_distribution.length){{
    h+='<div class="d-section"><h3>比分概率分布</h3>';
    for(var i=0;i<Math.min(c.scoreline_distribution.length,4);i++){{
      var d=c.scoreline_distribution[i];
      var sc=d.score||{{}};var p=d.probability||0;
      h+='<div class="dist-row" style="margin-bottom:5px">'
        +'<span class="dist-score">'+sc.home+'-'+sc.away+'</span>'
        +'<div class="dist-track"><div class="dist-fill" style="width:'+(p*100).toFixed(0)+'%"></div></div>'
        +'<span class="dist-pct">'+(p*100).toFixed(0)+'%</span></div>';
    }}
    h+='</div>';
  }}

  // Metaphysics Astrology Divination Section - simplified to hexagram only
  if(c.divination_overlay || c.divination_hexagram){{
    var div = c.divination_overlay || {{}};
    var hex = (div.hexagram_name || div.hexagram || c.divination_hexagram || '未判定').trim();
    // If it looks like a shichen/time value, show "未起卦"
    // Note: valid hexagram names may contain parens e.g. "豫 (Enthusiasm)"
    if(hex.match(/时[\\( （]|周期/) || !hex){{ hex = '未起卦'; }}

    var hexInterp = (div.hexagram_interpretation || '').trim();
    h+='<div class="d-section" style="border:1px solid rgba(167,139,250,0.15); background:rgba(167,139,250,0.02); padding:10px 14px; border-radius:var(--radius); box-shadow:0 0 15px rgba(167,139,250,0.04); position:relative; margin-bottom:14px;">'
      +'<div style="display:flex; align-items:center; gap:8px;">'
      +'<div style="font-size:10px; color:var(--purple); font-weight:700; text-transform:uppercase; letter-spacing:0.06em;">卦象</div>'
      +'<div style="font-size:15px; font-weight:700; color:#fff; font-family:var(--font-display);">'+hex+'</div>'
      +'</div>'
      +(hexInterp ? '<div style="margin-top:4px; font-size:12px; color:rgba(167,139,250,0.8);">卦辞：'+hexInterp+'</div>' : '')
      +'</div>';
  }}

  // 1. Weather and Climate
  h+=renderWeather(c.venue_adaptation_context, c.venue);

  // 2. Rosters side-by-side
  h+='<div class="d-section"><h3>球员阵容</h3>'
    +'<div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">'
    +'<div><div style="font-size:11px; font-weight:bold; color:var(--cyan); margin-bottom:6px;">'+c.home_name+'</div>'+renderRoster(c.home_players)+'</div>'
    +'<div><div style="font-size:11px; font-weight:bold; color:var(--purple); margin-bottom:6px;">'+c.away_name+'</div>'+renderRoster(c.away_players)+'</div>'
    +'</div></div>';

  // 3. Injuries & Suspensions
  h+='<div class="d-section"><h3>伤停情况</h3>';
  h+=renderInjuries(c.home_injuries, c.home_suspensions, c.home_name);
  h+=renderInjuries(c.away_injuries, c.away_suspensions, c.away_name);
  h+='</div>';

  // 4. Live News & Dynamics
  h+='<div class="d-section"><h3>两队近期动态</h3>'+renderNews(c.late_news)+'</div>';

  // 5. Recent Form
  h+='<div class="d-section"><h3>近期战绩</h3>';
  h+=renderForm(c.home_form,c.home_name);
  h+=renderForm(c.away_form,c.away_name);
  h+='</div>';

  // 6. H2H history
  h+='<div class="d-section"><h3>历史交锋</h3>'+renderH2H(c.h2h)+'</div>';

  if(c.analysis_layers&&c.analysis_layers.length){{
    h+='<div class="d-section"><h3>决策分析层</h3>';
    for(var i=0;i<c.analysis_layers.length;i++){{
      var l=c.analysis_layers[i];
      h+='<div class="layer-item"><div class="layer-head"><span>'+l.title+'</span>'
        +'<span>'+(l.confidence||'').toUpperCase()+'</span></div>'
        +'<div class="layer-verdict">'+l.verdict+'</div></div>';
    }}
    h+='</div>';
  }}

  h+='<div class="d-section"><h3>战术雷达图</h3><div id="dRadar" style="width:100%;height:260px"></div></div>';

  document.getElementById('dBody').innerHTML=h;

  setTimeout(function(){{
    var dom=document.getElementById('dRadar');
    if(dom&&typeof echarts!=='undefined'){{
      var ch=echarts.init(dom,null,{{renderer:'canvas'}});
      ch.setOption({{
        color:['#22d3ee','#a78bfa'],
        tooltip:{{trigger:'item',backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}},borderColor:'rgba(255,255,255,0.08)'}},
        legend:{{data:[c.home_name,c.away_name],bottom:0,textStyle:{{color:'#64748b',fontSize:11}}}},
        radar:{{
          indicator:[
            {{name:'进攻',max:100}},{{name:'防守',max:100}},{{name:'中场',max:100}},
            {{name:'Fitness',max:100}},{{name:'Form',max:100}}
          ],
          splitArea:{{areaStyle:{{color:['rgba(255,255,255,0.005)','rgba(255,255,255,0.015)']}}}},
          axisLine:{{lineStyle:{{color:'rgba(255,255,255,0.06)'}}}},
          splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}},
          axisName:{{color:'#64748b',fontSize:10}}
        }},
        series:[{{
          type:'radar',
          data:[
            {{value:[c.home_radar.attack,c.home_radar.defense,c.home_radar.midfield,c.home_radar.fitness,c.home_radar.recent_form],
              name:c.home_name,areaStyle:{{color:'rgba(34,211,238,0.12)'}}}},
            {{value:[c.away_radar.attack,c.away_radar.defense,c.away_radar.midfield,c.away_radar.fitness,c.away_radar.recent_form],
              name:c.away_name,areaStyle:{{color:'rgba(167,139,250,0.12)'}}}}
          ]
        }}]
      }});
    }}
  }},80);

  document.getElementById('overlay').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}}

function renderForm(form,label){{
  if(!form||!form.length)return '<div style="font-size:11px;color:var(--muted);margin-bottom:8px">'+label+': 暂无数据</div>';
  var dots='';
  for(var i=0;i<form.length;i++){{
    var m=form[i];var cls='form-d';
    if(m.outcome==='W')cls='form-w';else if(m.outcome==='L')cls='form-l';
    dots+='<span class="form-dot '+cls+'" title="'+m.date+' vs '+m.opponent+'">'+m.outcome+'</span>';
  }}
  return '<div style="margin-bottom:10px"><div style="font-size:11px;color:var(--muted);margin-bottom:4px">'+label+'</div><div class="form-dots">'+dots+'</div></div>';
}}

function initCharts(){{
  if(chartsOk)return;
  var st=D.daily_stats.slice().reverse();
  var ds=[];var wdl=[];var sca=[];
  for(var i=0;i<st.length;i++){{
    ds.push(st[i].stat_date);
    wdl.push(st[i].result_hit_rate);
    sca.push(st[i].score_hit_rate);
  }}

  var accDom=document.getElementById('accChart');
  if(accDom&&typeof echarts!=='undefined'){{
    echarts.init(accDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'axis',backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}},borderColor:'rgba(34,211,238,0.3)'}},
      legend:{{data:['赛果','比分'],top:0,textStyle:{{color:'#64748b'}}}},
      grid:{{left:'3%',right:'4%',bottom:'3%',containLabel:true}},
      xAxis:{{type:'category',data:ds,axisLabel:{{color:'#64748b',fontSize:10}},axisLine:{{lineStyle:{{color:'rgba(255,255,255,0.06)'}}}}}},
      yAxis:{{type:'value',min:0,max:100,axisLabel:{{formatter:'{{value}}%',color:'#64748b'}},splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}}}},
      series:[
        {{name:'赛果',type:'line',smooth:true,data:wdl,itemStyle:{{color:'#34d399'}},lineStyle:{{width:2.5}},
          areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(52,211,153,0.2)'}},{{offset:1,color:'rgba(52,211,153,0)'}}]}}}}}},
        {{name:'比分',type:'line',smooth:true,data:sca,itemStyle:{{color:'#60a5fa'}},lineStyle:{{width:2.5}},
          areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(96,165,250,0.2)'}},{{offset:1,color:'rgba(96,165,250,0)'}}]}}}}}}
      ]
    }});
  }}

  var calDom=document.getElementById('calChart');
  if(calDom&&typeof echarts!=='undefined'){{
    var latest=st.length>0?st[st.length-1]:{{}};
    echarts.init(calDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}},backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}}}},
      legend:{{data:['实际','期望'],top:0,textStyle:{{color:'#64748b'}}}},
      grid:{{left:'3%',right:'4%',bottom:'3%',containLabel:true}},
      xAxis:{{type:'category',data:['High','Medium','Low'],axisLabel:{{color:'#64748b'}}}},
      yAxis:{{type:'value',min:0,max:100,axisLabel:{{formatter:'{{value}}%',color:'#64748b'}},splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}}}},
      series:[
        {{name:'实际',type:'bar',barWidth:'20%',
          data:[latest.high_confidence_hit_rate||0,latest.medium_confidence_hit_rate||0,latest.low_confidence_hit_rate||0],
          itemStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'#a78bfa'}},{{offset:1,color:'rgba(167,139,250,0.4)'}}]}},borderRadius:[4,4,0,0]}}}},
        {{name:'期望',type:'bar',barWidth:'20%',data:[75,60,45],
          itemStyle:{{color:'rgba(255,255,255,0.06)',borderRadius:[4,4,0,0]}}}}
      ]
    }});
  }}

  var hexDom=document.getElementById('hexChart');
  if(hexDom&&typeof echarts!=='undefined'){{
    var hm={{}};
    for(var i=0;i<D.cards.length;i++){{
      var c=D.cards[i];
      if(c.divination_hexagram){{
        var n=c.divination_hexagram.split(' ')[0];
        if(!hm[n])hm[n]={{h:0,t:0}};hm[n].t++;if(c.result_hit===true)hm[n].h++;
      }}
    }}
    var names=[];var hits=[];var misses=[];
    for(var k in hm){{names.push(k);hits.push(hm[k].h);misses.push(hm[k].t-hm[k].h);}}
    if(!names.length){{names=['N/A'];hits=[0];misses=[0];}}
    echarts.init(hexDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}},backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}}}},
      legend:{{data:['命中','偏差'],top:0,textStyle:{{color:'#64748b'}}}},
      grid:{{left:'3%',right:'4%',bottom:'3%',containLabel:true}},
      xAxis:{{type:'category',data:names,axisLabel:{{color:'#64748b'}}}},
      yAxis:{{type:'value',minInterval:1,splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}}}},
      series:[
        {{name:'命中',type:'bar',stack:'s',data:hits,
          itemStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'#34d399'}},{{offset:1,color:'rgba(52,211,153,0.5)'}}]}}}}}},
        {{name:'偏差',type:'bar',stack:'s',data:misses,
          itemStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'#f87171'}},{{offset:1,color:'rgba(248,113,113,0.5)'}}]}},borderRadius:[4,4,0,0]}}}}
      ]
    }});
  }}

  chartsOk=true;
}}

var obsChartsOk=false;
function initObsCharts(){{
  if(obsChartsOk)return;
  var obs=D.observation||{{}};
  var trends=obs.daily_trends||[];
  var errDist=obs.error_distribution||[];
  var hs=obs.health_score;

  /* Health score display */
  var hsEl=document.getElementById('obsHealthScore');
  if(hsEl){{
    if(hs!==null&&hs!==undefined){{
      hsEl.textContent=hs.toFixed(1);
      if(hs>=70)hsEl.style.color='#34d399';
      else if(hs>=40)hsEl.style.color='#fbbf24';
      else hsEl.style.color='#f87171';
    }} else {{
      hsEl.textContent='N/A';
      hsEl.style.color='var(--muted)';
    }}
  }}

  /* Latest stats mini cards */
  if(trends.length>0){{
    var latest=trends[trends.length-1];
    var rhrEl=document.getElementById('obsLatestRHR');
    if(rhrEl)rhrEl.textContent=(latest.result_hit_rate||0).toFixed(1)+'%';
    var brEl=document.getElementById('obsLatestBrier');
    if(brEl){{
      var bv=latest.brier_score_result;
      brEl.textContent=bv!==null&&bv!==undefined?bv.toFixed(4):'N/A';
    }}
    var tmEl=document.getElementById('obsTotalMatches');
    if(tmEl){{
      var total=0;
      for(var i=0;i<trends.length;i++)total+=(trends[i].matches_evaluated||0);
      tmEl.textContent=total;
    }}
  }}

  /* 1. Hit Rate Trend chart */
  var hitDom=document.getElementById('obsHitTrendChart');
  if(hitDom&&typeof echarts!=='undefined'&&trends.length){{
    var dates=[];var rhr=[];var shr=[];var rhrAvg=[];var cumR=0;
    for(var i=0;i<trends.length;i++){{
      dates.push(trends[i].stat_date);
      var rv=trends[i].result_hit_rate||0;
      var sv=trends[i].score_hit_rate||0;
      rhr.push(rv);shr.push(sv);
      cumR+=rv;rhrAvg.push(parseFloat((cumR/(i+1)).toFixed(1)));
    }}
    echarts.init(hitDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'axis',backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}},borderColor:'rgba(34,211,238,0.3)'}},
      legend:{{data:['赛果命中率','比分命中率','累计均值'],top:0,textStyle:{{color:'#64748b'}}}},
      grid:{{left:'3%',right:'4%',bottom:'3%',containLabel:true}},
      xAxis:{{type:'category',data:dates,axisLabel:{{color:'#64748b',fontSize:10,rotate:30}},axisLine:{{lineStyle:{{color:'rgba(255,255,255,0.06)'}}}}}},
      yAxis:{{type:'value',min:0,max:100,axisLabel:{{formatter:'{{value}}%',color:'#64748b'}},splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}}}},
      series:[
        {{name:'赛果命中率',type:'line',smooth:true,data:rhr,
          itemStyle:{{color:'#34d399'}},lineStyle:{{width:2.5}},
          areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(52,211,153,0.18)'}},{{offset:1,color:'rgba(52,211,153,0)'}}]}}}}}},
        {{name:'比分命中率',type:'line',smooth:true,data:shr,
          itemStyle:{{color:'#60a5fa'}},lineStyle:{{width:2.5}},
          areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(96,165,250,0.15)'}},{{offset:1,color:'rgba(96,165,250,0)'}}]}}}}}},
        {{name:'累计均值',type:'line',data:rhrAvg,
          itemStyle:{{color:'#fbbf24'}},lineStyle:{{width:1.5,type:'dashed'}},symbol:'none'}}
      ]
    }});
  }}

  /* 2. Brier drift chart */
  var brierDom=document.getElementById('obsBrierChart');
  if(brierDom&&typeof echarts!=='undefined'&&trends.length){{
    var bDates=[];var bVals=[];
    for(var i=0;i<trends.length;i++){{
      bDates.push(trends[i].stat_date);
      var b=trends[i].brier_score_result;
      bVals.push(b!==null&&b!==undefined?b:null);
    }}
    echarts.init(brierDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'axis',backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}},borderColor:'rgba(167,139,250,0.3)'}},
      legend:{{data:['Brier 分数','基准线 0.25'],top:0,textStyle:{{color:'#64748b'}}}},
      grid:{{left:'3%',right:'4%',bottom:'3%',containLabel:true}},
      xAxis:{{type:'category',data:bDates,axisLabel:{{color:'#64748b',fontSize:10,rotate:30}},axisLine:{{lineStyle:{{color:'rgba(255,255,255,0.06)'}}}}}},
      yAxis:{{type:'value',min:0,max:1,axisLabel:{{color:'#64748b'}},splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}}}},
      visualMap:{{show:false,dimension:1,pieces:[{{gt:0,lte:0.25,color:'#34d399'}},{{gt:0.25,lte:0.5,color:'#fbbf24'}},{{gt:0.5,lte:1,color:'#f87171'}}]}},
      series:[
        {{name:'Brier 分数',type:'line',smooth:true,data:bVals,connectNulls:true,
          lineStyle:{{width:2.5}},symbolSize:6,
          areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(167,139,250,0.15)'}},{{offset:1,color:'rgba(167,139,250,0)'}}]}}}}}},
        {{name:'基准线 0.25',type:'line',data:[],markLine:{{silent:true,
          data:[{{yAxis:0.25,lineStyle:{{color:'#fbbf24',type:'dashed',width:1.5}},
          label:{{formatter:'基准 0.25',color:'#fbbf24',fontSize:10}}}}]
        }}}}
      ]
    }});
  }}

  /* 3. Confidence calibration chart */
  var calDom=document.getElementById('obsCalibChart');
  if(calDom&&typeof echarts!=='undefined'&&trends.length){{
    /* Aggregate across all daily stats */
    var hSum=0,hCnt=0,mSum=0,mCnt=0,lSum=0,lCnt=0;
    for(var i=0;i<trends.length;i++){{
      var t=trends[i];var n=t.matches_evaluated||1;
      if(t.high_confidence_hit_rate!==null&&t.high_confidence_hit_rate!==undefined){{hSum+=t.high_confidence_hit_rate*n;hCnt+=n;}}
      if(t.medium_confidence_hit_rate!==null&&t.medium_confidence_hit_rate!==undefined){{mSum+=t.medium_confidence_hit_rate*n;mCnt+=n;}}
      if(t.low_confidence_hit_rate!==null&&t.low_confidence_hit_rate!==undefined){{lSum+=t.low_confidence_hit_rate*n;lCnt+=n;}}
    }}
    var hAvg=hCnt>0?(hSum/hCnt):0;
    var mAvg=mCnt>0?(mSum/mCnt):0;
    var lAvg=lCnt>0?(lSum/lCnt):0;
    echarts.init(calDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}},backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}}}},
      legend:{{data:['实际命中率','期望命中率'],top:0,textStyle:{{color:'#64748b'}}}},
      grid:{{left:'3%',right:'4%',bottom:'3%',containLabel:true}},
      xAxis:{{type:'category',data:['高置信','中置信','低置信'],axisLabel:{{color:'#64748b'}}}},
      yAxis:{{type:'value',min:0,max:100,axisLabel:{{formatter:'{{value}}%',color:'#64748b'}},splitLine:{{lineStyle:{{color:'rgba(255,255,255,0.04)'}}}}}},
      series:[
        {{name:'实际命中率',type:'bar',barWidth:'25%',
          data:[
            {{value:parseFloat(hAvg.toFixed(1)),itemStyle:{{color:'#34d399'}}}},
            {{value:parseFloat(mAvg.toFixed(1)),itemStyle:{{color:'#22d3ee'}}}},
            {{value:parseFloat(lAvg.toFixed(1)),itemStyle:{{color:'#fbbf24'}}}}
          ],
          itemStyle:{{borderRadius:[4,4,0,0]}}}},
        {{name:'期望命中率',type:'bar',barWidth:'25%',data:[75,60,45],
          itemStyle:{{color:'rgba(255,255,255,0.06)',borderRadius:[4,4,0,0]}}}}
      ]
    }});
  }}

  /* 4. Error distribution pie chart */
  var errDom=document.getElementById('obsErrorChart');
  if(errDom&&typeof echarts!=='undefined'&&errDist.length){{
    var pieData=[];
    var pieColors=['#34d399','#f87171','#fbbf24','#a78bfa','#60a5fa','#fb923c','#22d3ee','#f472b6'];
    for(var i=0;i<errDist.length;i++){{
      pieData.push({{name:errDist[i].error_type,value:errDist[i].count,
        itemStyle:{{color:pieColors[i%pieColors.length]}}}});
    }}
    echarts.init(errDom,null,{{renderer:'canvas'}}).setOption({{
      backgroundColor:'transparent',
      tooltip:{{trigger:'item',backgroundColor:'#0c1220',textStyle:{{color:'#e8ecf4'}},borderColor:'rgba(255,255,255,0.08)',
        formatter:'{{b}}: {{c}} ({{d}}%)'}},
      legend:{{type:'scroll',bottom:0,textStyle:{{color:'#64748b',fontSize:10}},
        pageTextStyle:{{color:'#64748b'}}}},
      series:[{{
        type:'pie',radius:['35%','65%'],center:['50%','45%'],
        label:{{color:'#e8ecf4',fontSize:11,formatter:'{{b}}\\n{{d}}%'}},
        labelLine:{{lineStyle:{{color:'rgba(255,255,255,0.15)'}}}},
        emphasis:{{itemStyle:{{shadowBlur:10,shadowOffsetX:0,shadowColor:'rgba(0,0,0,0.5)'}}}},
        data:pieData
      }}]
    }});
  }}

  obsChartsOk=true;
}}

init();
</script>
</body>
</html>
"""



def _assemble_dashboard_payload(*, root: Path, edition: str, now: str | None = None, include_local: bool = True) -> dict:
    data_path, html_path = _dashboard_paths(root, edition)
    static_data_path = _dashboard_static_data_path(root, edition)
    payload = build_dashboard_payload(root=root, edition=edition, now=now, include_local=include_local)

    # Inject tournament schedule from match ledger
    ledger = _load_match_ledger(root, edition)
    payload["schedule_data"] = _build_tournament_schedule(ledger)

    ed_root = edition_data_root(root, edition)
    reports_dir = ed_root / "reports"

    # Build evaluation index from DB and ledger for actual scores
    db_path = worldcup_db_path(root, edition)
    eval_index: dict[str, dict] = {}
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "evaluations" in tables:
                for row in cursor.execute("SELECT match_id, actual_score_home, actual_score_away, is_result_correct, is_score_correct, primary_error FROM evaluations").fetchall():
                    mid = row["match_id"]
                    eval_index[mid] = {
                        "actual_home": row["actual_score_home"],
                        "actual_away": row["actual_score_away"],
                        "evaluation": {
                            "result_hit": True if row["is_result_correct"] == 1 else False if row["is_result_correct"] == 0 else None,
                            "score_hit": True if row["is_score_correct"] == 1 else False if row["is_score_correct"] == 0 else None,
                            "primary_error": row["primary_error"]
                        }
                    }
            conn.close()
        except Exception:
            pass

    for m in _canonical_ledger_matches(ledger):
        mid = m.get("match_id", "")
        final = m.get("final_score") or {}
        if m.get("status") == "final" and final:
            ledger_eval = {
                "actual_home": final.get("home"),
                "actual_away": final.get("away"),
                "knockout_actual": _normalize_knockout_payload(
                    m.get("knockout_result"),
                    home_name=_match_team_name(m, "home"),
                    away_name=_match_team_name(m, "away"),
                ),
                "evaluation": m.get("evaluation") or {},
            }
            # Merge: if DB already has evaluation result (result_hit/score_hit),
            # preserve it — ledger evaluation may be empty
            if mid in eval_index:
                existing = eval_index[mid]
                # Keep actual scores from ledger (source of truth)
                if ledger_eval.get("actual_home") is None:
                    ledger_eval["actual_home"] = existing.get("actual_home")
                if ledger_eval.get("actual_away") is None:
                    ledger_eval["actual_away"] = existing.get("actual_away")
                # Merge evaluation: prefer DB result over ledger if DB has data
                existing_ev = existing.get("evaluation", {})
                ledger_ev_ev = ledger_eval.get("evaluation", {})
                if existing_ev.get("result_hit") is not None and ledger_ev_ev.get("result_hit") is None:
                    ledger_eval["evaluation"]["result_hit"] = existing_ev["result_hit"]
                if existing_ev.get("score_hit") is not None and ledger_ev_ev.get("score_hit") is None:
                    ledger_eval["evaluation"]["score_hit"] = existing_ev["score_hit"]
                if existing_ev.get("primary_error") and not ledger_ev_ev.get("primary_error"):
                    ledger_eval["evaluation"]["primary_error"] = existing_ev["primary_error"]
            eval_index[mid] = ledger_eval


    db_data = query_db_data(db_path) if include_local and db_path.exists() else None
    players_by_team = {}
    all_history = []
    team_id_to_name = {}

    def canonical_key(name: str) -> str:
        return name.lower()

    if db_data:
        all_history = load_all_historical_matches(root, edition)
        for p in db_data.get("players", []):
            tid = p["team_id"].lower()
            players_by_team.setdefault(tid, []).append({
                "shirt_number": p["shirt_number"],
                "position": p["position"],
                "name": p["player_name"],
                "club": p["club"],
                "height": p["height_cm"]
            })
        for m in db_data.get("matches", []):
            h_id = m.get("home_team_id")
            if h_id and m.get("home_name_en"):
                team_id_to_name[h_id.lower()] = m.get("home_name_en")
            a_id = m.get("away_team_id")
            if a_id and m.get("away_name_en"):
                team_id_to_name[a_id.lower()] = m.get("away_name_en")
        try:
            from worldcup_history_fetcher import _normalize_key, TEAM_NAME_ALIASES
            def canonical_key_func(name: str) -> str:
                key = _normalize_key(name)
                if key in TEAM_NAME_ALIASES:
                    return _normalize_key(TEAM_NAME_ALIASES[key])
                return key
            canonical_key = canonical_key_func
        except Exception:
            pass

    # Enrich existing DB cards + create new cards from scoring model reports
    existing_ids = {c.get("match_id") for c in payload.get("cards", [])}
    if include_local and reports_dir.exists():
        for report_file in sorted(reports_dir.glob("*-prediction-report.json")):
            report = load_json(report_file, {})
            for pred in report.get("predictions", []):
                mid = pred.get("match_id", "")
                prediction = pred.get("prediction", {}) or {}

                # Enrich existing cards with detail metrics (don't overwrite result/score)
                for card in payload.get("cards", []):
                    if card.get("match_id") == mid:
                        if card.get("prediction_status") == "not_predicted" and prediction.get("result"):
                            home = pred.get("home_team", {}) or {}
                            away = pred.get("away_team", {}) or {}
                            score = prediction.get("score", {}) or {}
                            has_market_odds, market_odds, market_odds_status = _normalize_market_odds_status(
                                pred.get("market_odds"),
                                pred.get("market_odds_status"),
                            )
                            card.update({
                                "prediction_origin": "user_local",
                                "prediction_source": "user_local",
                                "prediction_source_path": _display_path(report_file, root),
                                "data_origin": "user_local",
                                "prediction_status": "locked_pre_match_prediction",
                                "predicted_result": prediction.get("result", ""),
                                "predicted_result_label": OUTCOME_LABELS.get(prediction.get("result", ""), prediction.get("result", "")),
                                "score_text": f"{score.get('home', '-')}-{score.get('away', '-')}",
                                "total_goals": prediction.get("total_goals", "-"),
                                "confidence": prediction.get("confidence", "unknown"),
                                "confidence_label": str(prediction.get("confidence", "unknown")).upper(),
                                "expected_goals_proxy": prediction.get("expected_goals_proxy"),
                                "clean_sheet_probability": prediction.get("clean_sheet_probability"),
                                "scoreline_distribution": prediction.get("scoreline_distribution"),
                                "result_confidence": prediction.get("result_confidence", prediction.get("confidence", "unknown")),
                                "score_confidence": prediction.get("score_confidence", "unknown"),
                                "total_goals_confidence": prediction.get("total_goals_confidence", "unknown"),
                                "confidence_note": prediction.get("confidence_note", ""),
                                "venue_adaptation_context": pred.get("venue_adaptation_context") or prediction.get("venue_adaptation_context"),
                                "referee_analysis": pred.get("referee_analysis"),
                                "play_card": pred.get("play_card", {}),
                                "divination_overlay": pred.get("divination_overlay") or card.get("divination_overlay"),
                                "divination_hexagram": prediction.get("divination_hexagram") or prediction.get("hexagram") or card.get("divination_hexagram", ""),
                                "home_ranking": home.get("ranking", card.get("home_ranking")),
                                "away_ranking": away.get("ranking", card.get("away_ranking")),
                                "evidence_gaps": prediction.get("evidence_gaps", card.get("evidence_gaps", [])),
                                "play_title": (pred.get("play_card") or {}).get("share_title", card.get("play_title", "")),
                                "risk_flags": (pred.get("play_card") or {}).get("risk_flags", card.get("risk_flags", [])),
                                "watch_points": (pred.get("play_card") or {}).get("watch_points", card.get("watch_points", [])),
                                "has_odds": has_market_odds,
                                "market_odds": market_odds,
                                "market_odds_status": market_odds_status,
                                "market_odds_source": market_odds_status.get("source", "missing"),
                                "market_odds_is_mock": bool(market_odds_status.get("is_mock")),
                                "analysis_layers": pred.get("analysis_layers", card.get("analysis_layers", [])),
                            })
                        if prediction.get("scoreline_distribution") and not card.get("scoreline_distribution"):
                            card["scoreline_distribution"] = prediction["scoreline_distribution"]
                        if prediction.get("expected_goals_proxy") and not card.get("expected_goals_proxy"):
                            card["expected_goals_proxy"] = prediction["expected_goals_proxy"]
                        if prediction.get("clean_sheet_probability") and not card.get("clean_sheet_probability"):
                            card["clean_sheet_probability"] = prediction["clean_sheet_probability"]
                        if prediction.get("venue_adaptation_context") and not card.get("venue_adaptation_context"):
                            card["venue_adaptation_context"] = prediction["venue_adaptation_context"]
                        if pred.get("market_odds") or pred.get("market_odds_status"):
                            has_market_odds, market_odds, market_odds_status = _normalize_market_odds_status(
                                pred.get("market_odds"),
                                pred.get("market_odds_status"),
                            )
                            card["has_odds"] = has_market_odds
                            card["market_odds"] = market_odds
                            card["market_odds_status"] = market_odds_status
                            card["market_odds_source"] = market_odds_status.get("source", "missing")
                            card["market_odds_is_mock"] = bool(market_odds_status.get("is_mock"))
                        break

                # Create new card for predictions not yet in DB
                if mid and is_canonical_match(mid) and mid not in existing_ids and prediction.get("result"):
                    home = pred.get("home_team", {}) or {}
                    away = pred.get("away_team", {}) or {}
                    score = prediction.get("score", {}) or {}
                    result = prediction.get("result", "")
                    knockout_prediction = _normalize_knockout_payload(
                        prediction.get("knockout"),
                        home_name=home.get("name", ""),
                        away_name=away.get("name", ""),
                    )
                    has_market_odds, market_odds, market_odds_status = _normalize_market_odds_status(
                        pred.get("market_odds"),
                        pred.get("market_odds_status"),
                    )
                    kickoff_utc = pred.get("kickoff_at", "")
                    # Compute divination overlay for new card
                    divination_overlay = pred.get("divination_overlay")
                    if not divination_overlay or not isinstance(divination_overlay, dict) or not divination_overlay.get("local_kickoff_at"):
                        divination_overlay = compute_tianji_overlay(
                            kickoff_utc,
                            mid,
                            venue=pred.get("venue")
                        )
                        # Also compute I Ching hexagram overlay
                        _dh = (pred.get("home_name_zh") or pred.get("home_name") or (pred.get("home_team",{}) or {}).get("name","")) if isinstance(pred.get("home_team"), dict) else (pred.get("home_name_zh") or pred.get("home_name",""))
                        _da = (pred.get("away_name_zh") or pred.get("away_name") or (pred.get("away_team",{}) or {}).get("name","")) if isinstance(pred.get("away_team"), dict) else (pred.get("away_name_zh") or pred.get("away_name",""))
                        _hex_overlay = compute_divination_overlay(kickoff_utc[:10] if kickoff_utc else "", mid,
                                                                  home_name=_dh, away_name=_da)
                        divination_overlay["hexagram_number"] = _hex_overlay["hexagram_number"]
                        divination_overlay["hexagram_name"] = _hex_overlay["hexagram_name"]
                        divination_overlay["hexagram"] = _hex_overlay["hexagram_name"]
                        divination_overlay["hexagram_interpretation"] = _hex_overlay["interpretation"]
                        divination_overlay["hexagram_home_modifier"] = _hex_overlay["home_modifier"]
                        divination_overlay["hexagram_away_modifier"] = _hex_overlay["away_modifier"]

                    new_card = {
                        "match_id": mid,
                        "divination_overlay": divination_overlay,
                        "date": kickoff_utc[:10] if kickoff_utc else "",
                        "kickoff_at": kickoff_utc,
                        "local_kickoff_at": kickoff_utc,
                        "calculation_timezone": "UTC+8",
                        "venue": pred.get("venue", ""),
                        "group": pred.get("group", ""),
                        "phase": pred.get("phase", "group"),
                        "home_name": home.get("name", ""),
                        "away_name": away.get("name", ""),
                        "predicted_result": result,
                        "predicted_result_label": OUTCOME_LABELS.get(result, result),
                        "score_text": f"{score.get('home', '-')}-{score.get('away', '-')}",
                        "knockout_prediction": knockout_prediction,
                        "total_goals": prediction.get("total_goals", "-"),
                        "confidence": prediction.get("confidence", "unknown"),
                        "confidence_label": (prediction.get("confidence", "unknown")).upper(),
                        "expected_goals_proxy": prediction.get("expected_goals_proxy"),
                        "clean_sheet_probability": prediction.get("clean_sheet_probability"),
                        "scoreline_distribution": prediction.get("scoreline_distribution"),
                        "result_confidence": prediction.get("result_confidence", prediction.get("confidence", "unknown")),
                        "score_confidence": prediction.get("score_confidence", "unknown"),
                        "total_goals_confidence": prediction.get("total_goals_confidence", "unknown"),
                        "confidence_note": prediction.get("confidence_note", ""),
                        "venue_adaptation_context": pred.get("venue_adaptation_context"),
                        "referee_analysis": pred.get("referee_analysis"),
                        "play_card": pred.get("play_card", {}),
                        "divination_hexagram": prediction.get("divination_hexagram") or prediction.get("hexagram") or "",
                        "evaluation_status": "pending_final_score",
                        "evaluation_label": "待开赛",
                        "hit_class": "pending",
                        "result_hit": None,
                        "score_hit": None,
                        "home_colors": "",
                        "away_colors": "",
                        "home_ranking": home.get("ranking"),
                        "away_ranking": away.get("ranking"),
                        "evidence_gaps": [],
                        "play_title": (pred.get("play_card") or {}).get("share_title", ""),
                        "risk_flags": (pred.get("play_card") or {}).get("risk_flags", []),
                        "watch_points": (pred.get("play_card") or {}).get("watch_points", []),
                        "primary_error": "",
                        "has_odds": has_market_odds,
                        "market_odds": market_odds,
                        "market_odds_status": market_odds_status,
                        "market_odds_source": market_odds_status.get("source", "missing"),
                        "market_odds_is_mock": bool(market_odds_status.get("is_mock")),
                        "has_referee": False,
                        "has_news": False,
                        "analysis_layers": [],
                        "home_radar": {"attack": 70, "defense": 70, "midfield": 70, "fitness": 70, "recent_form": 70},
                        "away_radar": {"attack": 70, "defense": 70, "midfield": 70, "fitness": 70, "recent_form": 70},
                        "home_form": [],
                        "away_form": [],
                        "h2h": [],
                        "home_players": [],
                        "away_players": [],
                        "home_injuries": [],
                        "away_injuries": [],
                        "home_suspensions": [],
                        "away_suspensions": [],
                        "late_news": [],
                    }
                    if db_data:
                        _enrich_card_from_sources(
                            new_card,
                            ledger=ledger,
                            ed_root=ed_root,
                            players_by_team=players_by_team,
                            all_history=all_history,
                            db_matches=db_data.get("matches", []),
                            canonical_key_func=canonical_key,
                            team_id_to_name=team_id_to_name,
                        )
                    payload.setdefault("cards", []).append(new_card)
                    existing_ids.add(mid)
                    payload.setdefault("summary", {}).setdefault("dates", [])
                    if new_card.get("date") and new_card.get("data_source") != "placeholder" and new_card["date"] not in payload["summary"]["dates"]:
                        payload["summary"]["dates"].append(new_card["date"])

    _merge_daily_prediction_sources_into_cards(payload, root, edition, include_local=include_local)

    # Post-process all cards: Chinese names, Beijing time, actual scores
    _BJT = timezone(timedelta(hours=8))
    for card in payload.get("cards", []):
        # Chinese team names
        home_id = ""
        away_id = ""
        mid = card.get("match_id", "")
        # Try to extract team IDs from match_id pattern like "2026-GA-01"
        for m in _canonical_ledger_matches(ledger):
            if m.get("match_id") == mid:
                home_id = _match_team_id(m, "home")
                away_id = _match_team_id(m, "away")
                break
        if home_id and home_id.lower() in TEAM_ZH:
            card["home_name"] = TEAM_ZH[home_id.lower()]
        if away_id and away_id.lower() in TEAM_ZH:
            card["away_name"] = TEAM_ZH[away_id.lower()]

        # Beijing time conversion
        kickoff_utc = card.get("kickoff_at", "")
        if kickoff_utc and "T" in str(kickoff_utc):
            try:
                utc_str = str(kickoff_utc).replace("Z", "+00:00")
                dt = datetime.fromisoformat(utc_str).astimezone(_BJT)
                card["beijing_date"] = dt.strftime("%Y-%m-%d")
                card["beijing_time"] = dt.strftime("%H:%M")
                card["date"] = dt.strftime("%Y-%m-%d")
            except Exception:
                card["beijing_date"] = str(kickoff_utc)[:10]
                card["beijing_time"] = ""
        else:
            card["beijing_date"] = str(kickoff_utc)[:10] if kickoff_utc else ""
            card["beijing_time"] = ""

        evidence_odds_status = _daily_evidence_match_odds_status(ed_root, str(card.get("date", ""))[:10], str(mid))
        if evidence_odds_status:
            has_market_odds, market_odds, market_odds_status = evidence_odds_status
            card["has_odds"] = has_market_odds
            card["market_odds"] = market_odds
            card["market_odds_status"] = market_odds_status
            card["market_odds_source"] = market_odds_status.get("source", "missing")
            card["market_odds_is_mock"] = bool(market_odds_status.get("is_mock"))

        # Actual scores from evaluations
        if mid in eval_index:
            ev = eval_index[mid]
            card["actual_score_home"] = ev["actual_home"]
            card["actual_score_away"] = ev["actual_away"]
            if ev.get("knockout_actual"):
                card["knockout_actual"] = ev.get("knockout_actual")
            card["is_completed"] = True
            ev_data = ev.get("evaluation", {})
            card["result_hit"] = ev_data.get("result_hit")
            card["score_hit"] = ev_data.get("score_hit")
            if ev_data.get("knockout_prediction") and not card.get("knockout_prediction"):
                card["knockout_prediction"] = _normalize_knockout_payload(
                    ev_data.get("knockout_prediction"),
                    home_name=str(card.get("home_name") or ""),
                    away_name=str(card.get("away_name") or ""),
                )
            if any(key in ev_data for key in ("regular_time_result_hit", "regular_time_score_hit", "extra_time_hit", "penalties_hit", "advance_hit")):
                card["knockout_evaluation"] = {
                    "regular_time_result_hit": ev_data.get("regular_time_result_hit"),
                    "regular_time_score_hit": ev_data.get("regular_time_score_hit"),
                    "extra_time_hit": ev_data.get("extra_time_hit"),
                    "penalties_hit": ev_data.get("penalties_hit"),
                    "advance_hit": ev_data.get("advance_hit"),
                }
            if card.get("result_hit") is True and card.get("score_hit") is True:
                card["hit_class"] = "double-hit"
                card["evaluation_label"] = "完美双中"
                card["evaluation_status"] = "evaluated"
            elif card.get("result_hit") is True:
                card["hit_class"] = "result-hit"
                card["evaluation_label"] = "仅中赛果"
                card["evaluation_status"] = "evaluated"
            elif card.get("result_hit") is False:
                card["hit_class"] = "miss"
                card["evaluation_label"] = "预测偏差"
                card["evaluation_status"] = "evaluated"
        else:
            card["actual_score_home"] = None
            card["actual_score_away"] = None
            card["is_completed"] = False

    # Load and attach model-hyperparameters.json payload
    hyper_path = edition_data_root(root, edition) / "model-hyperparameters.json"
    hyper_data = load_json(hyper_path, {
        "data_weight": 0.60,
        "divination_weight": 0.40,
        "component_weights": {
            "ranking_strength": 0.30,
            "squad_depth": 0.20,
            "historical_proxy": 0.20,
            "rest_travel": 0.15,
            "evidence_completeness": 0.15
        }
    })
    # Inject stage weight table from worldcup_core
    try:
        from worldcup_core import STAGE_WEIGHT_TABLE
        hyper_data["_stage_weights"] = {k: {"data_weight": v[0], "divination_weight": v[1]} for k, v in STAGE_WEIGHT_TABLE.items()}
    except Exception:
        pass
    payload["hyperparameters"] = hyper_data

    # Final post-process: ensure all divination_overlay text is in Chinese
    # (ledger supplement may re-inject English names from prediction report JSONs)
    # Also normalize hexagram_name to pure Chinese (strip legacy English parens)
    import re as _re_final
    for _fc in payload.get("cards", []):
        _fd = _fc.get("divination_overlay")
        if not isinstance(_fd, dict):
            continue
        _fzh = _fc.get("home_name_zh") or _fc.get("home_name", "") or ""
        _fza = _fc.get("away_name_zh") or _fc.get("away_name", "") or ""
        if not _fzh or not _fza:
            continue
        _fhm = float(_fd.get("hexagram_home_modifier", 0) or 0)
        _fam = float(_fd.get("hexagram_away_modifier", 0) or 0)

        # 1) Clean hexagram_name: strip English parens if still present
        _hn_raw = _fd.get("hexagram_name", "")
        if _hn_raw and _re_final.search(r'\([A-Za-z]', _hn_raw):
            _fd["hexagram_name"] = _hn_raw.split("(")[0].strip()
            _fd["hexagram"] = _fd["hexagram_name"]

        # 2) Rebuild fortune_summary with Chinese team names
        _fss = _fd.get("fortune_summary", "")
        if _fss and _re_final.search(r'[a-zA-Z]{2,}', _fss):
            if _fhm > _fam:
                _fd["fortune_summary"] = f"利{_fzh}"
            elif _fam > _fhm:
                _fd["fortune_summary"] = f"利{_fza}"
            else:
                _fd["fortune_summary"] = "势均力敌"

        # 3) Rebuild match_interpretation with Chinese team names
        _mi = _fd.get("match_interpretation", "")
        if _mi and _re_final.search(r'[a-zA-Z]{2,}', _mi):
            try:
                _hn = int(_fd.get("hexagram_number", 1) or 1)
                _hn_name = _fd.get("hexagram_name", "") or ""
                _hi = _fd.get("hexagram_interpretation", "") or ""
                _new = _generate_match_hexagram_interpretation(
                    _hn, _hn_name, _fhm, _fam, _fzh, _fza, hex_interp=_hi
                )
                _fd["match_interpretation"] = _new.get("narrative", _mi)
            except Exception:
                pass

        # 4) Keep Tianji score oracle available, but do not overwrite
        # an existing locked prediction score that was already generated upstream.
        _existing_score = _parse_score_text(_fc.get("score_text"))
        _result = _fc.get("predicted_result_label", "")
        _fd = _fc.get("divination_overlay") or _fc.get("divination_data") or {}
        if _result:
            _outcome = "home_win" if _result == "主胜" else "away_win" if _result == "客胜" else "draw"
            try:
                from prediction_scoring_model import _tianji_score_oracle
                _tianji = _tianji_score_oracle(
                    hex_num=_fd.get("hexagram_number", 1),
                    tianji_home_modifier=float(_fd.get("home_modifier", 0)),
                    tianji_away_modifier=float(_fd.get("away_modifier", 0)),
                    hexagram_home_modifier=float(_fd.get("hexagram_home_modifier", 0)),
                    hexagram_away_modifier=float(_fd.get("hexagram_away_modifier", 0)),
                    home_stars=_fd.get("home_stars", []),
                    away_stars=_fd.get("away_stars", []),
                    host_palace_branch=_fd.get("host_palace_branch", "子"),
                    guest_palace_branch=_fd.get("guest_palace_branch", "午"),
                    has_physical_conflict=bool(_fd.get("has_physical_conflict")),
                    home_final=float(_fc.get("home_final", 50)),
                    away_final=float(_fc.get("away_final", 50)),
                    predicted_outcome=_outcome,
                )
                _fc["tianji_score_text"] = f"{_tianji['home']}-{_tianji['away']}"
                _fc["tianji_total_goals"] = _tianji["home"] + _tianji["away"]
                _fc["tianji_score_trace"] = _tianji.get("divination_trace")
                if _existing_score["home"] is None or _existing_score["away"] is None:
                    _fc["score_text"] = _fc["tianji_score_text"]
                    _fc["total_goals"] = _fc["tianji_total_goals"]
            except Exception:
                # Fallback: keep existing score if Tianji oracle fails
                pass

    payload = _refresh_dashboard_summary(payload)
    payload = _decorate_dashboard_payload_for_frontend(payload)
    return payload


def write_visual_dashboard(*, root: Path, edition: str, now: str | None = None, include_local: bool = True) -> dict:
    data_path, html_path = _dashboard_paths(root, edition)
    static_data_path = _dashboard_static_data_path(root, edition)
    payload = _assemble_dashboard_payload(root=root, edition=edition, now=now, include_local=include_local)

    data_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    static_data_path.parent.mkdir(parents=True, exist_ok=True)

    write_json(data_path, payload)
    write_json(static_data_path, payload)
    write_text(html_path, render_html(payload, root=root, html_path=html_path))
    payload["data_path"] = _display_path(data_path, root)
    payload["html_path"] = _display_path(html_path, root)
    payload["static_data_path"] = _display_path(static_data_path, root)
    write_json(data_path, payload)
    write_json(static_data_path, payload)
    return payload


def _build_live_dashboard_payload(*, root: Path, edition: str, now: str | None = None, include_local: bool = True) -> dict:
    payload = _assemble_dashboard_payload(root=root, edition=edition, now=now, include_local=include_local)
    payload.pop("data_path", None)
    payload.pop("html_path", None)
    payload.pop("static_data_path", None)
    return payload


def _write_json_response(handler: http.server.BaseHTTPRequestHandler, status_code: int, payload: dict, *, no_store: bool = True) -> None:
    response_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if no_store:
        handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_payload)))
    handler.end_headers()
    handler.wfile.write(response_payload)


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" %
                         (self.address_string(),
                          self.log_date_time_string(),
                          format % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/api/dashboard", "/api/dashboard/overview"):
            try:
                root = self.server.dashboard_root
                edition = (parse_qs(parsed.query).get("edition") or [self.server.dashboard_edition])[0]
                payload = _build_live_dashboard_payload(root=root, edition=edition, now=self.server.dashboard_now, include_local=True)
                if parsed.path == "/api/dashboard/overview":
                    payload = _dashboard_overview_payload(payload)
                _write_json_response(self, 200, payload)
            except Exception as e:
                _write_json_response(self, 500, {"status": "error", "message": str(e)})
            return

        if parsed.path == "/api/dashboard/match":
            try:
                root = self.server.dashboard_root
                params = parse_qs(parsed.query)
                edition = (params.get("edition") or [self.server.dashboard_edition])[0]
                match_id = (params.get("match_id") or [""])[0]
                if not match_id:
                    _write_json_response(self, 400, {"status": "error", "message": "match_id is required"})
                    return
                payload = _build_live_dashboard_payload(root=root, edition=edition, now=self.server.dashboard_now, include_local=True)
                card = _find_dashboard_card(payload, match_id)
                if not card:
                    _write_json_response(self, 404, {"status": "error", "message": f"match not found: {match_id}"})
                    return
                _write_json_response(self, 200, card)
            except Exception as e:
                _write_json_response(self, 500, {"status": "error", "message": str(e)})
            return

        if parsed.path in ("/", "/index.html"):
            root = self.server.dashboard_root
            edition = self.server.dashboard_edition
            _, html_path = _dashboard_paths(root, edition)
            if not html_path.exists():
                try:
                    write_visual_dashboard(root=root, edition=edition, now=self.server.dashboard_now)
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(f"Error regenerating dashboard: {e}".encode("utf-8"))
                    return

            try:
                content = html_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"Error reading dashboard file: {e}".encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self) -> None:
        if self.path == "/api/save-config":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                config_data = json.loads(body.decode("utf-8"))

                root = self.server.dashboard_root
                edition = self.server.dashboard_edition

                # Write to model-hyperparameters.json
                hyper_path = edition_data_root(root, edition) / "model-hyperparameters.json"
                write_json(hyper_path, config_data)

                response_payload = json.dumps({"status": "success"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response_payload)))
                self.end_headers()
                self.wfile.write(response_payload)
            except Exception as e:
                response_payload = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response_payload)))
                self.end_headers()
                self.wfile.write(response_payload)
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found")


def serve_dashboard(root: Path, edition: str, now: str | None, host: str, port: int) -> None:
    # 1. Ensure initial dashboard is written
    print(f"Generating initial dashboard for edition {edition}...")
    write_visual_dashboard(root=root, edition=edition, now=now)

    # 2. Start HTTPServer
    server_address = (host, port)
    httpd = http.server.HTTPServer(server_address, DashboardHTTPRequestHandler)
    httpd.dashboard_root = root
    httpd.dashboard_edition = edition
    httpd.dashboard_now = now

    url = f"http://{host}:{port}/"
    print(f"Serving dashboard at {url}")
    print("Real-time hyperparameter configuration synchronization is enabled.")
    print("Press Ctrl+C to stop.")

    # 3. Open browser
    webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        httpd.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    write = sub.add_parser("write")
    write.add_argument("--edition", required=True)
    write.add_argument("--now")
    write.add_argument("--root", default=".")
    write.add_argument(
        "--public-only",
        action="store_true",
        help="Build the static dashboard from public facts/default predictions only, ignoring user-local reports and SQLite cache.",
    )

    serve = sub.add_parser("serve")
    serve.add_argument("--edition", required=True)
    serve.add_argument("--now")
    serve.add_argument("--root", default=".")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--host", default="127.0.0.1")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "serve":
        serve_dashboard(root=root, edition=args.edition, now=args.now, host=args.host, port=args.port)
        return 0

    result = write_visual_dashboard(root=root, edition=args.edition, now=args.now, include_local=not args.public_only)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
