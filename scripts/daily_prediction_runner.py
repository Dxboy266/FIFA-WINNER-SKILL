#!/usr/bin/env python3
"""Run pre-match entertainment predictions for one World Cup edition day."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    DISCLAIMER,
    edition_data_root,
    iso_now,
    load_json,
    load_match_ledger,
    match_on_date,
    match_started,
    now_datetime,
    prediction_markdown_path,
    prediction_report_path,
    render_daily_prediction_markdown,
    save_match_ledger,
    write_json,
    write_text,
)

from prediction_scoring_model import (
    _build_ranking_index,
    _build_squad_index,
    _build_evidence_index,
    predict_match,
)


def run_daily_predictions(
    *,
    root: Path,
    edition: str,
    date: str,
    now: str | None = None,
    poster: bool = False,
) -> dict:
    generated_at = iso_now(now)
    now_dt = now_datetime(now)
    report_path = prediction_report_path(root, edition, date)
    if report_path.exists():
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        locked = len(existing.get("predictions", []))
        existing["summary"]["predictions_created"] = 0
        existing["summary"]["locked_existing_predictions"] = locked
        existing["summary"].setdefault("matches_skipped_started", 0)
        existing["status"] = "locked_existing_report"
        return existing

    ledger = load_match_ledger(root, edition)
    ed_root = edition_data_root(root, edition)

    # Load scoring model sources
    rankings_data = load_json(ed_root / "rankings" / "fifa-men-ranking.json", {"rankings": []})
    squad_data = load_json(ed_root / "squad-depth-features.json", {"teams": [], "global_summary": {}})
    evidence_plan = load_json(ed_root / "prediction-evidence-plan.json", {"items": []})

    ranking_index = _build_ranking_index(rankings_data)
    squad_index = _build_squad_index(squad_data)
    evidence_index = _build_evidence_index(evidence_plan)
    global_summary = squad_data.get("global_summary")

    # Load daily evidence
    evidence_path = ed_root / "daily-evidence" / f"{date}.json"
    daily_evidence = load_json(evidence_path, {})

    predictions: list[dict] = []
    skipped_started = 0
    skipped_missing_kickoff = 0

    for match in ledger.get("matches", []):
        if not match_on_date(match, date):
            if not match.get("kickoff_at"):
                skipped_missing_kickoff += 1
            continue
        if match_started(match, now_dt):
            skipped_started += 1
            continue

        prediction = predict_match(
            match=match,
            edition=edition,
            date=date,
            all_matches=ledger.get("matches", []),
            ranking_index=ranking_index,
            squad_index=squad_index,
            evidence_index=evidence_index,
            global_summary=global_summary,
            daily_evidence=daily_evidence,
        )
        predictions.append(prediction)
        match["prediction_report"] = str(report_path)
        match["prediction_status"] = "locked_pre_match_prediction"

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
            "predictions_created": len(predictions),
            "matches_skipped_started": skipped_started,
            "matches_skipped_missing_kickoff": skipped_missing_kickoff,
            "locked_existing_predictions": 0,
        },
        "predictions": predictions,
        "disclaimer": DISCLAIMER,
        "safety_invariants": [
            "predictions_only_for_not_started_matches",
            "existing_daily_reports_are_locked_not_overwritten",
            "data_model_weight_is_0_60",
            "tianji_overlay_weight_is_0_40",
            "tianji_calculated_from_venue_local_time_when_known",
            "no_betting_amounts_or_guaranteed_win_language",
        ],
    }
    write_json(report_path, report)

    db_path = edition_data_root(root, edition) / f"worldcup_{edition}.db"
    from worldcup_db import (
        get_db_connection,
        init_database,
        save_match,
        save_prediction,
        save_prediction_analysis_layers,
    )
    init_database(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            for p in predictions:
                matched_ledger_list = [m for m in ledger.get("matches", []) if m["match_id"] == p["match_id"]]
                if matched_ledger_list:
                    save_match(conn, matched_ledger_list[0])
                save_prediction(conn, p)
                save_prediction_analysis_layers(conn, p)
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
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
