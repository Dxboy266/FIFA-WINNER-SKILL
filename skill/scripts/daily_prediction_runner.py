#!/usr/bin/env python3
"""Run pre-match entertainment predictions for one World Cup edition day."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    apply_prediction_items_to_matches,
    DISCLAIMER,
    canonical_matches,
    edition_data_root,
    materialize_actual_knockout_fixtures,
    public_edition_data_root,
    raw_edition_root,
    worldcup_db_path,
    iso_now,
    load_edition_data_json,
    load_json,
    load_match_ledger,
    match_on_date,
    match_started,
    now_datetime,
    prediction_markdown_path,
    prediction_report_path,
    public_edition_data_root,
    render_daily_prediction_markdown,
    save_match_ledger,
    write_json,
    write_text,
)

from prediction_scoring_model import (  # noqa: E402
    _build_scoreline_calibration,
    _build_evidence_index,
    _build_history_index,
    _build_ranking_index,
    _build_squad_index,
    _reconcile_evidence_from_disk,
    predict_match,
)


def _prediction_from_db(row: dict, match: dict) -> dict:
    home_team = match.get("home_team", {})
    away_team = match.get("away_team", {})
    predicted_result = row.get("predicted_result")
    predicted_home = row.get("predicted_score_home")
    predicted_away = row.get("predicted_score_away")
    return {
        "match_id": match.get("match_id", ""),
        "kickoff_at": match.get("kickoff_at", ""),
        "venue": match.get("venue", ""),
        "group": match.get("group", ""),
        "phase": match.get("phase", "group"),
        "home_team": {
            "team_id": home_team.get("team_id"),
            "name": home_team.get("name"),
            "ranking": 0,
            "points": 0.0,
        },
        "away_team": {
            "team_id": away_team.get("team_id"),
            "name": away_team.get("name"),
            "ranking": 0,
            "points": 0.0,
        },
        "status": row.get("prediction_status") or "locked_pre_match",
        "run_type": row.get("run_type") or "canonical",
        "locked_at": row.get("locked_at"),
        "lock_reason": row.get("lock_reason"),
        "prediction": {
            "result": predicted_result,
            "predicted_outcome": predicted_result,
            "score": {"home": predicted_home, "away": predicted_away},
            "total_goals": row.get("predicted_total_goals"),
            "goals_line_2_5": row.get("goals_line_2_5"),
            "confidence": row.get("confidence"),
            "confidence_label": row.get("confidence"),
            "evidence_quality": row.get("evidence_quality"),
        },
        "analysis_layers": [],
        "market_odds_status": {"status": "reused_canonical_prediction"},
        "disclaimer": DISCLAIMER,
    }


def _load_preserved_predictions(root: Path, edition: str, date: str, report_path: Path) -> tuple[dict[str, dict], dict[str, Path]]:
    preserved_predictions: dict[str, dict] = {}
    prediction_sources: dict[str, Path] = {}
    search_paths = [
        report_path,
        public_edition_data_root(root, edition) / "daily-predictions" / f"{date}.json",
    ]
    for path in search_paths:
        if not path.exists():
            continue
        try:
            existing_report = load_json(path, {})
        except Exception:
            continue
        for item in existing_report.get("predictions", []) or []:
            match_id = item.get("match_id", "")
            if match_id and match_id not in preserved_predictions:
                preserved_predictions[match_id] = item
                prediction_sources[match_id] = path
    return preserved_predictions, prediction_sources


def _prediction_from_published_report(
    item: dict,
    *,
    status: str,
    generated_at: str,
    report_path: Path,
) -> dict:
    prediction = copy.deepcopy(item)
    prediction["run_type"] = prediction.get("run_type") or "canonical"
    prediction["status"] = status if status in {"kickoff_locked", "result_locked"} else (prediction.get("status") or status)
    prediction["locked_at"] = prediction.get("locked_at") or generated_at
    prediction["lock_reason"] = prediction.get("lock_reason") or {
        "locked_pre_match": "published_prediction_reused",
        "kickoff_locked": "published_prediction_reused_after_kickoff",
        "result_locked": "published_prediction_reused_after_result",
    }.get(status, "published_prediction_reused")
    prediction["report_json_path"] = prediction.get("report_json_path") or str(report_path)
    return prediction


def _prediction_sources(root: Path, edition: str) -> list[Path]:
    public_reports_candidates = [
        public_edition_data_root(root, edition) / "daily-predictions",
        public_edition_data_root(root, edition) / "default-predictions" / "daily-predictions",
        edition_data_root(root, edition) / "reports" / "daily-predictions",
    ]
    seen: set[Path] = set()
    paths: list[Path] = []
    for report_dir in public_reports_candidates:
        if not report_dir.exists():
            continue
        for path in sorted(report_dir.glob("*.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return paths


def _prediction_items_by_match(root: Path, edition: str) -> dict[str, dict]:
    items: dict[str, dict] = {}
    for path in _prediction_sources(root, edition):
        report = load_json(path, {})
        for item in report.get("predictions", []) or []:
            match_id = str(item.get("match_id") or "")
            if not match_id:
                continue
            if match_id not in items:
                enriched = copy.deepcopy(item)
                enriched["prediction_source_path"] = str(path)
                items[match_id] = enriched
    return items


def run_daily_predictions(
    *,
    root: Path,
    edition: str,
    date: str,
    now: str | None = None,
    poster: bool = False,
    force_refresh: bool = False,
) -> dict:
    del force_refresh  # Canonical predictions are never refreshed once created.

    generated_at = iso_now(now)
    now_dt = now_datetime(now)
    report_path = prediction_report_path(root, edition, date)

    preserved_predictions, prediction_sources = _load_preserved_predictions(root, edition, date, report_path)

    ledger = load_match_ledger(root, edition)
    canonical_ledger_matches = canonical_matches(ledger.get("matches", []) or [])
    canonical_ledger_matches = materialize_actual_knockout_fixtures(
        canonical_ledger_matches,
        edition=edition,
    )
    prediction_items_by_match = _prediction_items_by_match(root, edition)
    ledger_matches = apply_prediction_items_to_matches(
        canonical_ledger_matches,
        prediction_items_by_match,
    )
    prediction_matches = apply_prediction_items_to_matches(
        canonical_ledger_matches,
        prediction_items_by_match,
    )
    ledger["matches"] = ledger_matches
    ledger_matches_by_id = {
        str(match.get("match_id") or ""): match
        for match in ledger_matches
        if match.get("match_id")
    }
    ed_root = edition_data_root(root, edition)

    rankings_data = load_json(raw_edition_root(root, edition) / "rankings/fifa-men-ranking.json", {"rankings": []})
    squad_data = load_edition_data_json(root, edition, "squad-depth-features.json", {"teams": [], "global_summary": {}})
    evidence_plan = load_json(ed_root / "prediction-evidence-plan.json", {"items": []})
    evidence_plan = _reconcile_evidence_from_disk(evidence_plan, ed_root)

    ranking_index = _build_ranking_index(rankings_data)
    squad_index = _build_squad_index(squad_data)
    evidence_index = _build_evidence_index(evidence_plan)
    history_index = _build_history_index(root, edition)
    global_summary = squad_data.get("global_summary")
    scoreline_calibration = _build_scoreline_calibration(ledger_matches)

    evidence_path = ed_root / "daily-evidence" / f"{date}.json"
    daily_evidence = load_json(evidence_path, {})

    db_path = worldcup_db_path(root, edition)
    from worldcup_db import (  # noqa: E402
        get_db_connection,
        get_prediction,
        init_database,
        lock_prediction,
        query_lessons,
        save_match,
        save_prediction,
        save_prediction_analysis_layers,
    )

    init_database(db_path)
    lessons_conn = get_db_connection(db_path)

    predictions: list[dict] = []
    skipped_started = 0
    skipped_completed = 0
    skipped_missing_kickoff = 0
    locked_existing = 0
    reused_db_predictions = 0
    reused_report_predictions = 0
    newly_created = 0

    for match in prediction_matches:
        if not match_on_date(match, date):
            if not match.get("kickoff_at"):
                skipped_missing_kickoff += 1
            continue

        match_id = match.get("match_id", "")
        ledger_match = ledger_matches_by_id.get(match_id)
        existing_prediction = get_prediction(lessons_conn, match_id) if match_id else None
        final_score = match.get("final_score") or {}
        has_result = final_score.get("home") is not None and final_score.get("away") is not None
        started = match_started(match, now_dt)

        # ── 最高宪法：已开赛或已有赛果 → 锁定不重算 ──
        if started or has_result:
            if existing_prediction and existing_prediction.get("prediction_status") in {"locked_pre_match", "kickoff_locked", "result_locked"}:
                if has_result and existing_prediction.get("prediction_status") != "result_locked":
                    lock_prediction(
                        lessons_conn,
                        match_id,
                        status="result_locked",
                        locked_at=generated_at,
                        reason="result_available_during_prediction_run",
                    )
                    existing_prediction["prediction_status"] = "result_locked"
                elif started and existing_prediction.get("prediction_status") == "locked_pre_match":
                    lock_prediction(
                        lessons_conn,
                        match_id,
                        status="kickoff_locked",
                        locked_at=generated_at,
                        reason="match_started_during_prediction_run",
                    )
                    existing_prediction["prediction_status"] = "kickoff_locked"
                predictions.append(preserved_predictions.get(match_id) or _prediction_from_db(existing_prediction, match))
                locked_existing += 1
                reused_db_predictions += 1
                if existing_prediction.get("prediction_status") == "result_locked":
                    skipped_completed += 1
                elif existing_prediction.get("prediction_status") == "kickoff_locked":
                    skipped_started += 1
                continue

            if match_id and match_id in preserved_predictions:
                current_status = "result_locked" if has_result else "kickoff_locked"
                predictions.append(
                    _prediction_from_published_report(
                        preserved_predictions[match_id],
                        status=current_status,
                        generated_at=generated_at,
                        report_path=prediction_sources.get(match_id, report_path),
                    )
                )
                locked_existing += 1
                reused_report_predictions += 1
                if ledger_match is not None:
                    ledger_match["prediction_report"] = str(report_path)
                    ledger_match["prediction_status"] = current_status
                if current_status == "result_locked":
                    skipped_completed += 1
                elif current_status == "kickoff_locked":
                    skipped_started += 1
                continue

            if has_result:
                skipped_completed += 1
                continue

            if started:
                if existing_prediction:
                    predictions.append(_prediction_from_db(existing_prediction, match))
                skipped_started += 1
                continue

        # ── 未开赛 + 无赛果 → 用最新模型重新预测 ──

        home_team = match.get("home_team", {})
        away_team = match.get("away_team", {})
        home_id = str(home_team.get("team_id", "")).lower()
        away_id = str(away_team.get("team_id", "")).lower()
        match_lessons: list[dict] = []
        if home_id:
            match_lessons.extend(query_lessons(lessons_conn, team_id=home_id))
        if away_id:
            match_lessons.extend(query_lessons(lessons_conn, team_id=away_id))

        prediction = predict_match(
            match=match,
            edition=edition,
            date=date,
            all_matches=ledger_matches,
            ranking_index=ranking_index,
            squad_index=squad_index,
            evidence_index=evidence_index,
            global_summary=global_summary,
            daily_evidence=daily_evidence,
            history_index=history_index,
            lessons=match_lessons if match_lessons else None,
            scoreline_calibration=scoreline_calibration,
        )
        prediction["run_type"] = "canonical"
        prediction["status"] = "locked_pre_match"
        prediction["locked_at"] = generated_at
        prediction["lock_reason"] = "pre_match_canonical_prediction"
        predictions.append(prediction)
        newly_created += 1
        if ledger_match is not None:
            ledger_match["prediction_report"] = str(report_path)
            ledger_match["prediction_status"] = "locked_pre_match"

        if match_lessons:
            for lesson in match_lessons:
                lessons_conn.execute(
                    "UPDATE prediction_lessons SET applied_count = applied_count + 1 WHERE lesson_id = ?",
                    (lesson["lesson_id"],),
                )
            lessons_conn.commit()

    lessons_conn.close()

    report = {
        "version": 1,
        "edition": edition,
        "date": date,
        "generated_at": generated_at,
        "mode": "worldcup-daily-pre-match-entertainment-predictions",
        "status": "created",
        "report_path": str(report_path),
        "markdown_path": str(prediction_markdown_path(root, edition, date)),
        "poster_requested": bool(poster),
        "summary": {
            "predictions_created": newly_created,
            "matches_skipped_started": skipped_started,
            "matches_skipped_completed": skipped_completed,
            "matches_skipped_missing_kickoff": skipped_missing_kickoff,
            "locked_existing_predictions": locked_existing,
            "preserved_predictions": len(preserved_predictions),
            "reused_db_predictions": reused_db_predictions,
            "reused_report_predictions": reused_report_predictions,
        },
        "predictions": predictions,
        "disclaimer": DISCLAIMER,
        "safety_invariants": [
            "predictions_only_for_not_started_unpredicted_matches",
            "existing_daily_reports_are_locked_not_overwritten",
            "published_predictions_are_reused_before_regeneration",
            "force_refresh_does_not_override_canonical_predictions",
            "started_or_completed_match_predictions_are_never_regenerated",
            "data_model_weight_is_0_60",
            "tianji_overlay_weight_is_0_40",
            "tianji_calculated_from_venue_local_time_when_known",
            "no_betting_amounts_or_guaranteed_win_language",
        ],
    }
    write_json(report_path, report)

    conn = get_db_connection(db_path)
    try:
        with conn:
            for prediction in predictions:
                prediction["report_json_path"] = str(report_path)
                prediction["generated_at"] = generated_at
                prediction["prediction_date"] = date
                matched_ledger = ledger_matches_by_id.get(str(prediction.get("match_id") or ""))
                if matched_ledger:
                    save_match(conn, matched_ledger)
                save_result = save_prediction(conn, prediction)
                if save_result.get("status") in {"saved_canonical", "merged_pending"}:
                    save_prediction_analysis_layers(conn, prediction)
    finally:
        conn.close()

    write_text(prediction_markdown_path(root, edition, date), render_daily_prediction_markdown(report))
    save_match_ledger(root, edition, ledger)
    if poster:
        from poster_prompt_builder import build_poster_manifest

        manifest = build_poster_manifest(
            root=root,
            edition=edition,
            date=date,
            report_path=report_path,
            now=generated_at,
        )
        report["poster_manifest"] = manifest["manifest_path"]
        write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--edition", required=True)
    run.add_argument("--date", required=True)
    run.add_argument("--now")
    run.add_argument("--poster", action="store_true")
    run.add_argument("--force-refresh", action="store_true")
    run.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_daily_predictions(
        root=Path(args.root).resolve(),
        edition=args.edition,
        date=args.date,
        now=args.now,
        poster=args.poster,
        force_refresh=args.force_refresh,
    )
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
