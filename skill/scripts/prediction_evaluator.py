#!/usr/bin/env python3
"""Evaluate locked World Cup predictions against recorded final scores."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import edition_data_root, iso_now, load_json, load_match_ledger, prediction_report_path, save_match_ledger, wiki_edition_root, worldcup_db_path, write_json, write_text  # noqa: E402


def result_from_score(home: int, away: int) -> str:
    if home > away:
        return "home_win"
    if away > home:
        return "away_win"
    return "draw"


def _is_knockout_phase(phase: str) -> bool:
    return str(phase or "").strip().lower() in {
        "round_of_32",
        "round_of_16",
        "quarter_final",
        "semi_final",
        "third_place",
        "final",
    }


def _score_hit(expected: dict | None, actual: dict | None) -> bool | None:
    expected = expected or {}
    actual = actual or {}
    if expected.get("home") is None or expected.get("away") is None:
        return None
    if actual.get("home") is None or actual.get("away") is None:
        return None
    return expected.get("home") == actual.get("home") and expected.get("away") == actual.get("away")


def _winner_side_from_result(result: str) -> str:
    if result == "home_win":
        return "home"
    if result == "away_win":
        return "away"
    return ""


def confidence_level(value: object) -> str:
    level = str(value or "unknown").strip().lower()
    if level in {"low", "medium", "high"}:
        return level
    return "unknown"


def build_confidence_calibration(evaluations: list[dict]) -> dict:
    buckets: dict[str, dict] = {}
    for item in evaluations:
        if item.get("status") != "evaluated":
            continue
        level = confidence_level(item.get("prediction_confidence"))
        bucket = buckets.setdefault(level, {"evaluated_matches": 0, "result_hits": 0})
        bucket["evaluated_matches"] += 1
        if item.get("result_hit"):
            bucket["result_hits"] += 1
    result: dict[str, dict] = {}
    for level in ["low", "medium", "high", "unknown"]:
        if level not in buckets:
            continue
        bucket = buckets[level]
        evaluated = bucket["evaluated_matches"]
        hits = bucket["result_hits"]
        result[level] = {
            "evaluated_matches": evaluated,
            "result_hits": hits,
            "result_hit_rate": hits / evaluated if evaluated else 0.0,
        }
    return result


def evaluate_predictions(*, root: Path, edition: str, date: str, now: str | None = None) -> dict:
    generated_at = iso_now(now)
    ed_root = edition_data_root(root, edition)
    report = load_json(prediction_report_path(root, edition, date), {"predictions": []})
    all_predictions: list[dict] = list(report.get("predictions", []))
    evaluated_match_ids: set[str] = set()

    # Collect predictions from standalone *-prediction-report.json files
    # that may not be included in the daily aggregate
    standalone_dir = ed_root / "reports"
    if standalone_dir.exists():
        for sp_file in sorted(standalone_dir.glob("*-prediction-report.json")):
            try:
                sp_data = load_json(sp_file, {})
                for p in sp_data.get("predictions", []):
                    mid = p.get("match_id", "")
                    # Only add if not already covered by the daily report
                    if mid and mid not in evaluated_match_ids:
                        # Check date match
                        p_kickoff = str(p.get("kickoff_at", ""))
                        if date in p_kickoff or date in sp_data.get("date", ""):
                            all_predictions.append(p)
            except Exception:
                pass

    ledger = load_match_ledger(root, edition)
    ledger_by_id = {match.get("match_id"): match for match in ledger.get("matches", [])}
    evaluations = []
    for prediction in all_predictions:
        evaluated_match_ids.add(prediction.get("match_id", ""))
        match = ledger_by_id.get(prediction["match_id"], {})
        final_score = match.get("final_score")
        knockout_result = match.get("knockout_result") or {}
        prediction_body = prediction.get("prediction", {})
        knockout_prediction = prediction_body.get("knockout") or {}
        prediction_confidence = confidence_level(prediction_body.get("confidence"))
        if not final_score:
            evaluations.append(
                {
                    "match_id": prediction["match_id"],
                    "status": "blocked_missing_final_score",
                    "prediction_confidence": prediction_confidence,
                    "prediction_kept_locked": True,
                }
            )
            continue
        actual_home = int(final_score.get("home", 0))
        actual_away = int(final_score.get("away", 0))
        predicted_score = prediction_body.get("score", {})
        predicted_result = prediction_body.get("result") or prediction_body.get("predicted_outcome")
        predicted_total_goals = prediction_body.get("total_goals")
        actual_result = result_from_score(actual_home, actual_away)
        phase = str(match.get("phase") or prediction.get("phase") or "")
        regular_time_actual = (knockout_result.get("regular_time") or final_score or {})
        regular_time_home = regular_time_actual.get("home", actual_home)
        regular_time_away = regular_time_actual.get("away", actual_away)
        regular_time_result = result_from_score(int(regular_time_home), int(regular_time_away))
        evaluation = {
            "match_id": prediction["match_id"],
            "status": "evaluated",
            "predicted_result": predicted_result,
            "prediction_confidence": prediction_confidence,
            "actual_result": actual_result,
            "actual_score": {"home": actual_home, "away": actual_away},
            "actual_total_goals": actual_home + actual_away,
            "result_hit": predicted_result == regular_time_result,
            "score_hit": predicted_score.get("home") == regular_time_home and predicted_score.get("away") == regular_time_away,
            "total_goals_hit": predicted_total_goals == actual_home + actual_away,
            "prediction_kept_locked": True,
        }
        if _is_knockout_phase(phase):
            regular_time_prediction = (knockout_prediction.get("regular_time") or {})
            extra_time_prediction = (knockout_prediction.get("extra_time") or {})
            penalties_prediction = (knockout_prediction.get("penalties") or {})
            advance_prediction = (knockout_prediction.get("advance") or {})

            extra_time_actual = (knockout_result.get("extra_time") or {})
            penalties_actual = (knockout_result.get("penalties") or {})
            advance_actual = (knockout_result.get("advance") or {})

            regular_time_pred_score = regular_time_prediction.get("score") or predicted_score
            regular_time_pred_result = regular_time_prediction.get("result") or predicted_result
            extra_time_played_pred = bool(extra_time_prediction.get("played"))
            extra_time_played_actual = bool(extra_time_actual.get("played"))
            penalties_played_pred = bool(penalties_prediction.get("played"))
            penalties_played_actual = bool(penalties_actual.get("played"))

            advance_actual_side = (
                advance_actual.get("winner")
                or penalties_actual.get("winner")
                or _winner_side_from_result(extra_time_actual.get("result") or "")
                or _winner_side_from_result(actual_result)
            )

            evaluation.update(
                {
                    "actual_regular_time_score": {"home": regular_time_home, "away": regular_time_away},
                    "actual_regular_time_result": regular_time_result,
                    "regular_time_result_hit": regular_time_pred_result == regular_time_result,
                    "regular_time_score_hit": _score_hit(
                        regular_time_pred_score,
                        {"home": regular_time_home, "away": regular_time_away},
                    ),
                    "actual_knockout_result": knockout_result,
                    "knockout_prediction": knockout_prediction,
                    "extra_time_hit": (
                        extra_time_played_pred == extra_time_played_actual
                        and (
                            not extra_time_played_actual
                            or _score_hit(extra_time_prediction.get("score"), extra_time_actual)
                            or extra_time_prediction.get("result") == extra_time_actual.get("result")
                        )
                    ),
                    "penalties_hit": (
                        penalties_played_pred == penalties_played_actual
                        and (
                            not penalties_played_actual
                            or _score_hit(penalties_prediction.get("score"), penalties_actual)
                            or penalties_prediction.get("winner") == penalties_actual.get("winner")
                        )
                    ),
                    "advance_hit": advance_prediction.get("winner") == advance_actual_side if advance_prediction.get("winner") else None,
                }
            )
            evaluation["result_hit"] = evaluation.get("regular_time_result_hit")
            evaluation["score_hit"] = evaluation.get("regular_time_score_hit")
        match["evaluation"] = evaluation
        evaluations.append(evaluation)

    summary = {
        "evaluations": len(evaluations),
        "evaluated_matches": sum(1 for item in evaluations if item["status"] == "evaluated"),
        "blocked_missing_final_score": sum(1 for item in evaluations if item["status"] == "blocked_missing_final_score"),
        "result_hits": sum(1 for item in evaluations if item.get("result_hit")),
        "score_hits": sum(1 for item in evaluations if item.get("score_hit")),
        "total_goals_hits": sum(1 for item in evaluations if item.get("total_goals_hit")),
        "confidence_calibration": build_confidence_calibration(evaluations),
    }
    result = {
        "version": 1,
        "edition": edition,
        "date": date,
        "generated_at": generated_at,
        "mode": "worldcup-prediction-post-match-evaluation",
        "summary": summary,
        "evaluations": evaluations,
        "safety_invariants": ["post_match_evaluation_does_not_rewrite_locked_pre_match_predictions"],
    }
    out_path = edition_data_root(root, edition) / "reports" / "evaluations" / f"{date}.json"
    md_path = wiki_edition_root(root, edition) / "reports" / "evaluations" / f"{date}.md"
    write_json(out_path, result)

    db_path = worldcup_db_path(root, edition)
    from worldcup_db import get_db_connection, init_database, save_match, save_evaluation
    init_database(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            for ev in evaluations:
                if ev.get("status") == "evaluated":
                    match_obj = ledger_by_id.get(ev["match_id"])
                    if match_obj:
                        save_match(conn, match_obj)
                    db_ev = {
                        "match_id": ev["match_id"],
                        "actual_score_home": ev["actual_score"]["home"],
                        "actual_score_away": ev["actual_score"]["away"],
                        "is_result_correct": ev["result_hit"],
                        "is_score_correct": ev["score_hit"],
                        "evaluated_at": generated_at
                    }
                    save_evaluation(conn, db_ev)
    finally:
        conn.close()

    write_text(
        md_path,
        f"""---
type: report
edition: {edition}
date: {date}
status: active
---

# {edition} 世界杯 {date} 预测复盘

- 已评估：{summary['evaluated_matches']}
- 缺赛果阻塞：{summary['blocked_missing_final_score']}
- 胜平负命中：{summary['result_hits']}
- 比分命中：{summary['score_hits']}
- 总进球数命中：{summary['total_goals_hits']}
- 信心校准：{json.dumps(summary['confidence_calibration'], ensure_ascii=False)}
""",
    )
    save_match_ledger(root, edition, ledger)
    return result


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
    result = evaluate_predictions(root=Path(args.root).resolve(), edition=args.edition, date=args.date, now=args.now)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
