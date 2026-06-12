#!/usr/bin/env python3
"""Audit GitHub publication readiness for the World Cup predictor package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import edition_data_root, iso_now, load_json, project_root, raw_edition_root, write_json  # noqa: E402


REQUIRED_REPO_FILES = [
    "README.md",
    "AGENT_README.md",
    "TODO.md",
    "LICENSE",
    "install_as_skill.sh",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    "examples/sample-prediction-report.json",
    "examples/sample-poster-manifest.json",
    "examples/sample-poster-result-blocked.json",
    "examples/sample-poster-result-generated.json",
    "assets/posters/2026-06-12-mexico-vs-south-africa.png",
    "assets/posters/2026-06-12-south-korea-vs-czechia.png",
    "assets/contact/wechat-qr.jpg",
    "knowledge-base/agent/AGENT_CARD.json",
    "knowledge-base/agent/TOOL_CATALOG.json",
    "knowledge-base/agent/ARCHITECTURE.md",
    "knowledge-base/agent/RUNBOOK.md",
    "knowledge-base/agent/GUARDRAILS.md",
    "knowledge-base/agent/HANDOFFS.md",
    "knowledge-base/agent/TRACE_EVENTS.md",
    "scripts/worldcup_core.py",
    "scripts/daily_prediction_runner.py",
    "scripts/prediction_report_prompt_builder.py",
    "scripts/worldcup_prediction_evidence_planner.py",
    "scripts/worldcup_source_snapshot_tool.py",
    "scripts/sync_external_reference_sources.py",
    "scripts/poster_generator.py",
    "schema/match-ledger.schema.json",
    "schema/prediction-evidence-plan.schema.json",
    "schema/daily-prediction-report.schema.json",
    "schema/github-readiness.schema.json",
    "schema/agent-card.schema.json",
    "schema/agent-tool-catalog.schema.json",
    "skills/fifa-winner-skill/SKILL.md",
    "tests/test_worldcup_predictor_system.py",
]

README_SECTIONS = ["Quick Start", "Roadmap", "Prediction Evidence", "Daily Prediction", "GitHub Readiness", "Playability", "Examples", "Safety"]
AGENT_README_SECTIONS = [
    "Capability Card",
    "Install For Runtime Agents",
    "Agent Design Alignment",
    "A2A Invocation Contract",
    "Tool Resource Prompt Discovery",
    "Handoff Contract",
    "Trace Contract",
    "Output Contract For A2A Callers",
    "Storage Policy",
    "Safety Requirements",
]
SKILL_SECTIONS = ["Source Tiers", "Prediction Evidence", "Prediction Rules", "Poster Rules", "玩法卡片"]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def check_required_files(repo_root: Path) -> tuple[list[dict], bool]:
    checks = []
    for rel in REQUIRED_REPO_FILES:
        exists = (repo_root / rel).exists()
        checks.append({"check_id": f"file:{rel}", "status": "pass" if exists else "fail", "path": rel})
    return checks, all(item["status"] == "pass" for item in checks)


def check_text_sections(repo_root: Path, rel: str, sections: list[str]) -> tuple[list[dict], bool]:
    text = read_text(repo_root / rel)
    checks = []
    for section in sections:
        present = section in text
        checks.append({"check_id": f"{rel}:{section}", "status": "pass" if present else "fail", "section": section})
    return checks, all(item["status"] == "pass" for item in checks)


def read_json_document(repo_root: Path, rel: str) -> tuple[object | None, str]:
    path = repo_root / rel
    if not path.exists():
        return None, "missing_file"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.msg}"


def check_agent_interop(repo_root: Path) -> tuple[list[dict], bool]:
    checks: list[dict] = []
    card, card_error = read_json_document(repo_root, "knowledge-base/agent/AGENT_CARD.json")
    catalog, catalog_error = read_json_document(repo_root, "knowledge-base/agent/TOOL_CATALOG.json")

    checks.append(
        {
            "check_id": "agent-card-json-valid",
            "status": "pass" if card_error == "" and isinstance(card, dict) else "fail",
            "error": card_error,
        }
    )
    checks.append(
        {
            "check_id": "tool-catalog-json-valid",
            "status": "pass" if catalog_error == "" and isinstance(catalog, dict) else "fail",
            "error": catalog_error,
        }
    )

    if isinstance(card, dict):
        required_card_keys = [
            "$schema",
            "agent_id",
            "name",
            "runtime_contract",
            "discovery",
            "interfaces",
            "skills",
            "safety",
            "capabilities",
        ]
        for key in required_card_keys:
            checks.append(
                {
                    "check_id": f"agent-card-key:{key}",
                    "status": "pass" if key in card and card.get(key) not in ("", [], {}) else "fail",
                }
            )
        discovery_blob = json.dumps(card.get("discovery", {}), ensure_ascii=False)
        checks.append(
            {
                "check_id": "agent-card-references-tool-catalog",
                "status": "pass" if "TOOL_CATALOG.json" in discovery_blob else "fail",
            }
        )

    if isinstance(catalog, dict):
        required_catalog_keys = ["tools", "resources", "prompts", "guardrails", "handoffs", "trace_events"]
        for key in required_catalog_keys:
            checks.append(
                {
                    "check_id": f"tool-catalog-key:{key}",
                    "status": "pass" if isinstance(catalog.get(key), list) and len(catalog.get(key, [])) > 0 else "fail",
                }
            )

        tools = catalog.get("tools", []) if isinstance(catalog.get("tools"), list) else []
        tool_ids = {str(tool.get("id", "")) for tool in tools if isinstance(tool, dict)}
        for tool_id in ["initialize_edition", "plan_prediction_evidence", "predict_daily", "export_standalone"]:
            checks.append(
                {
                    "check_id": f"tool-catalog-tool:{tool_id}",
                    "status": "pass" if tool_id in tool_ids else "fail",
                }
            )

        tool_shape_ok = all(
            isinstance(tool, dict)
            and str(tool.get("command_template", "")).strip()
            and str(tool.get("safety_profile", "")).strip()
            for tool in tools
        )
        checks.append({"check_id": "tool-catalog-tools-have-command-and-safety", "status": "pass" if tool_shape_ok else "fail"})

    return checks, all(item["status"] == "pass" for item in checks)


def source_registry_checks(root: Path, edition: str) -> tuple[list[dict], bool]:
    registry = load_json(raw_edition_root(root, edition) / "source-registry.json", {"sources": []})
    checks = []
    for source in registry.get("sources", []):
        source_id = str(source.get("source_id", ""))
        missing_fields = []
        for field in ["source_id", "tier", "allowed_use", "role"]:
            if not str(source.get(field, "")).strip():
                missing_fields.append(field)
        if not str(source.get("url", "")).strip() and source_id != "national-fa-official-sites":
            missing_fields.append("url")
        status = "pass" if not missing_fields else "fail"
        checks.append(
            {
                "check_id": f"source:{source_id or 'unknown'}",
                "status": status,
                "tier": source.get("tier", ""),
                "missing_fields": missing_fields,
            }
        )
    if not checks:
        checks.append({"check_id": "source-registry-present", "status": "fail", "missing_fields": ["sources"]})
    return checks, all(item["status"] == "pass" for item in checks)


def evidence_plan_checks(root: Path, edition: str) -> tuple[list[dict], bool, bool]:
    path = edition_data_root(root, edition) / "prediction-evidence-plan.json"
    if not path.exists():
        return [{"check_id": "prediction-evidence-plan", "status": "fail", "path": str(path)}], False, False
    plan = load_json(path)
    items = plan.get("items", [])
    source_refs_ok = all(
        ref.get("source_id") and ref.get("tier") and "allowed_use" in ref
        for item in items
        for ref in item.get("source_refs", [])
    )
    statuses = {str(item.get("status", "")) for item in items}
    missing_is_visible = bool(statuses & {"partial", "blocked"}) or all(status == "complete" for status in statuses)
    checks = [
        {"check_id": "prediction-evidence-plan-present", "status": "pass", "path": str(path)},
        {"check_id": "source-refs-include-tier-and-allowed-use", "status": "pass" if source_refs_ok else "fail"},
        {"check_id": "missing-evidence-visible", "status": "pass" if missing_is_visible else "fail", "statuses": sorted(statuses)},
    ]
    data_gaps_present = any(item.get("status") in {"partial", "blocked"} for item in items)
    return checks, all(item["status"] == "pass" for item in checks), data_gaps_present


def playability_checks(repo_root: Path) -> tuple[list[dict], bool]:
    core_text = read_text(repo_root / "scripts/worldcup_core.py")
    schema_text = read_text(repo_root / "schema/daily-prediction-report.schema.json")
    readme_text = read_text(repo_root / "README.md")
    checks = [
        {"check_id": "play-card-builder-present", "status": "pass" if "build_play_card" in core_text else "fail"},
        {"check_id": "daily-report-schema-play-card", "status": "pass" if "play_card" in schema_text else "fail"},
        {"check_id": "readme-playability-section", "status": "pass" if "Playability" in readme_text else "fail"},
    ]
    return checks, all(item["status"] == "pass" for item in checks)


def build_github_readiness_report(*, root: Path, edition: str, now: str | None = None) -> dict:
    generated_at = iso_now(now)
    repo_root = project_root(root)
    file_checks, files_ok = check_required_files(repo_root)
    readme_checks, readme_ok = check_text_sections(repo_root, "README.md", README_SECTIONS)
    agent_readme_checks, agent_readme_ok = check_text_sections(repo_root, "AGENT_README.md", AGENT_README_SECTIONS)
    skill_checks, skill_ok = check_text_sections(repo_root, "skills/fifa-winner-skill/SKILL.md", SKILL_SECTIONS)
    agent_interop_checks, agent_interop_ok = check_agent_interop(repo_root)
    source_checks, sources_ok = source_registry_checks(root, edition)
    evidence_checks, evidence_ok, data_gaps_present = evidence_plan_checks(root, edition)
    play_checks, play_ok = playability_checks(repo_root)

    format_ready = files_ok and readme_ok and agent_readme_ok and skill_ok
    agent_interop_ready = agent_interop_ok
    data_accuracy_guardrails_ready = sources_ok and evidence_ok
    playability_ready = play_ok
    if format_ready and agent_interop_ready and data_accuracy_guardrails_ready and playability_ready:
        status = "ready_with_known_data_gaps" if data_gaps_present else "ready"
    else:
        status = "blocked"

    sections = [
        {"section_id": "format", "status": "pass" if format_ready else "fail", "checks": file_checks + readme_checks + agent_readme_checks + skill_checks},
        {"section_id": "agent_interop", "status": "pass" if agent_interop_ready else "fail", "checks": agent_interop_checks},
        {"section_id": "data_accuracy", "status": "pass" if data_accuracy_guardrails_ready else "fail", "checks": source_checks + evidence_checks},
        {"section_id": "playability", "status": "pass" if playability_ready else "fail", "checks": play_checks},
    ]
    report_path = edition_data_root(root, edition) / "github-readiness.json"
    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "worldcup-github-readiness",
        "status": status,
        "report_path": str(report_path),
        "summary": {
            "format_ready": format_ready,
            "agent_interop_ready": agent_interop_ready,
            "data_accuracy_guardrails_ready": data_accuracy_guardrails_ready,
            "playability_ready": playability_ready,
            "known_data_gaps_present": data_gaps_present,
        },
        "sections": sections,
        "safety_invariants": [
            "github_readiness_does_not_claim_data_completeness_when_evidence_has_gaps",
            "github_readiness_requires_source_tier_allowed_use_and_blocker_visibility",
            "github_readiness_requires_playability_without_betting_language",
            "github_readiness_requires_agent_card_tool_catalog_and_guardrail_discovery",
        ],
    }


def write_github_readiness_report(*, root: Path, edition: str, now: str | None = None) -> dict:
    report = build_github_readiness_report(root=root, edition=edition, now=now)
    write_json(Path(report["report_path"]), report)
    return report


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
    result = write_github_readiness_report(root=Path(args.root).resolve(), edition=args.edition, now=args.now)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
