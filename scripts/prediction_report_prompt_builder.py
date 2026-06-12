#!/usr/bin/env python3
"""Build reusable Chinese report-writing prompts from prediction reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import DISCLAIMER, edition_data_root, iso_now, load_json, wiki_edition_root, write_json, write_text  # noqa: E402


def _team_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("team_id") or "Unknown Team")
    return str(value or "Unknown Team")


def _result_label(result: str) -> str:
    return {"home_win": "主队胜", "away_win": "客队胜", "draw": "平局"}.get(result, result)


def _prompt_path(root: Path, edition: str, date: str, match_id: str | None = None) -> Path:
    suffix = f"-{match_id}" if match_id else ""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in suffix)
    return edition_data_root(root, edition) / "reports" / "prompts" / f"{date}{safe}-report-prompts.json"


def _markdown_path(root: Path, edition: str, date: str, match_id: str | None = None) -> Path:
    suffix = f"-{match_id}" if match_id else ""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in suffix)
    return wiki_edition_root(root, edition) / "reports" / "prompts" / f"{date}{safe}-report-prompts.md"


def build_single_report_prompt(item: dict) -> dict:
    prediction = item.get("prediction", {})
    home = _team_name(item.get("home_team"))
    away = _team_name(item.get("away_team"))
    result = prediction.get("result") or prediction.get("predicted_outcome", "")
    score = prediction.get("score", {"home": "?", "away": "?"})
    total_goals = prediction.get("total_goals", "?")
    confidence = prediction.get("confidence_label") or prediction.get("confidence", "")
    divination = item.get("divination_overlay", {})
    play_card = item.get("play_card", {})
    data_score = item.get("data_score", {})
    evidence_gaps = prediction.get("evidence_gaps", [])
    analysis_layers = item.get("analysis_layers", []) or []
    layer_lines = []
    for layer in analysis_layers:
        drivers = "; ".join(layer.get("key_drivers", [])[:3])
        counters = "; ".join(layer.get("counter_signals", [])[:2])
        missing = "; ".join(layer.get("missing_context", [])[:2])
        layer_lines.append(
            f"- {layer.get('title') or layer.get('layer_id')}: verdict={layer.get('verdict')}, summary={layer.get('summary')}, drivers={drivers}, counters={counters}, missing={missing}"
        )
    scenario_analysis = item.get("scenario_analysis", {}) or {}
    decision_audit = item.get("decision_audit", {}) or {}

    prompt = f"""请基于下面的结构化预测数据，写一份中文世界杯赛前娱乐预测报告。

硬性要求：
1. 报告必须清楚给出胜平负、预测比分、总进球数、信心等级。
2. 必须说明数据模型权重 60%，天纪娱乐层权重 40%，天纪只做娱乐叙事，不能覆盖硬数据。
3. 必须写出关键证据和证据缺口，缺失资料要标注为风险，不要假装完整。
4. 必须包含免责声明：{DISCLAIMER}
5. 禁止出现投注金额、下注建议、赔率建议、稳赢、稳胆、保证命中等赌博相关措辞。

比赛：
- Match ID：{item.get('match_id', '')}
- 对阵：{home} vs {away}
- 开球时间：{item.get('kickoff_at', '')}

预测结论：
- 胜平负：{_result_label(str(result))}
- 预测比分：{score.get('home')}-{score.get('away')}
- 总进球数：{total_goals}
- 大小球倾向：{prediction.get('goals_line_2_5', '')}
- 信心：{confidence}
- 结果信心：{prediction.get('result_confidence', confidence)}
- 比分信心：{prediction.get('score_confidence', '')}
- 总进球信心：{prediction.get('total_goals_confidence', '')}

模型证据：
- 主队数据分：{data_score.get('home', '')}
- 客队数据分：{data_score.get('away', '')}
- 主队最终分：{prediction.get('home_final', '')}
- 客队最终分：{prediction.get('away_final', '')}
- 证据缺口：{', '.join(evidence_gaps) if evidence_gaps else '暂无'}
- 比分分布：{json.dumps(prediction.get('scoreline_distribution', []), ensure_ascii=False)}
- 零封概率：{json.dumps(prediction.get('clean_sheet_probability', {}), ensure_ascii=False)}
- 场馆适应：{json.dumps(prediction.get('venue_adaptation_context', item.get('venue_adaptation_context', {})), ensure_ascii=False)}

周易娱乐层：
- 卦象：{divination.get('hexagram_name') or divination.get('hexagram', '')}
- 解读：{divination.get('interpretation', '')}

玩法卡片：
- 分享标题：{play_card.get('share_title', '')}
- 看点：{'; '.join(play_card.get('watch_points', []))}
- 风险提示：{'; '.join(play_card.get('risk_flags', []))}

请输出结构：
标题
一句话结论
预测结果表
关键证据
周易娱乐解读
风险提示
免责声明
"""
    prompt += f"""

多层分析栈：
{chr(10).join(layer_lines) if layer_lines else '- 暂无多层分析'}

比赛剧本推演：
- Base case：{scenario_analysis.get('base_case', '')}
- Counter case：{scenario_analysis.get('upset_case', '')}
- Draw case：{scenario_analysis.get('draw_case', '')}

反方审稿：
- 风险等级：{decision_audit.get('risk_level', '')}
- 为什么这么选：{'; '.join(decision_audit.get('why_this_pick', []))}
- 什么会改变判断：{'; '.join(decision_audit.get('what_would_change_the_pick', []))}
"""

    return {
        "match_id": item.get("match_id", ""),
        "home_team": home,
        "away_team": away,
        "prediction": prediction,
        "prompt": prompt,
        "required_text": [home, away, DISCLAIMER, "数据模型权重 60%", "天纪娱乐层权重 40%"],
        "forbidden_text": ["投注金额", "下注", "赔率建议", "稳赢", "稳胆", "保证命中"],
    }


def render_markdown(manifest: dict) -> str:
    lines = [
        "---",
        "type: report",
        f"edition: {manifest['edition']}",
        f"date: {manifest['date']}",
        "status: active",
        "---",
        "",
        f"# {manifest['edition']} 世界杯 {manifest['date']} 预测报告 Prompt",
        "",
    ]
    for item in manifest.get("prompt_items", []):
        lines.extend(
            [
                f"## {item['home_team']} vs {item['away_team']}",
                "",
                "```text",
                item["prompt"].rstrip(),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_report_prompt_manifest(
    *,
    root: Path,
    edition: str,
    date: str,
    report_path: Path | None = None,
    match_id: str | None = None,
    now: str | None = None,
) -> dict:
    generated_at = iso_now(now)
    report_path = report_path or (edition_data_root(root, edition) / "reports" / f"{date}-prediction-report.json")
    report = load_json(report_path, {})
    prompt_items = []
    for prediction in report.get("predictions", []):
        if match_id and prediction.get("match_id") != match_id:
            continue
        prompt_items.append(build_single_report_prompt(prediction))

    manifest = {
        "version": 1,
        "edition": edition,
        "date": date,
        "generated_at": generated_at,
        "mode": "worldcup-prediction-report-prompt-manifest",
        "source_report": str(report_path),
        "manifest_path": str(_prompt_path(root, edition, date, match_id)),
        "markdown_path": str(_markdown_path(root, edition, date, match_id)),
        "match_id_filter": match_id or "",
        "summary": {"prompt_items": len(prompt_items)},
        "prompt_items": prompt_items,
        "safety_invariants": [
            "report_prompt_derived_from_structured_prediction_report",
            "report_prompt_requires_entertainment_disclaimer",
            "report_prompt_forbids_betting_language",
        ],
    }
    write_json(Path(manifest["manifest_path"]), manifest)
    write_text(Path(manifest["markdown_path"]), render_markdown(manifest))
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--edition", required=True)
    build.add_argument("--date", required=True)
    build.add_argument("--report-path")
    build.add_argument("--match-id")
    build.add_argument("--now")
    build.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_report_prompt_manifest(
        root=Path(args.root).resolve(),
        edition=args.edition,
        date=args.date,
        report_path=Path(args.report_path).resolve() if args.report_path else None,
        match_id=args.match_id,
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
