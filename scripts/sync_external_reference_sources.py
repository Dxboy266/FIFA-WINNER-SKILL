#!/usr/bin/env python3
"""Record external reference-agent source alignment for an edition.

This tool does not ingest third-party project code into the predictor. It records
the reference projects, their current HEADs, usable source leads, and limitations
so another runtime agent can audit what was considered.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import edition_data_root, iso_now, wiki_edition_root, write_json, write_text  # noqa: E402


REFERENCE_PROJECTS = [
    {
        "source_id": "zhangcraigxg-work-cup-2026",
        "repo": "https://github.com/ZhangCraigXG/work-cup-2026",
        "kind": "reference_skill",
        "usable_for": [
            "coach-view analysis workflow",
            "Chinese source lead for worldcup2026cn schedule, groups, team pages and player status checks",
            "A2A skill file layout inspiration",
        ],
        "not_usable_for": [
            "T0 match facts",
            "direct structured prediction features without separate source verification",
            "bulk scraping instructions",
        ],
        "observed_files": [
            "SKILL.md",
            "group-schedule.md",
            "group-rank.md",
            "team-data.md",
            "player-status.md",
            "32-data.md",
            "rules.md",
        ],
        "source_leads": [
            {
                "source_id": "worldcup2026cn",
                "url": "https://worldcup2026cn.com/",
                "allowed_use": "manual_cross_check_or_adapter_after_terms_review",
                "roles": ["fixtures", "groups", "teams", "player_status_reference"],
            }
        ],
    },
    {
        "source_id": "crain99-worldcut-2026",
        "repo": "https://github.com/Crain99/worldcut-2026",
        "kind": "reference_app",
        "usable_for": [
            "Sporttery fixed-bonus source lead",
            "SQLite cache pattern for prediction history, odds snapshots and simulated account state",
            "match intelligence tool-chain pattern combining official schedule, rankings, openfootball, APIs and search",
            "static prediction snapshot fields for cross-checking score/probability presentation",
        ],
        "not_usable_for": [
            "betting advice",
            "unverified final match facts",
            "copying UI or server code into this agent",
        ],
        "observed_files": ["README.md", "server.py", "index.html"],
        "source_leads": [
            {
                "source_id": "sporttery-cn-fixed-bonus",
                "url": "https://webapi.sporttery.cn/gateway/uniform/football/getMatchListV1.qry?clientCode=3001",
                "allowed_use": "api_metadata_after_terms_rate_limit_and_region_check",
                "roles": ["market_fixed_bonus_snapshot", "market_movement_reference"],
            },
            {
                "source_id": "worldcup26-api",
                "url": "https://worldcup26.ir/",
                "allowed_use": "api_metadata_after_terms_check",
                "roles": ["fixtures", "groups", "teams"],
            },
            {
                "source_id": "international-results-csv",
                "url": "https://github.com/martj42/international_results",
                "allowed_use": "open_structured_data_when_license_checked",
                "roles": ["recent_form", "head_to_head"],
            },
        ],
    },
]


def git_head(repo: str) -> dict:
    try:
        result = subprocess.run(
            ["git", "ls-remote", repo, "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"status": "blocked", "error": str(exc)}
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    commit = line.split()[0] if line else ""
    return {"status": "checked" if commit else "blocked", "head_commit": commit}


def build_reference_report(*, root: Path, edition: str, now: str | None = None) -> dict:
    generated_at = iso_now(now)
    projects = []
    for project in REFERENCE_PROJECTS:
        head = git_head(project["repo"])
        projects.append({**project, **head})
    source_leads = []
    for project in projects:
        for lead in project.get("source_leads", []):
            source_leads.append({**lead, "from_reference_project": project["source_id"]})
    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "external-reference-source-alignment",
        "status": "written",
        "projects": projects,
        "source_leads": source_leads,
        "adoption_decisions": [
            {
                "decision": "keep_json_markdown_canonical",
                "reason": "Reference SQLite patterns are useful for query/cache layers, but audit artifacts stay portable JSON/Markdown.",
            },
            {
                "decision": "register_reference_projects_as_t3",
                "reason": "They are design and source-lead references, not official match-fact authorities.",
            },
            {
                "decision": "add_market_signal_as_optional_evidence",
                "reason": "Market snapshots can explain divergence but must not become betting advice or override verified football evidence.",
            },
        ],
        "safety_invariants": [
            "reference_projects_are_not_treated_as_official_sources",
            "no_third_party_code_is_imported_by_this_report",
            "market_sources_remain_entertainment_context_not_betting_advice",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "---",
        "type: synthesis",
        f"edition: {report['edition']}",
        "status: active",
        "---",
        "",
        f"# External Reference Source Alignment {report['edition']}",
        "",
        "This report records which external agent projects were checked and which source leads were adopted.",
        "The reference projects are not official data authorities.",
        "",
        "## Projects",
        "",
    ]
    for project in report["projects"]:
        lines.extend(
            [
                f"### {project['source_id']}",
                "",
                f"- Repo: {project['repo']}",
                f"- Status: {project.get('status', 'unknown')}",
                f"- HEAD: {project.get('head_commit', '') or 'unknown'}",
                f"- Kind: {project['kind']}",
                f"- Usable for: {', '.join(project['usable_for'])}",
                f"- Not usable for: {', '.join(project['not_usable_for'])}",
                "",
            ]
        )
    lines.extend(["## Adopted Source Leads", ""])
    for lead in report["source_leads"]:
        lines.append(f"- {lead['source_id']} from {lead['from_reference_project']}: {lead['url']}")
    lines.extend(["", "## Decisions", ""])
    for decision in report["adoption_decisions"]:
        lines.append(f"- {decision['decision']}: {decision['reason']}")
    return "\n".join(lines).rstrip() + "\n"


def write_reference_report(*, root: Path, edition: str, now: str | None = None) -> dict:
    report = build_reference_report(root=root, edition=edition, now=now)
    data_path = edition_data_root(root, edition) / "external-reference-sources.json"
    wiki_path = wiki_edition_root(root, edition) / "synthesis" / "external-reference-sources.md"
    write_json(data_path, report)
    write_text(wiki_path, render_markdown(report))
    report["report_path"] = str(data_path)
    report["markdown_path"] = str(wiki_path)
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
    result = write_reference_report(root=Path(args.root).resolve(), edition=args.edition, now=args.now)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
