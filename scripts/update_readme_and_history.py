#!/usr/bin/env python3
"""Automated README and HISTORY updater for the World Cup predictions.

Isolates tomorrow's matches in README.md and moves past records to HISTORY.md.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    edition_data_root,
    load_json,
    load_match_ledger,
    now_datetime,
    parse_datetime,
    project_root,
    wiki_edition_root,
    write_text,
)


def update_readme_and_history(*, root: Path, edition: str, date_str: str | None = None, now: str | None = None) -> dict:
    repo_root = project_root(root)
    ed_root = edition_data_root(root, edition)

    # 1. Determine "Today" and "Tomorrow" (Target date)
    now_dt = now_datetime(now).astimezone(ZoneInfo("Asia/Shanghai"))
    if date_str:
        target_date = date_str
    else:
        # Default target date to tomorrow
        target_date = (now_dt.date() + timedelta(days=1)).isoformat()

    print(f"Targeting prediction date: {target_date}")

    # 2. Load Match Ledger
    ledger = load_match_ledger(root, edition)
    all_matches = ledger.get("matches", [])

    # 3. Partition matches
    tomorrow_matches = []
    history_matches = []

    for match in all_matches:
        kickoff = parse_datetime(str(match.get("kickoff_at", "")))
        if not kickoff:
            continue
        kickoff_local = kickoff.astimezone(ZoneInfo("Asia/Shanghai"))
        kickoff_date_str = kickoff_local.date().isoformat()

        if kickoff_date_str == target_date:
            tomorrow_matches.append((match, kickoff_local))
        elif kickoff_local < now_dt or kickoff_date_str < target_date:
            history_matches.append((match, kickoff_local))

    # Sort matches chronologically
    tomorrow_matches.sort(key=lambda x: x[1])
    history_matches.sort(key=lambda x: x[1], reverse=True)

    # 4. Read prediction reports for tomorrow's matches (cached by UTC date to avoid redundant reads)
    pred_cache = {}

    # 5. Generate Tomorrow's Match Table for README.md
    readme_table_lines = [
        "## Prediction Schedule / 预测日历",
        "",
        f"展示北京时间 **{target_date}** 的比赛预测。之前的历史预测记录已移入 [[HISTORY.md]]。",
        "",
        "| 阶段 | 比赛对阵 | 预测比分与信心指数 | 状态 |",
        "|---|---|---|---|",
    ]

    if not tomorrow_matches:
        readme_table_lines.append(f"| N/A | 无比赛对阵 | 本日没有安排的世界杯比赛 | 待更新 |")
    else:
        for match, kickoff_local in tomorrow_matches:
            match_id = match["match_id"]
            home_name = match["home_team"]["name"]
            away_name = match["away_team"]["name"]
            phase = match.get("phase", "group").replace("_", " ").title()

            # Determine match UTC date to look up prediction report
            kickoff_utc = parse_datetime(str(match.get("kickoff_at", ""))).astimezone(timezone.utc)
            utc_date_str = kickoff_utc.date().isoformat()

            if utc_date_str not in pred_cache:
                day_report_path = ed_root / "reports" / "daily-predictions" / f"{utc_date_str}.json"
                pred_cache[utc_date_str] = load_json(day_report_path, default={})

            day_report = pred_cache[utc_date_str]
            p = None
            for pred in day_report.get("predictions", []):
                if pred["match_id"] == match_id:
                    p = pred
                    break

            if p:
                score = p["prediction"]["score"]
                total = p["prediction"]["total_goals"]
                conf = p["prediction"].get("confidence_label", p["prediction"].get("confidence", "low"))
                summary = f"{home_name} {score['home']}-{score['away']} {away_name}，总进球 {total}，{conf}"
                status = "已生成报告"
            else:
                summary = "比赛日前刷新数据后生成预测"
                status = "待预测"

            readme_table_lines.append(f"| {phase} | `{match_id}` {home_name} vs {away_name} | {summary} | {status} |")
    readme_table_lines.append("")

    # 6. Generate HISTORY.md
    # Load dashboard if exists to show stats
    dashboard_path = ed_root / "reports" / "evaluations" / "aggregate-dashboard.json"
    dashboard = load_json(dashboard_path, default={})

    history_lines = [
        "# FIFA-WINNER-SKILL 历史预测与复盘记录",
        "",
        "本文档收录本届世界杯所有历史比赛的预测报告、实际赛果及命中复盘数据。",
        "",
    ]

    if dashboard:
        summary = dashboard.get("summary", {})
        rates = dashboard.get("rates", {})
        history_lines.extend([
            "## 预测命中率概览",
            "",
            f"- **已评估比赛数**: {summary.get('evaluated_matches', 0)} 场",
            f"- **胜平负命中率**: {rates.get('result_hit_rate', 0.0):.2%}",
            f"- **比分直落命中率**: {rates.get('score_hit_rate', 0.0):.2%}",
            f"- **总进球数大小命中率**: {rates.get('total_goals_hit_rate', 0.0):.2%}",
            "",
        ])

    history_lines.extend([
        "## 历史对阵日志",
        "",
        "| 比赛ID | 阶段 | 开球时间 (北京时间) | 比赛对阵 | 预测比分 | 实际比分 | 状态 |",
        "|---|---|---|---|---|---|---|",
    ])

    if not history_matches:
        history_lines.append("| - | - | - | 暂无历史预测对阵 | - | - | - |")
    else:
        for match, kickoff_local in history_matches:
            match_id = match["match_id"]
            home_name = match["home_team"]["name"]
            away_name = match["away_team"]["name"]
            phase = match.get("phase", "group").replace("_", " ").title()
            kickoff_str = kickoff_local.strftime("%Y-%m-%d %H:%M")

            # Load daily report for the match's kickoff date to find the predicted score
            kickoff_utc = parse_datetime(str(match.get("kickoff_at", ""))).astimezone(timezone.utc)
            m_date_str = kickoff_utc.date().isoformat()
            day_report_path = ed_root / "reports" / "daily-predictions" / f"{m_date_str}.json"
            day_report = load_json(day_report_path, default={})

            pred_score_str = "-"
            for p in day_report.get("predictions", []):
                if p["match_id"] == match_id:
                    score = p["prediction"]["score"]
                    pred_score_str = f"{score['home']}-{score['away']}"
                    break

            final_score = match.get("final_score")
            final_score_str = "-"
            if final_score:
                final_score_str = f"{final_score.get('home', 0)}-{final_score.get('away', 0)}"

            status = "已复盘" if final_score else "已预测"
            history_lines.append(
                f"| `{match_id}` | {phase} | {kickoff_str} | {home_name} vs {away_name} | {pred_score_str} | {final_score_str} | {status} |"
            )

    history_lines.append("")
    # Append self-reflection journal if exists
    journal_path = wiki_edition_root(root, edition) / "synthesis" / "self-reflection-journal.md"
    if journal_path.exists():
        history_lines.append("## Model Self-Reflection Journal / 模型自反思日志\n")
        history_lines.append(journal_path.read_text(encoding="utf-8"))
        history_lines.append("")

    write_text(repo_root / "HISTORY.md", "\n".join(history_lines))

    # 7. Update README.md
    readme_path = repo_root / "README.md"
    if readme_path.exists():
        readme_content = readme_path.read_text(encoding="utf-8")

        # Replace the section: from "## Prediction Schedule / 预测日历" up to "## Quick Start / 快速开始"
        pattern = re.compile(
            r"(## Prediction Schedule / 预测日历\n)(.*?)(## Quick Start / 快速开始)",
            re.DOTALL
        )

        replacement = "\n".join(readme_table_lines[2:])  # Skip header since it is in Group 1
        new_content, count = pattern.subn(r"\1\n" + replacement + "\n\\g<3>", readme_content)

        if count > 0:
            readme_path.write_text(new_content, encoding="utf-8")
            print("README.md successfully updated.")
        else:
            print("Warning: Could not find 'Prediction Schedule' section in README.md to replace.")

    return {
        "status": "completed",
        "target_date": target_date,
        "tomorrow_matches_count": len(tomorrow_matches),
        "history_matches_count": len(history_matches)
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--date", help="Override tomorrow's date (YYYY-MM-DD)")
    parser.add_argument("--now", help="Override today's date (ISO-8601)")
    parser.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    res = update_readme_and_history(
        root=Path(args.root).resolve(),
        edition=args.edition,
        date_str=args.date,
        now=args.now,
    )
    import json
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
