#!/usr/bin/env python3
"""Build a matchday intelligence briefing for the information-officer agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from tianji_oracle import infer_timezone_from_venue  # noqa: E402
from worldcup_core import (  # noqa: E402
    DISCLAIMER,
    canonical_matches,
    edition_data_root,
    iso_now,
    load_json,
    load_match_ledger,
    match_on_date,
    parse_datetime,
    wiki_edition_root,
    write_json,
    write_text,
)


def _paths(root: Path, edition: str, date: str) -> tuple[Path, Path]:
    data_path = edition_data_root(root, edition) / "reports" / "intelligence" / f"{date}.json"
    markdown_path = wiki_edition_root(root, edition) / "reports" / "intelligence" / f"{date}.md"
    return data_path, markdown_path


def _match_local_time(match: dict) -> dict:
    kickoff = parse_datetime(str(match.get("kickoff_at", "")))
    venue = str(match.get("venue", ""))
    tz_name = infer_timezone_from_venue(venue)
    if not kickoff:
        return {"local_kickoff_at": "", "timezone": tz_name or "", "timezone_source": "missing_kickoff"}
    if not tz_name:
        tz_name = getattr(kickoff.tzinfo, "key", "") or "Asia/Shanghai"
        return {
            "local_kickoff_at": kickoff.isoformat(),
            "timezone": tz_name,
            "timezone_source": "input_datetime",
        }
    return {
        "local_kickoff_at": kickoff.astimezone(ZoneInfo(tz_name)).isoformat(),
        "timezone": tz_name,
        "timezone_source": "venue",
    }


def _normalise_team_code(team: dict) -> str:
    return str(team.get("team_id") or team.get("code") or "").upper()


def _team_entries(entries: list[dict], team_code: str) -> list[dict]:
    if not team_code:
        return []
    return [entry for entry in entries if str(entry.get("team_code", "")).upper() == team_code]


def _daily_match_evidence(daily_evidence: dict, match_id: str) -> dict:
    for item in daily_evidence.get("matches", []) or []:
        if item.get("match_id") == match_id:
            return item
    return {}


def _profile_counts(profile_tasks: dict, home_id: str, away_id: str) -> dict:
    counts = {
        "team_tasks_partial": 0,
        "player_tasks_partial": 0,
        "teams": {},
    }
    wanted = {home_id.lower(), away_id.lower()}
    for task in profile_tasks.get("tasks", []) or []:
        team_id = str(task.get("team_id", "")).lower()
        if team_id not in wanted:
            continue
        status = str(task.get("status", "unknown"))
        bucket = counts["teams"].setdefault(team_id, {"partial": 0, "complete": 0, "blocked": 0, "unknown": 0})
        bucket[status if status in bucket else "unknown"] += 1
        if status == "partial" and task.get("task_type") == "team_profile":
            counts["team_tasks_partial"] += 1
        if status == "partial" and task.get("task_type") == "player_deep_profile":
            counts["player_tasks_partial"] += 1
    return counts


def _evidence_status(daily_evidence: dict, match_item: dict, home_code: str, away_code: str) -> tuple[str, list[str]]:
    gaps: list[str] = []
    if not daily_evidence:
        gaps.append("daily_evidence_missing")
        return "blocked", gaps
    if not match_item.get("odds"):
        gaps.append("market_odds_missing")
    if not match_item.get("referee"):
        gaps.append("referee_missing")

    lineups = daily_evidence.get("probable_lineups", []) or []
    if not _team_entries(lineups, home_code):
        gaps.append("home_probable_lineup_missing")
    if not _team_entries(lineups, away_code):
        gaps.append("away_probable_lineup_missing")

    injuries = daily_evidence.get("injuries", []) or []
    suspensions = daily_evidence.get("suspensions", []) or []
    late_news = daily_evidence.get("late_news", []) or []
    team_signal_count = sum(
        len(_team_entries(collection, code))
        for collection in [injuries, suspensions, late_news]
        for code in [home_code, away_code]
    )
    if team_signal_count == 0:
        gaps.append("team_availability_unconfirmed")

    if not gaps:
        return "complete", []
    if len(gaps) >= 4:
        return "blocked", gaps
    return "partial", gaps


def _action(command: str, why: str) -> dict:
    return {"command": command, "why": why}


def _agent_actions(*, edition: str, date: str, match_id: str, gaps: list[str], home_code: str, away_code: str) -> list[dict]:
    actions: list[dict] = []
    if "daily_evidence_missing" in gaps:
        actions.append(
            _action(
                f"python scripts/daily_evidence_input.py init --edition {edition} --date {date} --root .",
                "create the matchday evidence file before any prediction refresh",
            )
        )
        actions.append(
            _action(
                f"python scripts/worldcup_live_fetcher.py fetch-news --edition {edition} --date {date} --root .",
                "seed late-news and availability signals",
            )
        )
    if "market_odds_missing" in gaps:
        actions.append(
            _action(
                f"python scripts/daily_evidence_input.py add-odds --edition {edition} --date {date} --match-id {match_id} --home-win <odds> --draw <odds> --away-win <odds> --root .",
                "record a transparent market snapshot for dual-track comparison",
            )
        )
    if "referee_missing" in gaps:
        actions.append(
            _action(
                f"python scripts/daily_evidence_input.py add-referee --edition {edition} --date {date} --match-id {match_id} --referee-name <name> --strictness <high|medium|low> --root .",
                "add referee strictness before card and tempo analysis",
            )
        )
    if "home_probable_lineup_missing" in gaps:
        actions.append(
            _action(
                f"python scripts/daily_evidence_input.py add-lineup --edition {edition} --date {date} --team-code {home_code} --formation <shape> --players \"<comma-separated players>\" --source national_fa --root .",
                "confirm the home team's likely tactical shell",
            )
        )
    if "away_probable_lineup_missing" in gaps:
        actions.append(
            _action(
                f"python scripts/daily_evidence_input.py add-lineup --edition {edition} --date {date} --team-code {away_code} --formation <shape> --players \"<comma-separated players>\" --source national_fa --root .",
                "confirm the away team's likely tactical shell",
            )
        )
    if "team_availability_unconfirmed" in gaps:
        actions.append(
            _action(
                f"python scripts/extract_injuries_from_news.py --edition {edition} --date {date} --root .",
                "extract injury and suspension hints from collected news",
            )
        )
    return actions


def build_intelligence_briefing(*, root: Path, edition: str, date: str, now: str | None = None) -> dict:
    generated_at = iso_now(now)
    ed_root = edition_data_root(root, edition)
    data_path, markdown_path = _paths(root, edition, date)

    ledger = load_match_ledger(root, edition)
    daily_path = ed_root / "daily-evidence" / f"{date}.json"
    daily_evidence = load_json(daily_path, {}) if daily_path.exists() else {}
    source_readiness = load_json(ed_root / "source-readiness.json", {})
    evidence_plan = load_json(ed_root / "prediction-evidence-plan.json", {"items": []})
    profile_tasks = load_json(ed_root / "profile-tasks.json", {"tasks": []})

    matches = []
    all_gaps: list[str] = []
    for match in canonical_matches(ledger.get("matches", []) or []):
        if not match_on_date(match, date):
            continue
        home = match.get("home_team", {}) or {}
        away = match.get("away_team", {}) or {}
        home_code = _normalise_team_code(home)
        away_code = _normalise_team_code(away)
        match_item = _daily_match_evidence(daily_evidence, str(match.get("match_id", "")))
        status, gaps = _evidence_status(daily_evidence, match_item, home_code, away_code)
        all_gaps.extend(gaps)

        profile_summary = _profile_counts(profile_tasks, home_code, away_code)
        actions = _agent_actions(
            edition=edition,
            date=date,
            match_id=str(match.get("match_id", "")),
            gaps=gaps,
            home_code=home_code,
            away_code=away_code,
        )
        matches.append(
            {
                "match_id": match.get("match_id", ""),
                "home_team": home,
                "away_team": away,
                "kickoff_at": match.get("kickoff_at", ""),
                "local_time": _match_local_time(match),
                "venue": match.get("venue", ""),
                "group": match.get("group", ""),
                "phase": match.get("phase", ""),
                "status": status,
                "information_gaps": gaps,
                "referee": match_item.get("referee"),
                "odds": match_item.get("odds"),
                "injuries": {
                    "home": _team_entries(daily_evidence.get("injuries", []) or [], home_code),
                    "away": _team_entries(daily_evidence.get("injuries", []) or [], away_code),
                },
                "suspensions": {
                    "home": _team_entries(daily_evidence.get("suspensions", []) or [], home_code),
                    "away": _team_entries(daily_evidence.get("suspensions", []) or [], away_code),
                },
                "late_news": {
                    "home": _team_entries(daily_evidence.get("late_news", []) or [], home_code),
                    "away": _team_entries(daily_evidence.get("late_news", []) or [], away_code),
                },
                "profile_task_summary": profile_summary,
                "information_officer_summary": (
                    "情报链可用，适合进入分析侧。"
                    if status == "complete"
                    else "情报链仍有缺口，预测信心必须降档显示。"
                ),
                "agent_actions": actions,
            }
        )

    evidence_statuses = {
        str(item.get("evidence_id", "")): str(item.get("status", "unknown"))
        for item in evidence_plan.get("items", []) or []
    }
    unique_gaps = sorted(set(all_gaps))
    report = {
        "version": 1,
        "edition": edition,
        "date": date,
        "generated_at": generated_at,
        "mode": "worldcup-matchday-intelligence-briefing",
        "status": "complete" if matches and not unique_gaps else "partial" if matches else "no_matches_found",
        "data_path": str(data_path),
        "markdown_path": str(markdown_path),
        "summary": {
            "matches": len(matches),
            "complete_matches": sum(1 for item in matches if item["status"] == "complete"),
            "partial_matches": sum(1 for item in matches if item["status"] == "partial"),
            "blocked_matches": sum(1 for item in matches if item["status"] == "blocked"),
            "unique_information_gaps": unique_gaps,
            "source_readiness_status": source_readiness.get("status", "unknown"),
        },
        "evidence_statuses": evidence_statuses,
        "matches": matches,
        "agent_roles": {
            "information_officer": "collects and marks matchday evidence gaps before analysis",
            "analyst": "uses this briefing plus prediction reports to refresh the visual dashboard",
        },
        "disclaimer": DISCLAIMER,
        "safety_invariants": [
            "information_gaps_remain_visible",
            "briefing_does_not_rewrite_locked_predictions",
            "no_betting_or_stake_advice",
        ],
    }
    return report


def render_markdown(report: dict) -> str:
    lines = [
        "---",
        "type: report",
        f"edition: {report['edition']}",
        f"date: {report['date']}",
        "status: active",
        "---",
        "",
        f"# {report['edition']} 世界杯 {report['date']} 赛前情报员简报",
        "",
        f"> {report['disclaimer']}",
        "",
        "## Summary",
        "",
        f"- Matches: {report['summary']['matches']}",
        f"- Complete: {report['summary']['complete_matches']}",
        f"- Partial: {report['summary']['partial_matches']}",
        f"- Blocked: {report['summary']['blocked_matches']}",
        f"- Gaps: {', '.join(report['summary']['unique_information_gaps']) or 'none'}",
        "",
    ]
    for item in report.get("matches", []):
        home = item.get("home_team", {}).get("name") or item.get("home_team", {}).get("team_id")
        away = item.get("away_team", {}).get("name") or item.get("away_team", {}).get("team_id")
        local = item.get("local_time", {})
        lines.extend(
            [
                f"## {item.get('match_id')} | {home} vs {away}",
                "",
                f"- Local kickoff: `{local.get('local_kickoff_at', '')}` `{local.get('timezone', '')}`",
                f"- Venue: {item.get('venue') or 'unknown'}",
                f"- Status: `{item.get('status')}`",
                f"- Information gaps: {', '.join(item.get('information_gaps', [])) or 'none'}",
                f"- Summary: {item.get('information_officer_summary')}",
                "",
                "### Next Agent Actions",
                "",
            ]
        )
        actions = item.get("agent_actions", [])
        if not actions:
            lines.append("- No immediate information-officer action.")
        for action in actions:
            lines.append(f"- `{action['command']}` — {action['why']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_intelligence_briefing(*, root: Path, edition: str, date: str, now: str | None = None) -> dict:
    report = build_intelligence_briefing(root=root, edition=edition, date=date, now=now)
    write_json(Path(report["data_path"]), report)
    write_text(Path(report["markdown_path"]), render_markdown(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--edition", required=True)
    write.add_argument("--date", required=True)
    write.add_argument("--now")
    write.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = write_intelligence_briefing(
        root=Path(args.root).resolve(),
        edition=args.edition,
        date=args.date,
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
