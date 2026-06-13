#!/usr/bin/env python3
"""Shared helpers for the reusable World Cup predictor project."""

from __future__ import annotations

import hashlib
import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_REL = Path("_meta/projects/世界杯预测")
SPORT_DOMAIN = "体育"
WORLD_CUP_TOPIC = "世界杯"
DISCLAIMER = "娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。"
DIVINATION_WEIGHT = 0.40
DATA_WEIGHT = 0.60
CANONICAL_MATCH_ID_PATTERN = re.compile(r"^\d{4}-(G[A-L]-\d{2}|R32-\d{2}|R16-\d{2}|QF-\d{2}|SF-\d{2}|TP-\d{2}|F-\d{2})$")
LOCAL_MATCH_OVERLAY_FIELDS = (
    "status",
    "prediction_report",
    "prediction_status",
    "poster_manifest",
    "poster_outputs",
    "final_score",
    "evaluation",
)


def load_hyperparameters(root: Path, edition: str) -> None:
    """Load hyperparameters from JSON and update global weight values."""
    global DATA_WEIGHT, DIVINATION_WEIGHT
    path = edition_data_root(root, edition) / "model-hyperparameters.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "data_weight" in data:
                DATA_WEIGHT = float(data["data_weight"])
            if "divination_weight" in data:
                DIVINATION_WEIGHT = float(data["divination_weight"])
        except Exception as e:
            import sys
            print(f"Warning: Failed to load core weights from {path}: {e}", file=sys.stderr)

SOURCE_REGISTRY = [
    {
        "source_id": "fifa-match-schedule",
        "tier": "T0",
        "name": "FIFA World Cup official match schedule",
        "url": "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/match-schedule-fixtures-results-teams-stadiums",
        "allowed_use": "metadata_and_short_summary",
        "role": "fixtures",
    },
    {
        "source_id": "fifa-squad-lists-pdf",
        "tier": "T0",
        "name": "FIFA official squad lists PDF",
        "url": "https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf",
        "allowed_use": "metadata_and_short_summary",
        "role": "rosters",
    },
    {
        "source_id": "fifa-men-ranking",
        "tier": "T0",
        "name": "FIFA men's world ranking",
        "url": "https://inside.fifa.com/fifa-world-ranking/men",
        "allowed_use": "metadata_and_short_summary",
        "role": "rankings",
    },
    {
        "source_id": "national-fa-official-sites",
        "tier": "T0",
        "name": "National football association official sites",
        "url": "",
        "allowed_use": "metadata_and_short_summary",
        "role": "team_and_player_cross_check",
    },
    {
        "source_id": "wikidata-sparql",
        "tier": "T1",
        "name": "Wikidata SPARQL",
        "url": "https://query.wikidata.org/",
        "allowed_use": "structured_open_metadata",
        "role": "identity_aliases_birth_club_links",
    },
    {
        "source_id": "wikipedia",
        "tier": "T1",
        "name": "Wikipedia",
        "url": "https://www.wikipedia.org/",
        "allowed_use": "summary_and_url_only",
        "role": "identity_and_history_orientation",
    },
    {
        "source_id": "openfootball",
        "tier": "T1",
        "name": "OpenFootball data",
        "url": "https://github.com/openfootball/worldcup",
        "allowed_use": "open_structured_data_when_license_checked",
        "role": "historical_fixtures",
    },
    {
        "source_id": "openfootball-worldcup-json",
        "tier": "T1",
        "name": "OpenFootball World Cup JSON datasets",
        "url": "https://api.github.com/repos/openfootball/worldcup.json/contents",
        "allowed_use": "open_structured_data_when_license_checked",
        "role": "historical_fixtures_results_cross_check",
    },
    {
        "source_id": "worldfootball-elo",
        "tier": "T1",
        "name": "World Football Elo Ratings",
        "url": "https://www.eloratings.net/",
        "allowed_use": "metadata_and_short_summary_with_terms_check",
        "role": "team_strength_recent_form_reference",
    },
    {
        "source_id": "football-data-org",
        "tier": "T2",
        "name": "football-data.org API",
        "url": "https://www.football-data.org/documentation/quickstart",
        "allowed_use": "api_metadata_with_key_limit_license_record",
        "role": "api_fixtures_results",
    },
    {
        "source_id": "api-football",
        "tier": "T2",
        "name": "API-Football",
        "url": "https://www.api-football.com/documentation-v3",
        "allowed_use": "api_metadata_with_key_limit_license_record",
        "role": "api_fixtures_stats",
    },
    {
        "source_id": "sporttery_fixed_odds",
        "tier": "T2",
        "name": "中国体育彩票竞彩网固定奖金",
        "url": "https://webapi.sporttery.cn/gateway/uniform/football/getMatchListV1.qry?clientCode=3001",
        "allowed_use": "public_fixed_bonus_snapshot_with_timestamp_and_source_url",
        "role": "market_odds_snapshot",
    },
    {
        "source_id": "thesportsdb",
        "tier": "T2",
        "name": "TheSportsDB API",
        "url": "https://www.thesportsdb.com/api.php",
        "allowed_use": "api_metadata_with_terms_check",
        "role": "team_player_metadata",
    },
    {
        "source_id": "fbref",
        "tier": "T3",
        "name": "FBref",
        "url": "https://fbref.com/",
        "allowed_use": "reference_only_no_unauthorized_bulk_scrape",
        "role": "stats_cross_check",
    },
    {
        "source_id": "statbunker",
        "tier": "T3",
        "name": "StatBunker",
        "url": "https://www.statbunker.com/",
        "allowed_use": "reference_only_no_unauthorized_bulk_scrape",
        "role": "stats_cross_check",
    },
    {
        "source_id": "transfermarkt",
        "tier": "T3",
        "name": "Transfermarkt",
        "url": "https://www.transfermarkt.com/",
        "allowed_use": "reference_only_no_unauthorized_bulk_scrape",
        "role": "market_value_and_squad_reference",
    },
    {
        "source_id": "espn-soccer",
        "tier": "T3",
        "name": "ESPN Soccer",
        "url": "https://www.espn.com/soccer/",
        "allowed_use": "reference_only_no_unauthorized_bulk_scrape",
        "role": "injury_news_lineup_reference",
    },
]

PREDICTION_EVIDENCE_REQUIREMENTS = [
    {
        "evidence_id": "official_fixtures",
        "name": "官方赛程和比赛事实",
        "why_needed": "确定 match_id、开球时间、场馆、阶段、对阵和每天可预测比赛。",
        "required": True,
        "source_ids": ["fifa-match-schedule"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_or_placeholder_fixtures_block_daily_prediction_for_real_matches",
    },
    {
        "evidence_id": "official_rosters",
        "name": "官方球队阵容",
        "why_needed": "确认 48 队大名单、教练、球员位置、俱乐部和基础身份，支撑阵容深度判断。",
        "required": True,
        "source_ids": ["fifa-squad-lists-pdf", "national-fa-official-sites"],
        "minimum_status_for_prediction": "complete",
        "confidence_impact": "missing_rosters_cap_confidence_at_low",
    },
    {
        "evidence_id": "fifa_rankings",
        "name": "FIFA 男足排名",
        "why_needed": "提供跨队伍强弱基线，不能单独决定胜负，但影响数据模型基础分。",
        "required": True,
        "source_ids": ["fifa-men-ranking"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_rankings_cap_confidence_at_low",
    },
    {
        "evidence_id": "historical_worldcup_results",
        "name": "历届世界杯成绩和比赛结果",
        "why_needed": "补足国家队世界杯经验、淘汰赛韧性、进球/失球历史基线。",
        "required": True,
        "source_ids": ["openfootball", "openfootball-worldcup-json", "wikipedia"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_history_reduces_knockout_context",
    },
    {
        "evidence_id": "recent_form_results",
        "name": "近期国家队战绩",
        "why_needed": "反映近 6-12 个月状态、进攻/防守趋势和教练体系稳定性。",
        "required": True,
        "source_ids": ["football-data-org", "api-football", "worldfootball-elo", "national-fa-official-sites"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_recent_form_cap_confidence_at_low_or_medium",
    },
    {
        "evidence_id": "squad_depth_position_balance",
        "name": "阵容深度和位置平衡",
        "why_needed": "根据官方大名单统计门将、后卫、中场、前锋结构，识别板凳深度和位置短板。",
        "required": True,
        "source_ids": ["fifa-squad-lists-pdf"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_depth_features_keep_model_at_seed_baseline",
    },
    {
        "evidence_id": "injury_availability",
        "name": "伤停、停赛和赛前可用性",
        "why_needed": "关键球员缺阵会显著影响胜平负和总进球倾向，必须赛前每日更新。",
        "required": True,
        "source_ids": ["national-fa-official-sites", "espn-soccer", "fbref", "statbunker"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_availability_cap_confidence_at_low",
    },
    {
        "evidence_id": "venue_rest_travel",
        "name": "场馆、休息天数和旅行因素",
        "why_needed": "根据赛程计算休息间隔、跨城市旅行和主办国/近主场因素。",
        "required": True,
        "source_ids": ["fifa-match-schedule"],
        "minimum_status_for_prediction": "partial",
        "confidence_impact": "missing_rest_travel_reduces_total_goals_confidence",
    },
    {
        "evidence_id": "head_to_head",
        "name": "交锋历史",
        "why_needed": "作为低权重参考，帮助解释风格克制和历史心理因素。",
        "required": False,
        "source_ids": ["openfootball", "openfootball-worldcup-json", "wikipedia"],
        "minimum_status_for_prediction": "optional",
        "confidence_impact": "missing_h2h_does_not_block_prediction",
    },
    {
        "evidence_id": "player_identity_enrichment",
        "name": "球员身份和别名补强",
        "why_needed": "用 Wikidata/Wikipedia 对齐球员别名、出生日期、国家队/俱乐部关系，方便后续深档和海报。",
        "required": False,
        "source_ids": ["wikidata-sparql", "wikipedia"],
        "minimum_status_for_prediction": "optional",
        "confidence_impact": "missing_identity_enrichment_does_not_block_prediction_but_limits_player_storytelling",
    },
]


def project_root(root: Path) -> Path:
    if is_standalone_root(root):
        return root
    return root / PROJECT_REL


def is_standalone_root(root: Path) -> bool:
    return (root / "scripts" / "worldcup_core.py").exists() and (root / "schema").exists()


def knowledge_base_root(root: Path) -> Path:
    return project_root(root) / "knowledge-base"


def public_knowledge_root(root: Path) -> Path:
    return knowledge_base_root(root) / "public"


def public_edition_data_root(root: Path, edition: str) -> Path:
    return public_knowledge_root(root) / edition


def edition_data_root(root: Path, edition: str) -> Path:
    return knowledge_base_root(root) / edition / "data"


def edition_data_path(root: Path, edition: str, rel_path: str | Path) -> Path:
    return edition_data_root(root, edition) / Path(rel_path)


def public_edition_data_path(root: Path, edition: str, rel_path: str | Path) -> Path:
    return public_edition_data_root(root, edition) / Path(rel_path)


def load_edition_data_json(root: Path, edition: str, rel_path: str | Path, default: object) -> object:
    local_path = edition_data_path(root, edition, rel_path)
    if local_path.exists():
        return load_json(local_path, default)
    public_path = public_edition_data_path(root, edition, rel_path)
    return load_json(public_path, default)


def raw_edition_root(root: Path, edition: str) -> Path:
    return knowledge_base_root(root) / edition / "raw"


def wiki_worldcup_root(root: Path) -> Path:
    return knowledge_base_root(root)


def wiki_edition_root(root: Path, edition: str) -> Path:
    return knowledge_base_root(root) / edition / "wiki"


def now_datetime(value: str | None = None) -> datetime:
    if value:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return parsed
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def iso_now(value: str | None = None) -> str:
    return now_datetime(value).isoformat()


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path, default: object | None = None) -> object:
    if not path.exists():
        if default is None:
            raise FileNotFoundError(path)
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unknown"


def stable_int(*parts: str, modulo: int = 1000) -> int:
    seed = "|".join(parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulo


def default_groups() -> list[str]:
    return [chr(ord("A") + i) for i in range(12)]


def default_teams() -> list[dict]:
    teams: list[dict] = []
    for group in default_groups():
        for slot in range(1, 5):
            team_id = f"group-{group.lower()}-team-{slot}"
            name = f"Group {group} Team {slot}"
            teams.append(
                {
                    "team_id": team_id,
                    "name": name,
                    "group": group,
                    "slot": slot,
                    "source_integrity": "partial",
                    "status": "placeholder_until_official_roster_ingest",
                    "source_refs": ["fifa-match-schedule", "fifa-squad-lists-pdf"],
                }
            )
    return teams


def default_group_matches(edition: str) -> list[dict]:
    pairings = [(1, 2), (3, 4), (1, 3), (2, 4), (4, 1), (2, 3)]
    matches: list[dict] = []
    ordinal = 1
    for group in default_groups():
        for group_match_number, (home_slot, away_slot) in enumerate(pairings, start=1):
            matches.append(
                {
                    "match_id": f"{edition}-G{group}-{group_match_number:02d}",
                    "edition": edition,
                    "phase": "group",
                    "group": group,
                    "match_number": ordinal,
                    "kickoff_at": "",
                    "venue": "",
                    "status": "fixture_placeholder",
                    "home_team": {
                        "team_id": f"group-{group.lower()}-team-{home_slot}",
                        "name": f"Group {group} Team {home_slot}",
                        "slot": home_slot,
                    },
                    "away_team": {
                        "team_id": f"group-{group.lower()}-team-{away_slot}",
                        "name": f"Group {group} Team {away_slot}",
                        "slot": away_slot,
                    },
                    "prediction_report": "",
                    "poster_manifest": "",
                    "poster_outputs": [],
                    "final_score": None,
                    "evaluation": None,
                }
            )
            ordinal += 1
    return matches


def default_knockout_matches(edition: str) -> list[dict]:
    rounds = [
        ("R32", "round_of_32", 16),
        ("R16", "round_of_16", 8),
        ("QF", "quarter_final", 4),
        ("SF", "semi_final", 2),
        ("TP", "third_place", 1),
        ("F", "final", 1),
    ]
    matches: list[dict] = []
    ordinal = 73
    for prefix, phase, count in rounds:
        for index in range(1, count + 1):
            matches.append(
                {
                    "match_id": f"{edition}-{prefix}-{index:02d}",
                    "edition": edition,
                    "phase": phase,
                    "group": "",
                    "match_number": ordinal,
                    "kickoff_at": "",
                    "venue": "",
                    "status": "knockout_placeholder_until_teams_known",
                    "home_team": {
                        "team_id": f"{phase}-home-{index}",
                        "name": f"{phase.replace('_', ' ').title()} Home {index}",
                    },
                    "away_team": {
                        "team_id": f"{phase}-away-{index}",
                        "name": f"{phase.replace('_', ' ').title()} Away {index}",
                    },
                    "prediction_report": "",
                    "poster_manifest": "",
                    "poster_outputs": [],
                    "final_score": None,
                    "evaluation": None,
                }
            )
            ordinal += 1
    return matches


def default_match_ledger(edition: str, generated_at: str) -> dict:
    matches = default_group_matches(edition) + default_knockout_matches(edition)
    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "worldcup-match-ledger",
        "summary": {
            "match_count": len(matches),
            "group_stage_matches": 72,
            "knockout_matches": 32,
            "fixture_status": "placeholder_until_official_schedule_snapshot",
        },
        "matches": matches,
        "safety_invariants": [
            "worldcup_match_ledger_records_all_104_matches",
            "unknown_knockout_teams_use_placeholders_until_officially_known",
            "prediction_reports_append_to_matches_by_stable_match_id",
        ],
    }


def is_canonical_match(match: dict | str) -> bool:
    match_id = match.get("match_id", "") if isinstance(match, dict) else str(match or "")
    return bool(CANONICAL_MATCH_ID_PATTERN.match(match_id))


def canonical_matches(matches: list[dict]) -> list[dict]:
    return [match for match in matches if is_canonical_match(match)]


def match_overrides_path(root: Path, edition: str) -> Path:
    return edition_data_root(root, edition) / "match-overrides.json"


def _has_overlay_value(key: str, value: object) -> bool:
    if key == "status":
        return bool(value) and value not in {"fixture_official", "knockout_placeholder_until_teams_known"}
    return value not in (None, "", [], {})


def _match_overlay_fields(match: dict) -> dict:
    overlay = {"match_id": match.get("match_id", "")}
    for key in LOCAL_MATCH_OVERLAY_FIELDS:
        value = match.get(key)
        if _has_overlay_value(key, value):
            overlay[key] = copy.deepcopy(value)
    return overlay


def match_overrides_from_ledger(ledger: dict, edition: str, generated_at: str) -> dict:
    overrides = []
    for match in canonical_matches(ledger.get("matches", []) or []):
        overlay = _match_overlay_fields(match)
        if len(overlay) > 1:
            overrides.append(overlay)
    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "worldcup-local-match-overrides",
        "summary": {"overrides": len(overrides)},
        "matches": overrides,
        "safety_invariants": [
            "local_overrides_do_not_define_the_canonical_fixture_list",
            "public_match_ledger_remains_the_base_for_schedule_and_teams",
        ],
    }


def _apply_match_overlay(base: dict, overlay: dict) -> dict:
    merged = copy.deepcopy(base)
    for key in LOCAL_MATCH_OVERLAY_FIELDS:
        if key in overlay and _has_overlay_value(key, overlay.get(key)):
            merged[key] = copy.deepcopy(overlay[key])
    return merged


def _merge_public_and_local_ledgers(public_ledger: dict, local_ledger: dict, explicit_overrides: dict) -> dict:
    base_matches = canonical_matches(public_ledger.get("matches", []) or [])
    local_by_id = {
        match.get("match_id"): match
        for match in canonical_matches(local_ledger.get("matches", []) or [])
        if match.get("match_id")
    }
    override_by_id = {
        match.get("match_id"): match
        for match in explicit_overrides.get("matches", []) or []
        if match.get("match_id")
    }

    merged_matches = []
    for match in base_matches:
        match_id = match.get("match_id")
        merged = copy.deepcopy(match)
        if match_id in local_by_id:
            merged = _apply_match_overlay(merged, local_by_id[match_id])
        if match_id in override_by_id:
            merged = _apply_match_overlay(merged, override_by_id[match_id])
        merged_matches.append(merged)

    result = copy.deepcopy(public_ledger)
    result["matches"] = merged_matches
    result["mode"] = "worldcup-match-ledger-merged-public-local"
    result["summary"] = dict(result.get("summary", {}))
    result["summary"]["match_count"] = len(merged_matches)
    result["summary"]["public_base"] = True
    result["summary"]["local_overrides"] = len(override_by_id)
    result["safety_invariants"] = list(result.get("safety_invariants", [])) + [
        "public_facts_are_loaded_before_local_prediction_overrides",
        "legacy_local_match_ledgers_cannot_add_noncanonical_fixture_cards",
    ]
    return result


def source_registry_payload(edition: str, generated_at: str) -> dict:
    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "worldcup-source-registry",
        "sources": SOURCE_REGISTRY,
        "rules": [
            "Prefer T0 official sources for schedule, squads, rankings and match facts.",
            "Use T1 structured open sources for identity alignment and historical context.",
            "Use T2 APIs only after key, rate limit and license boundaries are recorded.",
            "Use T3 reference sources for cross-checks; do not perform unauthorized bulk scraping.",
            "Store URLs, metadata and short summaries; do not store large copyrighted full text.",
        ],
    }


def ensure_base_wiki(root: Path) -> None:
    write_text(
        wiki_worldcup_root(root) / "index.md",
        """---
type: index
topic: 体育预测知识库
status: active
---

# 体育预测知识库 (Sports Prediction Knowledge Base)

当前已收录的世界杯预测及复盘：

## 届次目录

- [[2026/wiki/index|2026 FIFA World Cup (AI章鱼哥预测与复盘)]]
""",
    )


def wiki_edition_index(edition: str) -> str:
    return f"""---
type: index
topic: 世界杯{edition}
status: active
---

# 世界杯 {edition}

## 入口

- [[MOC-世界杯{edition}]] — 本届资料、预测、海报和复盘地图。

## 目录

- `summaries/` — 来源摘要和证据边界。
- `entities/teams/` — 国家队档案。
- `entities/players/` — 球员深档。
- `synthesis/` — MOC、总汇和缺口。
- `reports/daily-predictions/` — 每日赛前预测报告。
- `reports/posters/` — 预测海报和生成记录。
- `reports/evaluations/` — 赛后复盘。
"""


def wiki_edition_moc(edition: str) -> str:
    return f"""---
type: moc
topic: 世界杯{edition}
status: active
---

# MOC-世界杯{edition}

## 定位

本页是 {edition} 届世界杯的专题地图。资料库服务赛前娱乐预测、海报生成和赛后复盘，不提供投注建议。

## 运行时与知识库结构

- 项目 Runtime 根目录：`. /` (独立仓库运行)
- 知识库归拢根目录：`knowledge-base/`
- 本届核心数据：`knowledge-base/{edition}/data/` (包含 match-ledger, profiles, reports)
- 原始来源数据：`knowledge-base/{edition}/raw/` (包含原始 evidence-packets)
- 知识汇总与Wiki：`knowledge-base/{edition}/wiki/` (本 Wiki 目录)

## 核心产物

- 比赛账本：`match-ledger.json`
- 来源注册表：`source-registry.json`
- 每日预测：`reports/daily-predictions/`
- 海报：`reports/posters/`
- 赛后复盘：`reports/evaluations/`

## 边界

- 预测为娱乐内容。
- 周易层/天纪神算最多影响 15%，必须标注娱乐解释。
- 不输出投注金额、稳胆或保证命中措辞。
"""


def ensure_edition_wiki(root: Path, edition: str) -> None:
    base = wiki_edition_root(root, edition)
    for rel in [
        "summaries",
        "entities/teams",
        "entities/players",
        "concepts",
        "comparisons",
        "design-notes",
        "synthesis",
        "reports/daily-predictions",
        "reports/posters",
        "reports/evaluations",
    ]:
        (base / rel).mkdir(parents=True, exist_ok=True)
    write_text(base / "index.md", wiki_edition_index(edition))
    write_text(base / "synthesis" / f"MOC-世界杯{edition}.md", wiki_edition_moc(edition))


def load_match_ledger(root: Path, edition: str) -> dict:
    local_path = edition_data_root(root, edition) / "match-ledger.json"
    public_path = public_edition_data_root(root, edition) / "match-ledger.json"
    overrides_path = match_overrides_path(root, edition)
    public_ledger = load_json(public_path, {}) if public_path.exists() else {}
    local_ledger = load_json(local_path, {}) if local_path.exists() else {}
    explicit_overrides = load_json(overrides_path, {}) if overrides_path.exists() else {}
    if public_ledger.get("matches"):
        return _merge_public_and_local_ledgers(public_ledger, local_ledger, explicit_overrides)
    return local_ledger


def save_match_ledger(root: Path, edition: str, ledger: dict) -> None:
    generated_at = iso_now()
    overrides = match_overrides_from_ledger(ledger, edition, generated_at)
    if (public_edition_data_root(root, edition) / "match-ledger.json").exists():
        write_json(match_overrides_path(root, edition), overrides)
        local_ledger = copy.deepcopy(ledger)
        local_ledger["mode"] = "worldcup-local-match-ledger-compat-view"
        local_ledger["summary"] = dict(local_ledger.get("summary", {}))
        local_ledger["summary"]["compat_note"] = "Canonical facts live in knowledge-base/public; local match state lives in match-overrides.json."
        local_ledger["matches"] = canonical_matches(local_ledger.get("matches", []) or [])
        write_json(edition_data_root(root, edition) / "match-ledger.json", local_ledger)
        return
    write_json(edition_data_root(root, edition) / "match-ledger.json", ledger)


def prediction_report_path(root: Path, edition: str, date: str) -> Path:
    return edition_data_root(root, edition) / "reports" / "daily-predictions" / f"{date}.json"


def prediction_markdown_path(root: Path, edition: str, date: str) -> Path:
    return wiki_edition_root(root, edition) / "reports" / "daily-predictions" / f"{date}.md"


def poster_manifest_path(root: Path, edition: str, date: str) -> Path:
    return edition_data_root(root, edition) / "reports" / "posters" / f"{date}-poster-manifest.json"


def poster_result_path(root: Path, edition: str, date: str, backend: str) -> Path:
    return edition_data_root(root, edition) / "reports" / "posters" / f"{date}-{backend}-poster-result.json"


def match_on_date(match: dict, date: str) -> bool:
    kickoff = parse_datetime(str(match.get("kickoff_at", "")))
    return bool(kickoff and kickoff.date().isoformat() == date)


def match_started(match: dict, now: datetime) -> bool:
    kickoff = parse_datetime(str(match.get("kickoff_at", "")))
    if not kickoff:
        return False
    return kickoff.astimezone(timezone.utc) <= now.astimezone(timezone.utc)


def team_name(team: dict) -> str:
    return str(team.get("name") or team.get("team_id") or "Unknown Team")


def build_play_card(
    *,
    match: dict,
    home: str,
    away: str,
    result: str,
    home_goals: int,
    away_goals: int,
    total_goals: int,
    confidence: str,
    hexagram: str,
) -> dict:
    result_labels = {"home_win": f"{home} 不败倾向", "away_win": f"{away} 不败倾向", "draw": "平局拉扯倾向"}
    watch_pool = [
        "开场 15 分钟的压迫强度和失误率",
        "定位球防守、二点球保护和禁区前沿犯规",
        "边路推进能否稳定制造传中或倒三角机会",
        "下半场 60 分钟后的换人质量和体能落差",
        "领先一方收缩后反击质量，落后一方中路渗透效率",
    ]
    start = stable_int(match.get("match_id", ""), home, away, "watch", modulo=len(watch_pool))
    watch_points = [watch_pool[start], watch_pool[(start + 2) % len(watch_pool)]]
    if total_goals >= 3:
        watch_points.append("比赛节奏一旦被早球打开，总进球可能继续抬升")
    else:
        watch_points.append("如果中场缠斗时间过长，总进球会被压低")

    risk_flags = [
        "伤停、首发和临场轮换未确认时，信心必须下调",
        "赛程、排名或近期状态证据缺口会限制模型解释力",
    ]
    if confidence == "low":
        risk_flags.append("当前只适合娱乐讨论，不适合当作强判断")
    score_text = f"{home_goals}-{away_goals}"
    if result == "home_win":
        poster_caption = f"AI预测比分 {score_text}，{home}主线占优，胜负趋势指向主队。"
    elif result == "away_win":
        poster_caption = f"AI预测比分 {score_text}，{away}主线占优，胜负趋势指向客队。"
    else:
        poster_caption = f"AI预测比分 {score_text}，双方拉扯成局，平局剧本需要重点防范。"

    return {
        "share_title": f"{home} vs {away} 娱乐预测：{home_goals}-{away_goals}",
        "match_hook": f"{result_labels[result]}，总进球参考 {total_goals} 球。",
        "poster_caption": poster_caption,
        "watch_points": watch_points,
        "risk_flags": risk_flags,
        "poster_angle": f"{home} vs {away}，比分 {home_goals}-{away_goals}，{hexagram}象做娱乐氛围，不覆盖硬数据。",
        "confidence_meter": {
            "level": confidence,
            "data_weight": DATA_WEIGHT,
            "divination_weight": DIVINATION_WEIGHT,
            "label": "证据越完整，娱乐层越只能做叙事点缀",
        },
        "gameplay_tags": ["胜平负", "比分", "总进球", "看点", "赛后复盘"],
    }


def build_prediction(match: dict, edition: str, generated_at: str) -> dict:
    home = team_name(match.get("home_team", {}))
    away = team_name(match.get("away_team", {}))
    home_seed = stable_int(edition, match.get("match_id", ""), home, "home", modulo=100)
    away_seed = stable_int(edition, match.get("match_id", ""), away, "away", modulo=100)
    diff = home_seed - away_seed
    if abs(diff) <= 8:
        result = "draw"
        home_goals = 1 + stable_int(home, away, "draw-home", modulo=2)
        away_goals = home_goals
    elif diff > 0:
        result = "home_win"
        home_goals = 1 + stable_int(home, "goals", modulo=3)
        away_goals = stable_int(away, "goals", modulo=2)
        if home_goals <= away_goals:
            home_goals = away_goals + 1
    else:
        result = "away_win"
        away_goals = 1 + stable_int(away, "goals", modulo=3)
        home_goals = stable_int(home, "goals", modulo=2)
        if away_goals <= home_goals:
            away_goals = home_goals + 1

    total_goals = home_goals + away_goals
    confidence = "medium" if abs(diff) > 20 else "low"
    hexagrams = ["乾", "坤", "震", "巽", "坎", "离", "艮", "兑"]
    hexagram = hexagrams[stable_int(str(match.get("kickoff_at", "")), home, away, modulo=len(hexagrams))]
    return {
        "match_id": match["match_id"],
        "edition": edition,
        "generated_at": generated_at,
        "status": "locked_pre_match_prediction",
        "home_team": home,
        "away_team": away,
        "kickoff_at": match.get("kickoff_at", ""),
        "prediction": {
            "result": result,
            "score": {"home": home_goals, "away": away_goals},
            "total_goals": total_goals,
            "goals_line_2_5": "over" if total_goals >= 3 else "under",
            "confidence": confidence,
        },
        "weights": {"data_model": DATA_WEIGHT, "divination_overlay": DIVINATION_WEIGHT},
        "evidence_summary": [
            "T0/T1/T2/T3 sources must be checked before upgrading confidence.",
            "This deterministic baseline is a pre-match entertainment model until team/player dossiers are complete.",
            "Missing injury, lineup and recent-form evidence keeps confidence capped.",
        ],
        "divination_overlay": {
            "weight": DIVINATION_WEIGHT,
            "hexagram": hexagram,
            "interpretation": f"{hexagram}象仅作娱乐叙事，最多微调倾向，不覆盖球队和球员证据。",
        },
        "play_card": build_play_card(
            match=match,
            home=home,
            away=away,
            result=result,
            home_goals=home_goals,
            away_goals=away_goals,
            total_goals=total_goals,
            confidence=confidence,
            hexagram=hexagram,
        ),
        "disclaimer": DISCLAIMER,
        "forbidden_actions": ["投注金额建议", "稳赢判断", "稳胆措辞"],
    }


def render_daily_prediction_markdown(report: dict) -> str:
    lines = [
        "---",
        "type: report",
        f"edition: {report['edition']}",
        f"date: {report['date']}",
        "status: active",
        "---",
        "",
        f"# 🐙 {report['edition']} 世界杯 {report['date']} 赛前决策与双轨预测报告",
        "",
        f"> ⚠️ **免责声明**：{report['disclaimer']}",
        "",
        "## 📊 每日预测汇总 (Summary)",
        "",
        f"- **生成预测总场次**：{report['summary']['predictions_created']}",
        f"- **已开球跳过场次**：{report['summary']['matches_skipped_started']}",
        f"- **沿用已锁定报告**：{report['summary']['locked_existing_predictions']}",
        "",
        "## ⚽ 预测详情与决策回溯 (Detailed Predictions)",
        "",
    ]
    if not report.get("predictions"):
        lines.append("- 今日无可预测比赛。")

    for item in report.get("predictions", []):
        home_team = item.get("home_team")
        home_name = home_team.get("name") if isinstance(home_team, dict) else str(home_team or "主队")
        away_team = item.get("away_team")
        away_name = away_team.get("name") if isinstance(away_team, dict) else str(away_team or "客队")

        prediction = item.get("prediction", {})
        score = prediction.get("score", {"home": 0, "away": 0})

        # 1. Basic Info
        lines.extend([
            f"### ⚔️ {home_name} vs {away_name}",
            "",
            f"- **比赛 ID (Match ID)**: `{item.get('match_id', 'N/A')}`",
            f"- **开球时间 (Kickoff)**: `{item.get('kickoff_at', 'N/A')}`",
            f"- **比赛场馆 (Venue)**: `{item.get('venue', '未确认')}`",
            "",
        ])

        # 2. Decision Summary
        dt = item.get("dual_track", {}) or {}
        alignment = dt.get("alignment", "aligned")
        alignment_label = "【双轨共振】" if alignment == "aligned" else "【双轨背离】"
        reason = dt.get("divergence_analysis") or (item.get("play_card", {}).get("watch_points", ["无描述"])[0] if isinstance(item.get("play_card"), dict) else "无描述")

        lines.extend([
            "#### 🎯 双轨对比与决策结论",
            f"- **最终预测结果**: **{prediction.get('result', 'N/A')}** (比分: `{score.get('home', 0)}-{score.get('away', 0)}`, 总进球: `{prediction.get('total_goals', 0)}`, 大小球: `{prediction.get('goals_line_2_5', 'N/A')}`)",
            f"- **信心指数 (Confidence)**: `{prediction.get('confidence', 'N/A')}`",
            f"- **决策对撞状态**: `{alignment_label}` (状态码: `{alignment}`)",
            f"- **回溯决策依据**: {reason}",
            "",
        ])

        # 3. Data Model Breakdown
        ds = item.get("data_score", {}) or {}
        lines.extend([
            "#### ⚖️ 数据打分模型维度拆解 (Data Model Components - 85%)",
            f"- **基本面实力打分**: 主队 `{ds.get('home', 'N/A')}` 分 vs 客队 `{ds.get('away', 'N/A')}` 分",
        ])

        comps = ds.get("components", {}) or {}
        if comps:
            for comp_name, comp_data in comps.items():
                if isinstance(comp_data, dict):
                    w = comp_data.get("weight", 0.0)
                    pct = int(w * 100)
                    lines.append(
                        f"  * **{comp_name} ({pct}%)**: 主队 `{comp_data.get('home', 'N/A')}` vs 客队 `{comp_data.get('away', 'N/A')}`"
                    )
        lines.append("")

        analysis_layers = item.get("analysis_layers", []) or []
        if analysis_layers:
            lines.extend([
                "#### Multi-Layer Analysis Stack",
                "",
            ])
            for layer in analysis_layers:
                lines.append(
                    f"- **{layer.get('title') or layer.get('layer_id')}** (`{layer.get('verdict')}`): {layer.get('summary', '')}"
                )
                drivers = layer.get("key_drivers", []) or []
                counters = layer.get("counter_signals", []) or []
                missing = layer.get("missing_context", []) or []
                if drivers:
                    lines.append(f"  * Drivers: {'; '.join(drivers[:3])}")
                if counters:
                    lines.append(f"  * Counter-signals: {'; '.join(counters[:2])}")
                if missing:
                    lines.append(f"  * Missing context: {'; '.join(missing[:3])}")
            scenario = item.get("scenario_analysis", {}) or {}
            if scenario:
                lines.extend([
                    "",
                    f"- **Base case**: {scenario.get('base_case', '')}",
                    f"- **Counter case**: {scenario.get('upset_case', '')}",
                    f"- **Draw case**: {scenario.get('draw_case', '')}",
                    "",
                ])
            audit = item.get("decision_audit", {}) or {}
            if audit:
                lines.extend([
                    f"- **Risk level**: `{audit.get('risk_level', 'unknown')}`",
                    f"- **What could change the pick**: {'; '.join((audit.get('what_would_change_the_pick') or [])[:3])}",
                    "",
                ])

        # 4. Metaphysics Overlay
        div = item.get("divination_overlay", {}) or {}
        if div:
            home_stars = div.get("home_stars", []) or []
            away_stars = div.get("away_stars", []) or []
            lines.extend([
                "#### 🌌 《天纪》开球干支与紫微排盘 (Tianji Oracle Overlay - 15%)",
                f"- **排盘时间/农历**: `{div.get('lunar_date', 'N/A')}` (时辰: `{div.get('shichen', 'N/A')}`)",
                f"- **命盘分支映射**: 主队命宫分支 `{div.get('host_palace_branch', 'N/A')}` vs 客队迁移宫分支 `{div.get('guest_palace_branch', 'N/A')}`",
                f"- **主队命宫星曜**: `{', '.join(home_stars) or '无吉煞星'}` (气运修正: `{div.get('home_modifier', 0.0)}`)",
                f"- **客队迁移星曜**: `{', '.join(away_stars) or '无吉煞星'}` (气运修正: `{div.get('away_modifier', 0.0)}`)",
                f"- **玄学气运断语**: {div.get('interpretation', '相持无伤。')}",
                "",
            ])

        # 5. Market Odds & Sentiments
        mo = item.get("market_odds", {}) or {}
        ref = item.get("referee_analysis", {}) or {}
        lines.extend([
            "#### 📈 博彩市场期望与执裁影响 (Market Expectation & Referee)",
        ])

        if mo:
            odds = mo.get("odds", {}) or {}
            implied = mo.get("implied_probabilities", {}) or {}
            lines.extend([
                f"  * **博彩实时赔率**: 主胜 `{odds.get('home_win', 'N/A')}` | 平局 `{odds.get('draw', 'N/A')}` | 客胜 `{odds.get('away_win', 'N/A')}` (来源: `{odds.get('source', 'N/A')}`)",
                f"  * **隐含胜负概率**: 主队 `{implied.get('home', 'N/A')}` | 平局 `{implied.get('draw', 'N/A')}` | 客队 `{implied.get('away', 'N/A')}`",
            ])
        else:
            lines.append("  * **博彩赔率数据**: 未提供或缺失")

        if ref:
            lines.extend([
                f"  * **主裁判执裁分析**: **{ref.get('name', '未指派')}** (尺度级别: `{ref.get('strictness', 'normal')}`)",
                f"  * **执裁黄红牌预测**: 场均黄牌 `{ref.get('predicted_yellow_cards', 'N/A')}` 张，红牌 `{ref.get('predicted_red_cards', 'N/A')}` 张",
            ])
        lines.append("")

        # 6. Play Card & Watch Points
        pc = item.get("play_card", {}) or {}
        lines.extend([
            "#### 🎮 观赛看点与风险提示 (Watch Points & Risk Flags)",
            f"- **分享金句**: *\"{pc.get('share_title', '')}\"*",
            f"- **海报概念方向**: {pc.get('poster_angle', '无')}",
        ])

        rflags = pc.get("risk_flags", []) or []
        if rflags:
            lines.append(f"- **核心风险防线**: {', '.join(rflags)}")

        wpoints = pc.get("watch_points", []) or []
        if wpoints:
            lines.append("- **实战技术与战术看点**:")
            for wp in wpoints:
                lines.append(f"  * {wp}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def scope_list(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [item.strip() for item in value.split(",") if item.strip()]


def backend_command_env(backend: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", backend).upper()
    return f"WORLDCUP_{safe}_COMMAND"
