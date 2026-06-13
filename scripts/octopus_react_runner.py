#!/usr/bin/env python3
"""Bounded ReAct-style planner for matchday prediction runs.

This is intentionally small: it records the reasoning/action trace and
delegates domain work to existing fetch, briefing, prediction, and dashboard
modules. It is meant to be easy for Codex, Claude Code, and other runtime
agents to call.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from daily_prediction_runner import run_daily_predictions  # noqa: E402
from matchday_intelligence_briefing import write_intelligence_briefing  # noqa: E402
from prediction_visual_dashboard import write_visual_dashboard  # noqa: E402
from worldcup_core import (  # noqa: E402
    canonical_matches,
    edition_data_root,
    iso_now,
    load_match_ledger,
    match_on_date,
    now_datetime,
    parse_datetime,
    wiki_edition_root,
    write_json,
    write_text,
)
from worldcup_live_fetcher import (  # noqa: E402
    update_news_in_evidence,
    update_sporttery_odds_in_evidence,
)


def _parse_date(value: str) -> date_cls:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _date_range(start: str, end: str | None = None) -> list[str]:
    start_date = _parse_date(start)
    end_date = _parse_date(end) if end else start_date
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")
    days = []
    current = start_date
    while current <= end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _weekend_dates(start: str) -> list[str]:
    start_date = _parse_date(start)
    saturday = start_date - timedelta(days=(start_date.weekday() - 5) % 7)
    sunday = saturday + timedelta(days=1)
    return [saturday.isoformat(), sunday.isoformat()]


def _team_name(team: object) -> str:
    if isinstance(team, dict):
        return str(team.get("name") or team.get("team_id") or "")
    return str(team or "")


def _matches_for_date(ledger: dict, date_str: str) -> list[dict]:
    return [match for match in canonical_matches(ledger.get("matches", []) or []) if match_on_date(match, date_str)]


def _started_count(matches: list[dict], now_dt: datetime) -> int:
    count = 0
    for match in matches:
        kickoff = parse_datetime(str(match.get("kickoff_at", "")))
        if kickoff and kickoff <= now_dt:
            count += 1
    return count


def _trace_step(trace: list[dict], *, step: str, thought: str, action: str, result: dict) -> None:
    trace.append(
        {
            "step": step,
            "thought": thought,
            "action": action,
            "result": result,
        }
    )


def render_markdown(report: dict) -> str:
    lines = [
        f"# AI Octopus ReAct Run {report['edition']} {report['date_range']['start']} to {report['date_range']['end']}",
        "",
        "> 娱乐预测与数据研究用途，非投注建议，不得作为购彩、投注或资金决策依据。",
        "",
        "## Summary",
        "",
        f"- Dates: {', '.join(report['dates'])}",
        f"- Matches inspected: {report['summary']['matches_inspected']}",
        f"- Predictions created: {report['summary']['predictions_created']}",
        f"- Started or locked matches skipped: {report['summary']['matches_skipped_started_or_locked']}",
        f"- Sporttery matched odds: {report['summary']['sporttery_matched_count']}",
        f"- Sporttery unavailable odds: {report['summary']['sporttery_unavailable_count']}",
        "",
        "## Matches",
        "",
    ]
    for item in report.get("days", []):
        lines.append(f"### {item['date']}")
        for match in item.get("matches", []):
            lines.append(
                f"- `{match['match_id']}` {match['home']} vs {match['away']} "
                f"({match['kickoff_at']}, {match['status']})"
            )
        if not item.get("matches"):
            lines.append("- No canonical matches.")
        lines.append("")
    lines.extend(["## Trace", ""])
    for item in report.get("trace", []):
        lines.append(f"- **{item['step']}**: {item['thought']}")
        lines.append(f"  - action: `{item['action']}`")
        lines.append(f"  - result: `{json.dumps(item['result'], ensure_ascii=False)}`")
    return "\n".join(lines).rstrip() + "\n"


def run_react_plan(
    *,
    root: Path,
    edition: str,
    start_date: str,
    end_date: str | None = None,
    weekend: bool = False,
    now: str | None = None,
    poster: bool = False,
    force_refresh: bool = False,
) -> dict:
    generated_at = iso_now(now)
    now_dt = now_datetime(now)
    dates = _weekend_dates(start_date) if weekend else _date_range(start_date, end_date)
    ledger = load_match_ledger(root, edition)
    trace: list[dict] = []
    days: list[dict] = []
    summary = {
        "matches_inspected": 0,
        "predictions_created": 0,
        "matches_skipped_started_or_locked": 0,
        "sporttery_raw_count": 0,
        "sporttery_matched_count": 0,
        "sporttery_unavailable_count": 0,
        "news_items": 0,
    }

    for date_str in dates:
        matches = _matches_for_date(ledger, date_str)
        day_info = {
            "date": date_str,
            "matches": [
                {
                    "match_id": match.get("match_id", ""),
                    "kickoff_at": match.get("kickoff_at", ""),
                    "home": _team_name(match.get("home_team")),
                    "away": _team_name(match.get("away_team")),
                    "status": match.get("status", ""),
                }
                for match in matches
            ],
            "actions": {},
        }
        days.append(day_info)
        summary["matches_inspected"] += len(matches)
        summary["matches_skipped_started_or_locked"] += _started_count(matches, now_dt)
        _trace_step(
            trace,
            step=f"{date_str}:inspect_schedule",
            thought="Identify canonical matches before touching evidence or predictions.",
            action="load_match_ledger + canonical_matches",
            result={"matches": len(matches)},
        )
        if not matches:
            continue

        odds = update_sporttery_odds_in_evidence(root=root, edition=edition, date_str=date_str)
        day_info["actions"]["sporttery"] = odds
        summary["sporttery_raw_count"] += int(odds.get("sporttery_raw_count", 0))
        summary["sporttery_matched_count"] += int(odds.get("matched_count", 0))
        summary["sporttery_unavailable_count"] += int(odds.get("unavailable_count", 0))
        _trace_step(
            trace,
            step=f"{date_str}:fetch_sporttery_odds",
            thought="Market odds are useful only when sourced and matched; unmatched odds are marked unavailable.",
            action="update_sporttery_odds_in_evidence",
            result={
                "raw": odds.get("sporttery_raw_count", 0),
                "matched": odds.get("matched_count", 0),
                "unavailable": odds.get("unavailable_count", 0),
            },
        )

        news = update_news_in_evidence(root=root, edition=edition, date_str=date_str)
        day_info["actions"]["news"] = news
        summary["news_items"] += len(news.get("news", []) or news.get("late_news", []) or [])
        _trace_step(
            trace,
            step=f"{date_str}:fetch_news",
            thought="Late news can affect confidence and evidence completeness.",
            action="update_news_in_evidence",
            result={"status": news.get("status", ""), "items": len(news.get("news", []) or news.get("late_news", []) or [])},
        )

        briefing = write_intelligence_briefing(root=root, edition=edition, date=date_str, now=generated_at)
        day_info["actions"]["briefing"] = {
            "data_path": briefing.get("data_path", ""),
            "markdown_path": briefing.get("markdown_path", ""),
            "matches": len(briefing.get("matches", [])),
        }
        _trace_step(
            trace,
            step=f"{date_str}:write_briefing",
            thought="Generate an evidence gap briefing before locking predictions.",
            action="write_intelligence_briefing",
            result=day_info["actions"]["briefing"],
        )

        predictions = run_daily_predictions(
            root=root,
            edition=edition,
            date=date_str,
            now=generated_at,
            poster=poster,
            force_refresh=force_refresh,
        )
        day_info["actions"]["predictions"] = {
            "status": predictions.get("status", ""),
            "report_path": predictions.get("report_path", ""),
            "predictions_created": predictions.get("summary", {}).get("predictions_created", 0),
            "locked_existing_predictions": predictions.get("summary", {}).get("locked_existing_predictions", 0),
            "matches_skipped_started": predictions.get("summary", {}).get("matches_skipped_started", 0),
        }
        summary["predictions_created"] += int(day_info["actions"]["predictions"]["predictions_created"] or 0)
        summary["matches_skipped_started_or_locked"] += int(day_info["actions"]["predictions"]["locked_existing_predictions"] or 0)
        _trace_step(
            trace,
            step=f"{date_str}:run_predictions",
            thought="Lock only pre-match predictions; existing daily reports are not overwritten.",
            action="run_daily_predictions",
            result=day_info["actions"]["predictions"],
        )

    dashboard = write_visual_dashboard(root=root, edition=edition, now=generated_at)
    _trace_step(
        trace,
        step="write_dashboard",
        thought="Refresh the user-facing data product after evidence and predictions change.",
        action="write_visual_dashboard",
        result={
            "data_path": dashboard.get("data_path", ""),
            "html_path": dashboard.get("html_path", ""),
            "cards": len(dashboard.get("cards", [])),
        },
    )

    out_dir = edition_data_root(root, edition) / "reports" / "agent-runs"
    wiki_dir = wiki_edition_root(root, edition) / "reports" / "agent-runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{dates[0]}_to_{dates[-1]}_react-run"
    data_path = out_dir / f"{slug}.json"
    markdown_path = wiki_dir / f"{slug}.md"
    report = {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "octopus-bounded-react-runner",
        "date_range": {"start": dates[0], "end": dates[-1]},
        "dates": dates,
        "summary": summary,
        "days": days,
        "dashboard": {
            "data_path": dashboard.get("data_path", ""),
            "html_path": dashboard.get("html_path", ""),
            "cards": len(dashboard.get("cards", [])),
        },
        "trace": trace,
        "disclaimer": "娱乐预测与数据研究用途，非投注建议，不得作为购彩、投注或资金决策依据。",
    }
    report["data_path"] = str(data_path)
    report["markdown_path"] = str(markdown_path)
    write_json(data_path, report)
    write_text(markdown_path, render_markdown(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--edition", required=True)
    run.add_argument("--start-date", required=True)
    run.add_argument("--end-date")
    run.add_argument("--weekend", action="store_true", help="Expand start date to that Saturday/Sunday.")
    run.add_argument("--now")
    run.add_argument("--poster", action="store_true")
    run.add_argument("--force-refresh", action="store_true")
    run.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_react_plan(
        root=Path(args.root).resolve(),
        edition=args.edition,
        start_date=args.start_date,
        end_date=args.end_date,
        weekend=args.weekend,
        now=args.now,
        poster=args.poster,
        force_refresh=args.force_refresh,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
