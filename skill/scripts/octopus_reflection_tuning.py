#!/usr/bin/env python3
"""OpenHuman Self-Reflection & Auto-Tuning Loop for World Cup Predictor."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Insert SCRIPT_DIR to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (
    raw_edition_root,
    edition_data_root,
    worldcup_db_path,
    load_json,
    load_match_ledger,
    parse_datetime,
    project_root,
    wiki_edition_root,
    write_json,
)
from prediction_scoring_model import (
    _build_ranking_index,
    _build_squad_index,
    _build_evidence_index,
    score_ranking_strength,
    score_squad_depth,
    score_historical_proxy,
    score_rest_travel,
    score_evidence_completeness,
    _lookup_team,
    compute_tianji_overlay,
    _tianji_score,
)

_SCORELINE_TUNING_DEFAULTS = {
    "paired_score_bias": 0.84,
    "mode_collapse_penalty": 0.88,
    "clean_sheet_bias": 1.24,
    "draw_nil_bias": 1.10,
    "loser_xg_suppression": {
        "coinflip": 0.96,
        "slight": 0.86,
        "clear": 0.74,
        "strong": 0.58,
    },
}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _default_scoreline_tuning() -> dict:
    return {
        "paired_score_bias": float(_SCORELINE_TUNING_DEFAULTS["paired_score_bias"]),
        "mode_collapse_penalty": float(_SCORELINE_TUNING_DEFAULTS["mode_collapse_penalty"]),
        "clean_sheet_bias": float(_SCORELINE_TUNING_DEFAULTS["clean_sheet_bias"]),
        "draw_nil_bias": float(_SCORELINE_TUNING_DEFAULTS["draw_nil_bias"]),
        "loser_xg_suppression": dict(_SCORELINE_TUNING_DEFAULTS["loser_xg_suppression"]),
    }


def _normalize_scoreline_tuning(raw: dict | None) -> dict:
    tuning = _default_scoreline_tuning()
    if not isinstance(raw, dict):
        return tuning
    for key in ("paired_score_bias", "mode_collapse_penalty", "clean_sheet_bias", "draw_nil_bias"):
        if key in raw:
            tuning[key] = float(raw[key])
    loser_xg = raw.get("loser_xg_suppression", {})
    if isinstance(loser_xg, dict):
        for tier in tuning["loser_xg_suppression"]:
            if tier in loser_xg:
                tuning["loser_xg_suppression"][tier] = float(loser_xg[tier])
    return tuning


def _tune_scoreline_parameters(
    tuning: dict,
    *,
    predicted_score_home: int,
    predicted_score_away: int,
    actual_score_home: int,
    actual_score_away: int,
    actual_result: str,
    is_result_correct: bool,
    is_score_correct: bool,
    lr: float,
) -> bool:
    if is_score_correct:
        return False

    changed = False
    predicted_tuple = (predicted_score_home, predicted_score_away)
    actual_tuple = (actual_score_home, actual_score_away)
    predicted_clean_sheet = predicted_score_home == 0 or predicted_score_away == 0
    actual_clean_sheet = actual_score_home == 0 or actual_score_away == 0
    score_gap = abs(actual_score_home - predicted_score_home) + abs(actual_score_away - predicted_score_away)

    if predicted_tuple in {(2, 1), (1, 2), (1, 1)} and actual_tuple != predicted_tuple:
        tuning["paired_score_bias"] = _clamp(
            tuning["paired_score_bias"] - lr * 0.60,
            0.60,
            1.08,
        )
        tuning["mode_collapse_penalty"] = _clamp(
            tuning["mode_collapse_penalty"] - lr * 0.45,
            0.68,
            1.00,
        )
        changed = True

    if actual_clean_sheet and not predicted_clean_sheet:
        tuning["clean_sheet_bias"] = _clamp(
            tuning["clean_sheet_bias"] + lr * (1.6 if is_result_correct else 1.2),
            0.90,
            2.20,
        )
        for tier, boost in {"coinflip": 0.18, "slight": 0.34, "clear": 0.40, "strong": 0.46}.items():
            tuning["loser_xg_suppression"][tier] = _clamp(
                tuning["loser_xg_suppression"][tier] - lr * boost,
                0.35,
                0.98,
            )
        changed = True
    elif predicted_clean_sheet and not actual_clean_sheet:
        tuning["clean_sheet_bias"] = _clamp(
            tuning["clean_sheet_bias"] - lr * 0.90,
            0.90,
            2.20,
        )
        for tier, boost in {"coinflip": 0.14, "slight": 0.24, "clear": 0.28, "strong": 0.32}.items():
            tuning["loser_xg_suppression"][tier] = _clamp(
                tuning["loser_xg_suppression"][tier] + lr * boost,
                0.35,
                0.98,
            )
        changed = True

    if actual_result == "draw":
        if actual_tuple == (0, 0) and predicted_tuple != (0, 0):
            tuning["draw_nil_bias"] = _clamp(
                tuning["draw_nil_bias"] + lr * 1.00,
                0.90,
                1.80,
            )
            changed = True
        elif predicted_tuple == (0, 0) and actual_tuple != (0, 0):
            tuning["draw_nil_bias"] = _clamp(
                tuning["draw_nil_bias"] - lr * 0.70,
                0.90,
                1.80,
            )
            changed = True

    if is_result_correct and score_gap >= 2:
        tuning["mode_collapse_penalty"] = _clamp(
            tuning["mode_collapse_penalty"] - lr * 0.25,
            0.68,
            1.00,
        )
        changed = True

    return changed


def normalize_and_bound_weights(
    weights: dict[str, float], min_val: float = 0.05, max_val: float = 0.50
) -> dict[str, float]:
    """Ensure weights sum to 1.0 and each is clipped between min_val and max_val."""
    keys = list(weights.keys())
    w = {k: max(min_val, min(max_val, val)) for k, val in weights.items()}
    for _ in range(100):
        total = sum(w.values())
        if abs(total - 1.0) < 1e-9:
            break
        # Normalize
        w = {k: val / total for k, val in w.items()}
        # Clip again
        w = {k: max(min_val, min(max_val, val)) for k, val in w.items()}
    return w


def compute_match_features_and_scores(
    match: dict,
    edition: str,
    all_matches: list[dict],
    ranking_index: dict,
    squad_index: dict,
    evidence_index: dict,
    global_summary: dict | None,
    daily_evidence: dict | None,
    comp_weights: dict[str, float],
    data_weight: float,
    divination_weight: float,
) -> dict:
    """Recompute all scoring components for a specific match under given weights."""
    home_team = match.get("home_team", {})
    away_team = match.get("away_team", {})
    home_id = str(home_team.get("team_id", ""))
    away_id = str(away_team.get("team_id", ""))

    home_ranking = _lookup_team(home_id, ranking_index)
    away_ranking = _lookup_team(away_id, ranking_index)
    home_squad = _lookup_team(home_id, squad_index)
    away_squad = _lookup_team(away_id, squad_index)

    kickoff = parse_datetime(str(match.get("kickoff_at", "")))
    kickoff_dt = kickoff or datetime.now(timezone.utc)

    rs_home = score_ranking_strength(home_ranking)
    rs_away = score_ranking_strength(away_ranking)

    sd_home = score_squad_depth(home_squad, global_summary)
    sd_away = score_squad_depth(away_squad, global_summary)

    hp_home = score_historical_proxy(home_ranking)
    hp_away = score_historical_proxy(away_ranking)

    rt_home = score_rest_travel(
        team_id=home_id,
        is_home=True,
        current_kickoff=kickoff_dt,
        all_matches=all_matches,
        edition=edition,
    )
    rt_away = score_rest_travel(
        team_id=away_id,
        is_home=False,
        current_kickoff=kickoff_dt,
        all_matches=all_matches,
        edition=edition,
    )

    ec_modifier = score_evidence_completeness(evidence_index)

    referee = None
    late_news = []
    if daily_evidence:
        late_news = daily_evidence.get("late_news", [])
        for m in daily_evidence.get("matches", []):
            if m.get("match_id") == match.get("match_id"):
                referee = m.get("referee")
                break

    referee_home_mod = 0.0
    referee_away_mod = 0.0
    if referee:
        strictness = referee.get("strictness", "medium")
        if strictness == "high":
            if rs_home > rs_away:
                referee_home_mod += 2.0
                referee_away_mod -= 1.0
            else:
                referee_away_mod += 2.0
                referee_home_mod -= 1.0
            if sd_home > sd_away:
                referee_home_mod += 1.0
            elif sd_away > sd_home:
                referee_away_mod += 1.0
        elif strictness == "low":
            if rs_home < rs_away:
                referee_home_mod += 2.0
            elif rs_away < rs_home:
                referee_away_mod += 2.0

    home_news_sentiment = 0.0
    away_news_sentiment = 0.0
    for news in late_news:
        news_team = news.get("team_code", "")
        if news_team:
            sentiment = news.get("sentiment", "neutral")
            impact = news.get("impact", "medium")
            factor = 2.0 if impact == "high" else 1.0 if impact == "medium" else 0.5
            if sentiment == "positive":
                if news_team == home_id:
                    home_news_sentiment += factor
                elif news_team == away_id:
                    away_news_sentiment += factor
            elif sentiment == "negative":
                if news_team == home_id:
                    home_news_sentiment -= factor
                elif news_team == away_id:
                    away_news_sentiment -= factor

    home_news_sentiment = max(-3.0, min(3.0, home_news_sentiment))
    away_news_sentiment = max(-3.0, min(3.0, away_news_sentiment))

    # Calculate data scores using dynamic weights
    data_home = (
        rs_home * comp_weights["ranking_strength"]
        + sd_home * comp_weights["squad_depth"]
        + hp_home * comp_weights["historical_proxy"]
        + rt_home * comp_weights["rest_travel"]
        + ec_modifier * comp_weights["evidence_completeness"]
        + referee_home_mod
        + home_news_sentiment
    )
    data_away = (
        rs_away * comp_weights["ranking_strength"]
        + sd_away * comp_weights["squad_depth"]
        + hp_away * comp_weights["historical_proxy"]
        + rt_away * comp_weights["rest_travel"]
        + ec_modifier * comp_weights["evidence_completeness"]
        + referee_away_mod
        + away_news_sentiment
    )

    data_home = max(0.0, min(85.0, data_home))
    data_away = max(0.0, min(85.0, data_away))

    # Divination
    divination = compute_tianji_overlay(
        match.get("kickoff_at", ""),
        match.get("match_id", ""),
        venue=str(match.get("venue", "")),
    )
    tianji_home_score = _tianji_score(data_home, float(divination["home_modifier"]))
    tianji_away_score = _tianji_score(data_away, float(divination["away_modifier"]))

    home_final = round((data_home * data_weight) + (tianji_home_score * divination_weight), 1)
    away_final = round((data_away * data_weight) + (tianji_away_score * divination_weight), 1)

    return {
        "rs_home": rs_home,
        "rs_away": rs_away,
        "sd_home": sd_home,
        "sd_away": sd_away,
        "hp_home": hp_home,
        "hp_away": hp_away,
        "rt_home": rt_home,
        "rt_away": rt_away,
        "ec_modifier": ec_modifier,
        "data_home": data_home,
        "data_away": data_away,
        "home_final": home_final,
        "away_final": away_final,
        "divination": divination,
    }


def generate_reflection_entry(
    match_id: str,
    home_name: str,
    away_name: str,
    prediction: dict,
    evaluation: dict,
    features: dict,
) -> str:
    """Generate markdown segment for the mismatch self-reflection journal."""
    actual_home = evaluation["actual_score_home"]
    actual_away = evaluation["actual_score_away"]
    pred_home = prediction["predicted_score_home"]
    pred_away = prediction["predicted_score_away"]

    actual_result = (
        "home_win"
        if actual_home > actual_away
        else "away_win"
        if actual_home < actual_away
        else "draw"
    )
    pred_result = prediction["predicted_result"]

    entry_lines = []
    entry_lines.append(f"### Match {match_id}: {home_name} vs {away_name}")
    entry_lines.append(f"- **Prediction**: {pred_home}-{pred_away} ({pred_result})")
    entry_lines.append(f"- **Actual**: {actual_home}-{actual_away} ({actual_result})")

    status_str = (
        "Match Outcome Mismatch"
        if pred_result != actual_result
        else "Scoreline Discrepancy"
    )
    entry_lines.append(f"- **Status**: {status_str}")

    entry_lines.append("- **Feature Analysis**:")
    entry_lines.append(
        f"  - Ranking Strength: Home {features['rs_home']:.1f} vs Away {features['rs_away']:.1f}"
    )
    entry_lines.append(
        f"  - Squad Depth: Home {features['sd_home']:.1f} vs Away {features['sd_away']:.1f}"
    )
    entry_lines.append(
        f"  - Historical Proxy: Home {features['hp_home']:.1f} vs Away {features['hp_away']:.1f}"
    )
    entry_lines.append(
        f"  - Rest & Travel: Home {features['rt_home']:.1f} vs Away {features['rt_away']:.1f}"
    )
    entry_lines.append(f"  - Evidence Completeness: {features['ec_modifier']:.1f}")
    entry_lines.append(
        f"  - Divination Overlay: Home Mod {features['divination']['home_modifier']:.1f} vs Away Mod {features['divination']['away_modifier']:.1f} (Hexagram: {features['divination']['shichen']})"
    )

    # Simple rule-based explanation
    explanation = []
    if pred_result != actual_result:
        explanation.append(
            f"The model failed to predict the correct result. It predicted {pred_result} but the match ended as {actual_result}."
        )
        if actual_result == "home_win":
            if (
                features["rs_home"] > features["rs_away"]
                and features["sd_away"] > features["sd_home"]
            ):
                explanation.append(
                    "The model over-weighted the Away team's squad depth, ignoring the Home team's superior FIFA ranking strength."
                )
            elif features["rt_home"] > features["rt_away"]:
                explanation.append(
                    "The model under-valued rest and travel advantages for the Home team."
                )
            else:
                explanation.append(
                    "The Home team pulled off a decisive victory despite weaker paper indicators."
                )
        elif actual_result == "away_win":
            if features["rs_away"] > features["rs_home"]:
                explanation.append(
                    "The model underestimated the Away team's ranking strength advantage."
                )
            elif features["rt_away"] > features["rt_home"]:
                explanation.append(
                    "The model did not give enough weight to the Away team's superior rest/travel recovery."
                )
            else:
                explanation.append("The Away team won despite weaker paper fundamentals.")
        elif actual_result == "draw":
            explanation.append(
                "The model predicted a decisive result but the teams were evenly matched on the pitch."
            )
    else:
        explanation.append(
            f"The model correctly predicted the {actual_result} direction, but the scoreline differed by {abs(actual_home - pred_home) + abs(actual_away - pred_away)} goals."
        )
        if actual_home > pred_home:
            explanation.append(
                f"The Home team scored {actual_home - pred_home} more goal(s) than predicted. The model might have under-valued their attacking depth or clean sheet probability."
            )
        if actual_away > pred_away:
            explanation.append(
                f"The Away team scored {actual_away - pred_away} more goal(s) than predicted. The model might have under-valued their transition offense or rest status."
            )

    explanation_str = " ".join(explanation)
    entry_lines.append(f"- **Reflection**: {explanation_str}")
    entry_lines.append("")
    return "\n".join(entry_lines)


def run_tuning_loop(
    *, root: Path, edition: str, lr: float = 0.01
) -> dict:
    """Query SQLite evaluations, log mismatch reflections, and tune prediction weights."""
    ed_root = edition_data_root(root, edition)
    db_path = worldcup_db_path(root, edition)

    if not db_path.exists():
        print(f"Database not found at {db_path}. No evaluations to tune.")
        return {"status": "no_database", "msg": "Database file does not exist."}

    # 1. Load Indexes
    ledger = load_match_ledger(root, edition)
    rankings_data = load_json(
        raw_edition_root(root, edition) / "rankings" / "fifa-men-ranking.json", {"rankings": []}
    )
    squad_data = load_json(
        ed_root / "squad-depth-features.json",
        {"teams": [], "global_summary": {}},
    )
    evidence_plan = load_json(
        ed_root / "prediction-evidence-plan.json", {"items": []}
    )

    ranking_index = _build_ranking_index(rankings_data)
    squad_index = _build_squad_index(squad_data)
    evidence_index = _build_evidence_index(evidence_plan)
    global_summary = squad_data.get("global_summary")
    all_matches = ledger.get("matches", [])
    ledger_by_id = {m.get("match_id"): m for m in all_matches}

    # 2. Connect to SQLite and fetch evaluations
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("""
            SELECT e.match_id, e.actual_score_home, e.actual_score_away,
                   e.predicted_score_home, e.predicted_score_away,
                   e.is_result_correct, e.is_score_correct,
                   p.predicted_result, p.divination_hexagram
            FROM evaluations e
            JOIN predictions p ON e.match_id = p.match_id
        """)
        eval_rows = cursor.fetchall()
    except Exception as e:
        print(f"Error reading database evaluations: {e}")
        conn.close()
        return {"status": "db_error", "msg": str(e)}
    finally:
        conn.close()

    if not eval_rows:
        print("No evaluations found in database to process.")
        return {"status": "no_evaluations", "msg": "Evaluations table is empty."}

    # 3. Load Current Hyperparameters
    hyper_path = ed_root / "model-hyperparameters.json"
    hyper = load_json(hyper_path, {})

    data_weight = float(hyper.get("data_weight", 0.60))
    divination_weight = float(hyper.get("divination_weight", 0.40))
    comp_weights = hyper.get("component_weights", {})

    # Default weights if missing
    comp_weights.setdefault("ranking_strength", 0.30)
    comp_weights.setdefault("squad_depth", 0.20)
    comp_weights.setdefault("historical_proxy", 0.20)
    comp_weights.setdefault("rest_travel", 0.15)
    comp_weights.setdefault("evidence_completeness", 0.15)

    comp_weights = {k: float(v) for k, v in comp_weights.items()}
    scoreline_tuning = _normalize_scoreline_tuning(hyper.get("scoreline_tuning"))

    # 4. Process Self-Reflection Journal and Auto-Tuning
    journal_entries = []
    tuned_any_weights = False
    tuned_any_scoreline = False

    for row in eval_rows:
        match_id = row["match_id"]
        match_obj = ledger_by_id.get(match_id)
        if not match_obj:
            continue

        home_name = match_obj["home_team"]["name"]
        away_name = match_obj["away_team"]["name"]
        actual_home = row["actual_score_home"]
        actual_away = row["actual_score_away"]
        actual_result = (
            "home_win"
            if actual_home > actual_away
            else "away_win"
            if actual_home < actual_away
            else "draw"
        )

        pred_result = row["predicted_result"]
        is_result_correct = bool(row["is_result_correct"])
        is_score_correct = bool(row["is_score_correct"])

        # Re-compute features
        kickoff = parse_datetime(str(match_obj.get("kickoff_at", "")))
        daily_evidence = {}
        if kickoff:
            date_str = kickoff.date().isoformat()
            evidence_path = ed_root / "daily-evidence" / f"{date_str}.json"
            daily_evidence = load_json(evidence_path, {})

        features = compute_match_features_and_scores(
            match=match_obj,
            edition=edition,
            all_matches=all_matches,
            ranking_index=ranking_index,
            squad_index=squad_index,
            evidence_index=evidence_index,
            global_summary=global_summary,
            daily_evidence=daily_evidence,
            comp_weights=comp_weights,
            data_weight=data_weight,
            divination_weight=divination_weight,
        )

        # Check for mismatch / discrepancy for journal logging
        score_diff_margin = abs(actual_home - row["predicted_score_home"]) + abs(
            actual_away - row["predicted_score_away"]
        )
        is_mismatch = (not is_result_correct) or (score_diff_margin >= 2)

        if is_mismatch:
            # Generate reflection log
            pred_dict = {
                "predicted_score_home": row["predicted_score_home"],
                "predicted_score_away": row["predicted_score_away"],
                "predicted_result": pred_result,
            }
            eval_dict = {
                "actual_score_home": actual_home,
                "actual_score_away": actual_away,
            }
            entry = generate_reflection_entry(
                match_id, home_name, away_name, pred_dict, eval_dict, features
            )
            journal_entries.append(entry)

        if _tune_scoreline_parameters(
            scoreline_tuning,
            predicted_score_home=int(row["predicted_score_home"]),
            predicted_score_away=int(row["predicted_score_away"]),
            actual_score_home=int(actual_home),
            actual_score_away=int(actual_away),
            actual_result=actual_result,
            is_result_correct=is_result_correct,
            is_score_correct=is_score_correct,
            lr=lr,
        ):
            tuned_any_scoreline = True

        # Perform Win/Loss weight tuning for incorrect predictions
        if not is_result_correct:
            # Determine W (winner) and L (loser) team contexts
            w_team, l_team = None, None
            if actual_result == "home_win":
                w_team = "home"
                l_team = "away"
            elif actual_result == "away_win":
                w_team = "away"
                l_team = "home"
            elif actual_result == "draw":
                # Draw scenario: team that overperformed relative to model expectations
                if pred_result == "home_win":
                    w_team = "away"
                    l_team = "home"
                elif pred_result == "away_win":
                    w_team = "home"
                    l_team = "away"

            if w_team and l_team:
                tuned_any_weights = True

                # Get component scores for winner/loser
                rs_w = features[f"rs_{w_team}"]
                rs_l = features[f"rs_{l_team}"]
                sd_w = features[f"sd_{w_team}"]
                sd_l = features[f"sd_{l_team}"]
                hp_w = features[f"hp_{w_team}"]
                hp_l = features[f"hp_{l_team}"]
                rt_w = features[f"rt_{w_team}"]
                rt_l = features[f"rt_{l_team}"]

                # Calculate normalized deltas
                delta_rs = (rs_w - rs_l) / 100.0
                delta_sd = (sd_w - sd_l) / 100.0
                delta_hp = (hp_w - hp_l) / 100.0
                delta_rt = (rt_w - rt_l) / 100.0

                # Update weights
                comp_weights["ranking_strength"] += lr * delta_rs
                comp_weights["squad_depth"] += lr * delta_sd
                comp_weights["historical_proxy"] += lr * delta_hp
                comp_weights["rest_travel"] += lr * delta_rt

                # Global Weights adjustment (Data Weight vs Divination Weight)
                # Compute data-only predicted outcome
                gap_data = features["data_home"] - features["data_away"]
                data_predicted_outcome = (
                    "home_win"
                    if gap_data > 1.5
                    else "away_win"
                    if gap_data < -1.5
                    else "draw"
                )

                # Check if Divination corrected a data error or caused a data success to fail
                if (
                    data_predicted_outcome != actual_result
                    and pred_result == actual_result
                ):
                    # Tianji corrected the mistake
                    divination_weight = min(0.40, divination_weight + lr)
                    data_weight = 1.0 - divination_weight
                elif (
                    data_predicted_outcome == actual_result
                    and pred_result != actual_result
                ):
                    # Tianji ruined a correct data-driven prediction
                    divination_weight = max(0.0, divination_weight - lr)
                    data_weight = 1.0 - divination_weight

    # 5. Write Reflection Journal to Wiki
    journal_dir = wiki_edition_root(root, edition) / "synthesis"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "self-reflection-journal.md"

    # Read existing entries to prevent duplication
    existing_entries_by_match = {}
    if journal_file.exists():
        content = journal_file.read_text(encoding="utf-8")
        # Parse existing matches headers like "### Match 2026-GA-01"
        matches_found = re.findall(r"### Match ([\w\-]+)", content)
        for m_id in matches_found:
            existing_entries_by_match[m_id] = True

    # Filter out entries that already exist in the journal
    new_journal_entries = []
    for entry in journal_entries:
        match_id_match = re.search(r"### Match ([\w\-]+)", entry)
        if match_id_match:
            m_id = match_id_match.group(1)
            if m_id not in existing_entries_by_match:
                new_journal_entries.append(entry)

    if new_journal_entries:
        mode = "a" if journal_file.exists() else "w"
        with open(journal_file, mode, encoding="utf-8") as f:
            if mode == "w":
                f.write(
                    "# Model Self-Reflection & Adjustment Journal\n\n"
                    "记录模型预测失误或偏差的细节，以及背后的调参反馈。\n\n"
                )
            for entry in new_journal_entries:
                f.write(entry + "\n")
        print(f"Logged {len(new_journal_entries)} new reflection journal entries.")

    # 6. Normalize, Bound and Save Hyperparameters
    if tuned_any_weights or tuned_any_scoreline:
        comp_weights = normalize_and_bound_weights(
            comp_weights, min_val=0.05, max_val=0.50
        )

        # Enforce minimum data_weight safety bound
        if data_weight < 0.60:
            data_weight = 0.60
            divination_weight = 0.40

        updated_hyper = {
            "data_weight": round(data_weight, 3),
            "divination_weight": round(divination_weight, 3),
            "component_weights": {k: round(v, 4) for k, v in comp_weights.items()},
            "scoreline_tuning": {
                "paired_score_bias": round(float(scoreline_tuning["paired_score_bias"]), 4),
                "mode_collapse_penalty": round(float(scoreline_tuning["mode_collapse_penalty"]), 4),
                "clean_sheet_bias": round(float(scoreline_tuning["clean_sheet_bias"]), 4),
                "draw_nil_bias": round(float(scoreline_tuning["draw_nil_bias"]), 4),
                "loser_xg_suppression": {
                    tier: round(float(value), 4)
                    for tier, value in scoreline_tuning["loser_xg_suppression"].items()
                },
            },
        }
        write_json(hyper_path, updated_hyper)
        print(f"Saved tuned hyperparameters to {hyper_path}")
    else:
        # Save default or existing if tuned_any_weights is False but we want to initialize it
        if not hyper_path.exists():
            updated_hyper = {
                "data_weight": round(data_weight, 3),
                "divination_weight": round(divination_weight, 3),
                "component_weights": {k: round(v, 4) for k, v in comp_weights.items()},
                "scoreline_tuning": {
                    "paired_score_bias": round(float(scoreline_tuning["paired_score_bias"]), 4),
                    "mode_collapse_penalty": round(float(scoreline_tuning["mode_collapse_penalty"]), 4),
                    "clean_sheet_bias": round(float(scoreline_tuning["clean_sheet_bias"]), 4),
                    "draw_nil_bias": round(float(scoreline_tuning["draw_nil_bias"]), 4),
                    "loser_xg_suppression": {
                        tier: round(float(value), 4)
                        for tier, value in scoreline_tuning["loser_xg_suppression"].items()
                    },
                },
            }
            write_json(hyper_path, updated_hyper)
            print(f"Initialized default hyperparameters at {hyper_path}")

    return {
        "status": "success",
        "journal_entries_written": len(new_journal_entries),
        "tuned_any_weights": tuned_any_weights,
        "tuned_any_scoreline": tuned_any_scoreline,
        "weights": {
            "data_weight": data_weight,
            "divination_weight": divination_weight,
            "component_weights": comp_weights,
            "scoreline_tuning": scoreline_tuning,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-Reflection & Parameter Tuning Loop.")
    parser.add_argument("command", choices=["tune"])
    parser.add_argument("--edition", "-e", default="2026")
    parser.add_argument("--root", "-r", default=".")
    parser.add_argument("--lr", "-l", type=float, default=0.01, help="Learning rate")

    args = parser.parse_args()
    res = run_tuning_loop(
        root=Path(args.root).resolve(),
        edition=args.edition,
        lr=args.lr,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
