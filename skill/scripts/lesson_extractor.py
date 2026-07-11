#!/usr/bin/env python3
"""Extract prediction lessons from evaluated matches (long-term memory / experience loop)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import iso_now, worldcup_db_path  # noqa: E402

from worldcup_db import (  # noqa: E402
    get_db_connection,
    init_database,
    save_lesson,
)


def _keyword_to_lesson_type(primary_error: str) -> str:
    """Map primary_error keywords to a lesson_type tag."""
    error_lower = (primary_error or "").lower()
    if any(kw in error_lower for kw in ("overconfidence", "overconfident", "too high")):
        return "overconfidence"
    if any(kw in error_lower for kw in ("underestimate", "underdog", "upset")):
        return "underestimation"
    if any(kw in error_lower for kw in ("draw", "tie", "stalemate")):
        return "draw_bias"
    if any(kw in error_lower for kw in ("goal", "score", "scoreline")):
        return "score_miscalibration"
    if any(kw in error_lower for kw in ("injury", "squad", "roster", "lineup")):
        return "squad_impact_missed"
    if any(kw in error_lower for kw in ("odds", "market", "divergence")):
        return "market_signal_ignored"
    if any(kw in error_lower for kw in ("referee", "card", "penalty")):
        return "referee_factor"
    if any(kw in error_lower for kw in ("weather", "pitch", "venue", "travel")):
        return "environmental_factor"
    return "model_error"


def extract_lessons_for_match(conn, match_id: str, now: str) -> list[dict]:
    """Generate structured lessons for a single evaluated match."""
    lessons: list[dict] = []

    # Read the evaluation record
    ev_cursor = conn.execute(
        """
        SELECT e.*, m.home_team_id, m.away_team_id
        FROM evaluations e
        JOIN matches m ON e.match_id = m.match_id
        WHERE e.match_id = ?
        """,
        (match_id,),
    )
    ev_row = ev_cursor.fetchone()
    if not ev_row:
        return lessons

    ev = dict(ev_row)
    home_team_id = ev.get("home_team_id") or ""
    away_team_id = ev.get("away_team_id") or ""
    is_result_correct = ev.get("is_result_correct", 0)

    # Fetch prediction confidence for this match
    pred_cursor = conn.execute(
        "SELECT confidence FROM predictions WHERE match_id = ?",
        (match_id,),
    )
    pred_row = pred_cursor.fetchone()
    confidence_str = (dict(pred_row).get("confidence") or "medium").lower() if pred_row else "medium"

    # --- Lesson 1: Overconfidence ---
    if not is_result_correct and confidence_str == "high":
        for team_id in (home_team_id, away_team_id):
            if not team_id:
                continue
            lessons.append({
                "lesson_id": f"{match_id}_{team_id}_overconfidence",
                "match_id": match_id,
                "team_id": team_id,
                "lesson_type": "overconfidence",
                "summary": f"Overconfident prediction for match {match_id}",
                "detail": f"High-confidence prediction was incorrect for team {team_id} in match {match_id}. "
                          f"Predicted result did not match actual outcome.",
                "confidence_adjustment": -0.1,
                "applicable_until": None,
                "created_at": now,
                "applied_count": 0,
            })

    # --- Lesson 2: Underestimation of low-confidence correct picks ---
    if is_result_correct and confidence_str == "low":
        for team_id in (home_team_id, away_team_id):
            if not team_id:
                continue
            lessons.append({
                "lesson_id": f"{match_id}_{team_id}_underestimation",
                "match_id": match_id,
                "team_id": team_id,
                "lesson_type": "underestimation",
                "summary": f"Underestimated correct prediction for match {match_id}",
                "detail": f"Low-confidence prediction was actually correct for team {team_id}. "
                          f"Model may be underweighting this team's strengths.",
                "confidence_adjustment": 0.05,
                "applicable_until": None,
                "created_at": now,
                "applied_count": 0,
            })

    # --- Lesson 3: Primary error keyword-based lessons ---
    primary_error = ev.get("primary_error") or ""
    if primary_error and not is_result_correct:
        lesson_type = _keyword_to_lesson_type(primary_error)
        for team_id in (home_team_id, away_team_id):
            if not team_id:
                continue
            lessons.append({
                "lesson_id": f"{match_id}_{team_id}_{lesson_type}",
                "match_id": match_id,
                "team_id": team_id,
                "lesson_type": lesson_type,
                "summary": f"{lesson_type.replace('_', ' ').title()} issue in match {match_id}",
                "detail": f"Primary error: {primary_error}",
                "confidence_adjustment": -0.05,
                "applicable_until": None,
                "created_at": now,
                "applied_count": 0,
            })

    # --- Lessons from linked root causes ---
    cause_cursor = conn.execute(
        """
        SELECT rc.cause_id, rc.finding, rc.impact, rc.category
        FROM match_root_causes mrc
        JOIN root_causes rc ON mrc.cause_id = rc.cause_id
        WHERE mrc.match_id = ?
        """,
        (match_id,),
    )
    for cause_row in cause_cursor.fetchall():
        cause = dict(cause_row)
        cause_id = cause["cause_id"]
        for team_id in (home_team_id, away_team_id):
            if not team_id:
                continue
            lessons.append({
                "lesson_id": f"{match_id}_{team_id}_cause_{cause_id}",
                "match_id": match_id,
                "team_id": team_id,
                "lesson_type": f"root_cause_{cause.get('category', 'model')}",
                "summary": f"Root cause: {cause['finding'][:120]}",
                "detail": f"Impact: {cause['impact']}",
                "confidence_adjustment": -0.05,
                "applicable_until": None,
                "created_at": now,
                "applied_count": 0,
            })

    # --- Lessons from linked corrective actions ---
    action_cursor = conn.execute(
        """
        SELECT ca.action_id, ca.description, ca.priority, ca.status
        FROM match_actions ma
        JOIN corrective_actions ca ON ma.action_id = ca.action_id
        WHERE ma.match_id = ?
        """,
        (match_id,),
    )
    for action_row in action_cursor.fetchall():
        action = dict(action_row)
        action_id = action["action_id"]
        for team_id in (home_team_id, away_team_id):
            if not team_id:
                continue
            lessons.append({
                "lesson_id": f"{match_id}_{team_id}_action_{action_id}",
                "match_id": match_id,
                "team_id": team_id,
                "lesson_type": "corrective_action",
                "summary": f"Corrective action ({action['priority']}): {action['description'][:120]}",
                "detail": f"Action status: {action['status']}",
                "confidence_adjustment": 0.0,
                "applicable_until": None,
                "created_at": now,
                "applied_count": 0,
            })

    return lessons


def extract_lessons(
    *,
    root: Path,
    edition: str,
    match_id: str | None = None,
    extract_all: bool = False,
    now: str | None = None,
) -> dict:
    """Extract and persist prediction lessons from evaluated matches."""
    generated_at = iso_now(now)
    db_path = worldcup_db_path(root, edition)
    init_database(db_path)
    conn = get_db_connection(db_path)

    all_lessons: list[dict] = []
    processed_matches: list[str] = []

    try:
        with conn:
            if match_id:
                match_ids = [match_id]
            elif extract_all:
                cursor = conn.execute(
                    """
                    SELECT DISTINCT e.match_id
                    FROM evaluations e
                    WHERE e.is_result_correct IS NOT NULL
                    """
                )
                match_ids = [row["match_id"] for row in cursor.fetchall()]
            else:
                return {
                    "version": 1,
                    "edition": edition,
                    "generated_at": generated_at,
                    "mode": "lesson-extraction",
                    "status": "no_target",
                    "lessons_created": 0,
                    "matches_processed": 0,
                }

            for mid in match_ids:
                lessons = extract_lessons_for_match(conn, mid, generated_at)
                for lesson in lessons:
                    save_lesson(conn, lesson)
                all_lessons.extend(lessons)
                processed_matches.append(mid)
    finally:
        conn.close()

    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "lesson-extraction",
        "status": "ok",
        "lessons_created": len(all_lessons),
        "matches_processed": len(processed_matches),
        "lessons": [
            {
                "lesson_id": l["lesson_id"],
                "team_id": l["team_id"],
                "lesson_type": l["lesson_type"],
                "summary": l["summary"],
                "confidence_adjustment": l["confidence_adjustment"],
            }
            for l in all_lessons
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--edition", required=True)
    extract.add_argument("--root", default=".")
    extract.add_argument("--match-id", default=None, help="Extract lessons for a specific match_id")
    extract.add_argument("--all", action="store_true", dest="extract_all", help="Extract lessons for all evaluated matches")
    extract.add_argument("--now", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = extract_lessons(
        root=Path(args.root).resolve(),
        edition=args.edition,
        match_id=args.match_id,
        extract_all=args.extract_all,
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
