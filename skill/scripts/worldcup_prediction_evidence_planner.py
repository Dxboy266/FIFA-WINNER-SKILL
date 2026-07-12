#!/usr/bin/env python3
"""Write prediction evidence requirements and current readiness for one edition."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    PREDICTION_EVIDENCE_REQUIREMENTS,
    edition_data_root,
    iso_now,
    load_json,
    raw_edition_root,
    wiki_edition_root,
    write_json,
    write_text,
)


def evidence_plan_path(root: Path, edition: str) -> Path:
    return edition_data_root(root, edition) / "prediction-evidence-plan.json"


def evidence_markdown_path(root: Path, edition: str) -> Path:
    return wiki_edition_root(root, edition) / "synthesis" / "prediction-evidence-plan.md"


def latest_manifest(root: Path, edition: str, source_id: str) -> dict | None:
    manifest_dir = raw_edition_root(root, edition) / "evidence-packets"
    if not manifest_dir.exists():
        return None
    manifests: list[dict] = []
    for path in sorted(manifest_dir.glob(f"{source_id}-*-snapshot-manifest.json")):
        try:
            manifest = load_json(path)
        except json.JSONDecodeError:
            continue
        if manifest.get("source_id") == source_id:
            manifest["manifest_path"] = str(path)
            manifests.append(manifest)
    if not manifests:
        return None
    return sorted(manifests, key=lambda item: str(item.get("generated_at", "")))[-1]


def latest_successful_snapshot_manifest(root: Path, edition: str, source_id: str) -> dict | None:
    manifest = latest_manifest(root, edition, source_id)
    if not manifest or manifest.get("status") != "snapshot_written":
        return None
    snapshot_path = Path(str(manifest.get("snapshot_path", "")))
    if not snapshot_path.exists():
        return None
    return manifest


def failed_snapshot_blockers(root: Path, edition: str, source_id: str, prefix: str) -> list[str]:
    manifest = latest_manifest(root, edition, source_id)
    if manifest and str(manifest.get("status", "")).startswith("blocked"):
        return [f"{prefix}_fetch_failed", *manifest.get("blockers", [])]
    return []


def registry_sources(root: Path, edition: str) -> dict[str, dict]:
    registry = load_json(raw_edition_root(root, edition) / "source-registry.json", {"sources": []})
    return {str(source.get("source_id", "")): source for source in registry.get("sources", [])}


def roster_status(root: Path, edition: str) -> dict:
    roster_path = raw_edition_root(root, edition) / "rosters" / "fifa-squad-lists.json"
    if not roster_path.exists():
        return {
            "status": "blocked",
            "current_counts": {"teams": 0, "players": 0, "coaches": 0},
            "blockers": ["official_roster_json_missing"],
            "artifacts": [],
        }
    roster = load_json(roster_path)
    summary = roster.get("summary", {})
    counts = {
        "teams": int(summary.get("teams", 0) or 0),
        "players": int(summary.get("players", 0) or 0),
        "coaches": int(summary.get("coaches", 0) or 0),
    }
    complete = counts["teams"] == 48 and counts["players"] >= 48 * 26 and str(summary.get("source_integrity", roster.get("source_integrity", ""))) == "complete"
    if complete:
        return {
            "status": "complete",
            "current_counts": counts,
            "blockers": [],
            "artifacts": [str(roster_path)],
        }
    return {
        "status": "partial",
        "current_counts": counts,
        "blockers": ["official_roster_json_incomplete"],
        "artifacts": [str(roster_path)],
    }


def fixture_status(root: Path, edition: str) -> dict:
    ledger_path = edition_data_root(root, edition) / "match-ledger.json"
    ledger = load_json(ledger_path, {})
    summary = ledger.get("summary", {})
    matches = ledger.get("matches", [])
    imported = str(summary.get("fixture_status", "")) in {"official_schedule_imported", "complete"}
    has_usable_kickoffs = bool(matches) and all(str(match.get("kickoff_at", "")).strip() for match in matches[:72])
    snapshot = latest_successful_snapshot_manifest(root, edition, "fifa-match-schedule")
    failed_blockers = failed_snapshot_blockers(root, edition, "fifa-match-schedule", "fixture_schedule")
    artifacts = [str(ledger_path)]
    if snapshot:
        artifacts.append(str(snapshot.get("snapshot_path", "")))
    if imported and has_usable_kickoffs:
        return {
            "status": "complete",
            "current_counts": {"matches": len(matches), "matches_with_kickoff": sum(1 for match in matches if match.get("kickoff_at"))},
            "blockers": [],
            "artifacts": artifacts,
        }
    if snapshot:
        return {
            "status": "partial",
            "current_counts": {"matches": len(matches), "matches_with_kickoff": sum(1 for match in matches if match.get("kickoff_at"))},
            "blockers": ["fixture_schedule_not_imported", *failed_blockers],
            "artifacts": artifacts,
        }
    return {
        "status": "blocked",
        "current_counts": {"matches": len(matches), "matches_with_kickoff": sum(1 for match in matches if match.get("kickoff_at"))},
        "blockers": ["fixture_schedule_snapshot_missing", "fixture_schedule_not_imported", *failed_blockers],
        "artifacts": artifacts,
    }


def ranking_status(root: Path, edition: str) -> dict:
    ranking_path = raw_edition_root(root, edition) / "rankings" / "fifa-men-ranking.json"
    if ranking_path.exists():
        ranking = load_json(ranking_path)
        teams = ranking.get("teams") or ranking.get("rankings") or []
        return {
            "status": "complete" if teams else "partial",
            "current_counts": {"ranked_teams": len(teams)},
            "blockers": [] if teams else ["ranking_json_empty"],
            "artifacts": [str(ranking_path)],
        }
    snapshot = latest_successful_snapshot_manifest(root, edition, "fifa-men-ranking")
    failed_blockers = failed_snapshot_blockers(root, edition, "fifa-men-ranking", "ranking_snapshot")
    if snapshot:
        return {
            "status": "partial",
            "current_counts": {"ranked_teams": 0},
            "blockers": ["ranking_snapshot_not_parsed", *failed_blockers],
            "artifacts": [str(snapshot.get("snapshot_path", ""))],
        }
    return {
        "status": "blocked",
        "current_counts": {"ranked_teams": 0},
        "blockers": ["ranking_snapshot_missing", *failed_blockers],
        "artifacts": [],
    }


def file_backed_status(root: Path, edition: str, rel_path: str, complete_key: str, missing_blocker: str) -> dict:
    path = edition_data_root(root, edition) / rel_path
    if not path.exists():
        return {"status": "blocked", "current_counts": {complete_key: 0}, "blockers": [missing_blocker], "artifacts": []}
    payload = load_json(path)
    items = payload.get(complete_key, [])
    count = len(items) if isinstance(items, list) else int(bool(items))
    return {
        "status": "complete" if count else "partial",
        "current_counts": {complete_key: count},
        "blockers": [] if count else [f"{complete_key}_empty"],
        "artifacts": [str(path)],
    }


def derive_status(root: Path, edition: str, evidence_id: str) -> dict:
    if evidence_id == "official_fixtures":
        return fixture_status(root, edition)
    if evidence_id == "official_rosters":
        return roster_status(root, edition)
    if evidence_id == "fifa_rankings":
        return ranking_status(root, edition)
    if evidence_id == "squad_depth_position_balance":
        features_path = edition_data_root(root, edition) / "squad-depth-features.json"
        if features_path.exists():
            features = load_json(features_path)
            teams = features.get("teams", [])
            with_pos = sum(1 for t in teams if t.get("position_counts"))
            if with_pos >= 40:
                return {
                    "status": "complete",
                    "current_counts": {"teams": with_pos},
                    "blockers": [],
                    "artifacts": [str(features_path)],
                }
            if with_pos:
                return {
                    "status": "partial",
                    "current_counts": {"teams": with_pos},
                    "blockers": ["position_depth_features_incomplete"],
                    "artifacts": [str(features_path)],
                }
        status = roster_status(root, edition)
        if status["status"] == "complete":
            return {
                "status": "partial",
                "current_counts": status["current_counts"],
                "blockers": ["position_depth_features_not_compiled"],
                "artifacts": status["artifacts"],
            }
        return status
    if evidence_id == "historical_worldcup_results":
        ed = edition_data_root(root, edition)
        history_paths = [
            ed / "history" / "team-wc-history.json",
            ed / "raw" / "history" / "team-wc-history.json",
        ]
        for history_path in history_paths:
            if not history_path.exists():
                continue
            history = load_json(history_path)
            teams = history.get("teams", [])
            with_hist = sum(1 for t in teams if int(t.get("wc_appearances", 0) or 0) > 0)
            if with_hist >= 30:
                return {
                    "status": "partial",
                    "current_counts": {
                        "teams_with_history": with_hist,
                        "teams": len(teams),
                        "editions_processed": (history.get("summary") or {}).get("editions_processed", 0),
                    },
                    "blockers": ["historical_results_compiled_from_snapshot_not_live_feed"],
                    "artifacts": [str(history_path)],
                }
            if teams:
                return {
                    "status": "partial",
                    "current_counts": {"teams_with_history": with_hist, "teams": len(teams)},
                    "blockers": ["historical_results_incomplete"],
                    "artifacts": [str(history_path)],
                }
        openfootball_snapshot = latest_successful_snapshot_manifest(root, edition, "openfootball")
        openfootball_json_snapshot = latest_successful_snapshot_manifest(root, edition, "openfootball-worldcup-json")
        failed_blockers = [
            *failed_snapshot_blockers(root, edition, "openfootball", "historical_results"),
            *failed_snapshot_blockers(root, edition, "openfootball-worldcup-json", "historical_results"),
        ]
        if openfootball_snapshot or openfootball_json_snapshot:
            return {
                "status": "partial",
                "current_counts": {"snapshots": int(bool(openfootball_snapshot)) + int(bool(openfootball_json_snapshot))},
                "blockers": ["historical_results_not_compiled", *failed_blockers],
                "artifacts": [str(item.get("snapshot_path", "")) for item in [openfootball_snapshot, openfootball_json_snapshot] if item],
            }
        return {
            "status": "blocked",
            "current_counts": {"snapshots": 0},
            "blockers": ["historical_results_snapshot_missing", *failed_blockers],
            "artifacts": [],
        }
    if evidence_id == "recent_form_results":
        return file_backed_status(root, edition, "evidence/recent-form.json", "matches", "recent_form_results_missing")
    if evidence_id == "injury_availability":
        return file_backed_status(root, edition, "evidence/injury-availability.json", "items", "daily_injury_availability_check_missing")
    if evidence_id == "venue_rest_travel":
        rest_path = edition_data_root(root, edition) / "evidence" / "rest-travel-features.json"
        if rest_path.exists():
            rest = load_json(rest_path)
            rest_items = rest.get("items") or []
            if len(rest_items) >= 20:
                return {
                    "status": "partial",
                    "current_counts": {
                        "rest_items": len(rest_items),
                        "teams": (rest.get("summary") or {}).get("teams", 0),
                    },
                    "blockers": ["travel_distance_not_modeled"],
                    "artifacts": [str(rest_path)],
                }
        status = fixture_status(root, edition)
        if status["status"] == "complete":
            return {
                "status": "partial",
                "current_counts": status["current_counts"],
                "blockers": ["rest_travel_features_not_compiled"],
                "artifacts": status["artifacts"],
            }
        return {
            "status": "blocked",
            "current_counts": status["current_counts"],
            "blockers": ["fixture_schedule_required_for_rest_travel"] + status["blockers"],
            "artifacts": status["artifacts"],
        }
    if evidence_id == "head_to_head":
        return file_backed_status(root, edition, "evidence/head-to-head.json", "items", "head_to_head_dataset_missing")
    if evidence_id == "player_identity_enrichment":
        return file_backed_status(root, edition, "evidence/player-identity-enrichment.json", "players", "wikidata_identity_enrichment_missing")
    if evidence_id == "market_odds_signal":
        daily_dir = edition_data_root(root, edition) / "daily-evidence"
        odds_files = []
        if daily_dir.exists():
            for path in sorted(daily_dir.glob("*.json")):
                try:
                    payload = load_json(path)
                except json.JSONDecodeError:
                    continue
                if payload.get("odds") or payload.get("market_signals") or payload.get("source_refs"):
                    odds_files.append(path)
        reference_report = edition_data_root(root, edition) / "external-reference-sources.json"
        artifacts = [str(path) for path in odds_files[-5:]]
        if reference_report.exists():
            artifacts.append(str(reference_report))
        if odds_files:
            return {
                "status": "partial",
                "current_counts": {"daily_evidence_files_with_market_refs": len(odds_files)},
                "blockers": ["live_market_feed_not_required_for_prediction_but_should_be_refreshed_near_kickoff"],
                "artifacts": artifacts,
            }
        return {
            "status": "blocked",
            "current_counts": {"daily_evidence_files_with_market_refs": 0},
            "blockers": ["market_odds_snapshot_missing"],
            "artifacts": artifacts,
        }
    if evidence_id == "reference_agent_source_alignment":
        path = edition_data_root(root, edition) / "external-reference-sources.json"
        if not path.exists():
            return {
                "status": "blocked",
                "current_counts": {"reference_projects": 0},
                "blockers": ["external_reference_source_sync_missing"],
                "artifacts": [],
            }
        payload = load_json(path)
        projects = payload.get("projects", [])
        usable = [item for item in projects if item.get("status") == "checked"]
        return {
            "status": "complete" if len(usable) >= 2 else "partial",
            "current_counts": {"reference_projects": len(projects), "checked_reference_projects": len(usable)},
            "blockers": [] if len(usable) >= 2 else ["not_all_reference_projects_checked"],
            "artifacts": [str(path)],
        }
    return {"status": "blocked", "current_counts": {}, "blockers": ["unknown_evidence_requirement"], "artifacts": []}


def build_prediction_evidence_plan(*, root: Path, edition: str, now: str | None = None) -> dict:
    generated_at = iso_now(now)
    sources = registry_sources(root, edition)
    items = []
    for requirement in PREDICTION_EVIDENCE_REQUIREMENTS:
        status = derive_status(root, edition, requirement["evidence_id"])
        source_refs = []
        for source_id in requirement["source_ids"]:
            source = sources.get(source_id, {})
            source_refs.append(
                {
                    "source_id": source_id,
                    "tier": source.get("tier", ""),
                    "url": source.get("url", ""),
                    "allowed_use": source.get("allowed_use", ""),
                }
            )
        items.append({**requirement, **status, "source_refs": source_refs})
    summary = {
        "requirements": len(items),
        "complete": sum(1 for item in items if item["status"] == "complete"),
        "partial": sum(1 for item in items if item["status"] == "partial"),
        "blocked": sum(1 for item in items if item["status"] == "blocked"),
        "optional": sum(1 for item in items if not item.get("required")),
    }
    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "worldcup-prediction-evidence-plan",
        "status": "written",
        "plan_path": str(evidence_plan_path(root, edition)),
        "markdown_path": str(evidence_markdown_path(root, edition)),
        "summary": summary,
        "items": items,
        "rules": [
            "T0 official sources drive fixtures, rosters, rankings and match facts.",
            "T1 open structured sources can enrich identity and historical context after license check.",
            "T2 API sources require API key, rate limit and license records before use.",
            "T3 sources are reference-only and must not be bulk-scraped without permission.",
            "Missing injury, lineup, recent-form or fixture evidence downgrades prediction confidence.",
        ],
        "safety_invariants": [
            "prediction_evidence_plan_marks_missing_sources_partial_or_blocked",
            "prediction_evidence_plan_does_not_fetch_sources",
            "source_refs_include_tier_url_and_allowed_use",
        ],
    }


def render_markdown(plan: dict) -> str:
    lines = [
        "---",
        "type: synthesis",
        f"edition: {plan['edition']}",
        "status: active",
        "---",
        "",
        f"# 世界杯 {plan['edition']} 预测证据计划",
        "",
        "这份计划列出赛前预测需要的证据、可信来源和当前缺口。它只做 readiness 判断，不直接抓取资料。",
        "",
        "## 汇总",
        "",
        f"- 证据项：{plan['summary']['requirements']}",
        f"- complete：{plan['summary']['complete']}",
        f"- partial：{plan['summary']['partial']}",
        f"- blocked：{plan['summary']['blocked']}",
        "",
        "## 证据项",
        "",
    ]
    for item in plan["items"]:
        source_text = ", ".join(f"{ref['source_id']}({ref['tier']})" for ref in item["source_refs"])
        blockers = ", ".join(item.get("blockers", [])) or "无"
        lines.extend(
            [
                f"### {item['name']}",
                "",
                f"- Evidence ID：`{item['evidence_id']}`",
                f"- 状态：{item['status']}",
                f"- 是否必需：{'是' if item.get('required') else '否'}",
                f"- 推荐来源：{source_text}",
                f"- 用途：{item['why_needed']}",
                f"- 当前阻塞：{blockers}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_prediction_evidence_plan(*, root: Path, edition: str, now: str | None = None) -> dict:
    plan = build_prediction_evidence_plan(root=root, edition=edition, now=now)
    write_json(evidence_plan_path(root, edition), plan)
    write_text(evidence_markdown_path(root, edition), render_markdown(plan))
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--edition", required=True)
    write.add_argument("--now")
    write.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = write_prediction_evidence_plan(root=Path(args.root).resolve(), edition=args.edition, now=args.now)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
