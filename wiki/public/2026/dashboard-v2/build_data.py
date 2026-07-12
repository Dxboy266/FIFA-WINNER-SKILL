#!/usr/bin/env python3
"""Build dashboard-v2 data from official prediction-dashboard.json + latest match ledger."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # wiki/public/2026
OUT_DIR = Path(__file__).resolve().parent
TODAY = "2026-07-12"

DASHBOARD_CANDIDATES = [
    ROOT / "reports" / "dashboard" / "prediction-dashboard.json",
    ROOT / "wiki" / "dashboard" / "prediction-dashboard.json",
]

# 看板不展示三四名（进度条 / 阶段统计）
PHASE_ORDER = [
    "group",
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "final",
]
PHASE_LABEL = {
    "group": "小组赛",
    "round_of_32": "32强",
    "round_of_16": "16强",
    "quarter_final": "8强",
    "semi_final": "半决赛",
    "final": "决赛",
}
HIDDEN_PHASES = {"third_place"}


def outcome(h, a):
    if h is None or a is None:
        return None
    if h > a:
        return "home_win"
    if a > h:
        return "away_win"
    return "draw"


def hit_kind(ps, fs):
    if not ps or not fs:
        return None
    if ps.get("home") is None or fs.get("home") is None:
        return None
    if ps["home"] == fs["home"] and ps["away"] == fs["away"]:
        return "perfect"
    if outcome(ps["home"], ps["away"]) == outcome(fs["home"], fs["away"]):
        return "result"
    return "miss"


def load_dashboard() -> dict:
    for path in DASHBOARD_CANDIDATES:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data, path
    raise FileNotFoundError("prediction-dashboard.json not found")


def load_ledger_finals() -> dict:
    ledger_path = ROOT / "match-ledger.json"
    if not ledger_path.exists():
        return {}
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    finals = {}
    for m in ledger.get("matches") or []:
        mid = m.get("match_id")
        fs = m.get("final_score")
        if not mid or not isinstance(fs, dict) or fs.get("home") is None:
            continue
        home = m.get("home_team") or {}
        away = m.get("away_team") or {}
        finals[mid] = {
            "home": fs["home"],
            "away": fs["away"],
            "home_name": home.get("name") if isinstance(home, dict) else home,
            "away_name": away.get("name") if isinstance(away, dict) else away,
            "phase": m.get("phase"),
            "kickoff_at": m.get("kickoff_at"),
            "venue": m.get("venue"),
            "status": m.get("status") or fs.get("status"),
        }
    return finals


def parse_score_text(text):
    if not text or text in ("-:-", "—", "-", "None"):
        return None
    try:
        a, b = str(text).replace("：", ":").replace(" ", "").split("-")
        return {"home": int(a), "away": int(b)}
    except Exception:
        return None


def _ranking_radar_proxy(rank):
    """Soft radar when home_radar is missing (late-bound KO placeholders)."""
    if rank is None:
        return {}
    try:
        r = float(rank)
    except (TypeError, ValueError):
        return {}
    # Rank 1 ~ 90, rank 50 ~ 55, floor 40
    base = max(40.0, min(92.0, 95.0 - r * 0.9))
    return {
        "attack": round(base),
        "defense": round(base - 3),
        "midfield": round(base - 1),
        "fitness": round(min(90.0, base + 4)),
        "recent_form": round(base - 5),
    }


def _pick_radar(raw, rank):
    if isinstance(raw, dict):
        vals = [raw.get(k) for k in ("attack", "defense", "midfield", "recent_form")]
        if any(isinstance(v, (int, float)) and v > 0 for v in vals):
            return raw
    return _ranking_radar_proxy(rank)


def extract_prediction(card: dict) -> dict | None:
    pred_block = card.get("prediction") or {}
    if isinstance(pred_block, dict) and pred_block.get("exists") is False:
        # still try flat fields
        pass
    score = None
    if isinstance(pred_block, dict):
        sc = pred_block.get("score")
        if isinstance(sc, dict) and sc.get("home") is not None:
            score = {"home": sc["home"], "away": sc["away"]}
    if not score:
        score = parse_score_text(card.get("score_text"))
    if not score or score.get("home") is None:
        return None

    conf = card.get("confidence")
    if conf in (None, "", "none", "NONE"):
        conf = (pred_block or {}).get("confidence") if isinstance(pred_block, dict) else None
    if conf in (None, "", "none", "NONE"):
        conf = "unknown"

    return {
        "score": score,
        "score_text": card.get("score_text") or f"{score['home']}-{score['away']}",
        "result": card.get("predicted_result")
        or (pred_block.get("result") if isinstance(pred_block, dict) else None),
        "result_label": card.get("predicted_result_label")
        or (pred_block.get("result_label") if isinstance(pred_block, dict) else None),
        "confidence": str(conf).lower(),
        "confidence_label": card.get("confidence_label"),
        "result_confidence": card.get("result_confidence"),
        "score_confidence": card.get("score_confidence"),
        "total_goals": card.get("total_goals")
        if card.get("total_goals") not in ("-", None, "")
        else None,
        "expected_goals_proxy": card.get("expected_goals_proxy"),
        "clean_sheet_probability": card.get("clean_sheet_probability"),
        "scoreline_distribution": card.get("scoreline_distribution"),
        "knockout_prediction": card.get("knockout_prediction"),
        "origin": card.get("prediction_origin") or card.get("prediction_source"),
        "source_path": card.get("prediction_source_path"),
        "status": card.get("prediction_status"),
        "tianji_score_text": card.get("tianji_score_text"),
        "tianji_total_goals": card.get("tianji_total_goals"),
    }


def extract_venue(card: dict) -> dict | None:
    va = card.get("venue_adaptation_context") or {}
    if not va:
        return None
    home = va.get("home") or {}
    away = va.get("away") or {}
    return {
        "venue": va.get("venue") or card.get("venue"),
        "status": va.get("status"),
        "travel_km_home": home.get("travel_km"),
        "travel_km_away": away.get("travel_km"),
        "temperature_delta_home": home.get("temperature_delta_c"),
        "temperature_delta_away": away.get("temperature_delta_c"),
        "altitude_delta_home": home.get("altitude_delta_m"),
        "altitude_delta_away": away.get("altitude_delta_m"),
        "adaptation_risk_home": home.get("adaptation_risk"),
        "adaptation_risk_away": away.get("adaptation_risk"),
        "adaptation_notes_home": home.get("adaptation_notes") or [],
        "adaptation_notes_away": away.get("adaptation_notes") or [],
    }


def extract_divination(card: dict) -> dict | None:
    div = card.get("divination_overlay") or {}
    if not div and not card.get("divination_hexagram"):
        return None
    return {
        "hexagram": div.get("hexagram") or card.get("divination_hexagram"),
        "hexagram_name": div.get("hexagram_name") or card.get("divination_hexagram"),
        "hexagram_number": div.get("hexagram_number"),
        "hexagram_interpretation": div.get("hexagram_interpretation"),
        "shichen": div.get("shichen"),
        "lunar_date": div.get("lunar_date"),
        "home_stars": div.get("home_stars") or [],
        "away_stars": div.get("away_stars") or [],
        "home_modifier": div.get("home_modifier"),
        "away_modifier": div.get("away_modifier"),
        "combined_home_fortune": div.get("combined_home_fortune"),
        "combined_away_fortune": div.get("combined_away_fortune"),
        "interpretation": div.get("interpretation"),
        "weight": div.get("weight"),
        "data_weight": div.get("data_weight"),
    }


def compact_layers(layers):
    if not isinstance(layers, list):
        return []
    out = []
    for layer in layers[:12]:
        if not isinstance(layer, dict):
            continue
        out.append(
            {
                "name": layer.get("name")
                or layer.get("layer")
                or layer.get("title")
                or layer.get("id"),
                "summary": layer.get("summary")
                or layer.get("headline")
                or layer.get("note")
                or layer.get("text"),
                "score": layer.get("score") or layer.get("edge") or layer.get("value"),
            }
        )
    return out


def main() -> None:
    dash, dash_path = load_dashboard()
    finals = load_ledger_finals()
    cards_in = dash.get("cards") or []
    try:
        dash_rel = str(dash_path.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except Exception:
        dash_rel = "wiki/public/2026/reports/dashboard/prediction-dashboard.json"
    print("source", dash_rel, "cards", len(cards_in), "ledger finals", len(finals))

    matches = []
    for card in cards_in:
        mid = card.get("match_id")
        if not mid:
            continue

        pred = extract_prediction(card)
        has_prediction = pred is not None

        # actual from card, overlay ledger if newer/missing
        actual_home = card.get("actual_score_home")
        actual_away = card.get("actual_score_away")
        final = None
        if actual_home is not None and actual_away is not None:
            final = {"home": actual_home, "away": actual_away}
        if mid in finals:
            final = {"home": finals[mid]["home"], "away": finals[mid]["away"]}

        evaluation = hit_kind(pred["score"] if pred else None, final)
        # prefer official evaluation labels when still valid (no ledger override change)
        official_eval = card.get("evaluation") or {}
        hit_class = card.get("hit_class")
        eval_label = card.get("evaluation_label")
        if evaluation == "perfect":
            hit_class = "double-hit"
            eval_label = "完美双中"
        elif evaluation == "result":
            hit_class = "result-hit"
            eval_label = "胜负命中"
        elif evaluation == "miss":
            hit_class = "miss"
            eval_label = "完全失误"
        elif has_prediction and not final:
            hit_class = "pending"
            eval_label = "待赛果"
        elif final and not has_prediction:
            hit_class = "not-predicted"
            eval_label = "未预测"
        elif not has_prediction and not final:
            if card.get("display_state") == "placeholder" or "placeholder" in str(
                card.get("data_source") or ""
            ):
                hit_class = "placeholder"
                eval_label = "占位"
            else:
                hit_class = card.get("hit_class") or "pending"
                eval_label = card.get("evaluation_label") or "待定"

        home_name = card.get("home_name") or (finals.get(mid) or {}).get("home_name") or "TBD"
        away_name = card.get("away_name") or (finals.get(mid) or {}).get("away_name") or "TBD"

        play = card.get("play_card") or {}
        layers = card.get("analysis_layers") or []

        row = {
            "match_id": mid,
            "phase": card.get("phase") or (finals.get(mid) or {}).get("phase") or "",
            "group": card.get("group") or "",
            "kickoff_at": card.get("kickoff_at")
            or card.get("local_kickoff_at")
            or (finals.get(mid) or {}).get("kickoff_at")
            or "",
            "beijing_date": card.get("beijing_date"),
            "beijing_time": card.get("beijing_time"),
            "venue": card.get("venue") or (finals.get(mid) or {}).get("venue") or "",
            "home_team": {
                "name": home_name,
                "team_id": card.get("home_id") or "",
                "ranking": card.get("home_ranking"),
                "colors": card.get("home_colors"),
            },
            "away_team": {
                "name": away_name,
                "team_id": card.get("away_id") or "",
                "ranking": card.get("away_ranking"),
                "colors": card.get("away_colors"),
            },
            "prediction": pred,
            "final_score": final,
            "has_prediction": has_prediction,
            "evaluation": evaluation,
            "hit_class": hit_class,
            "evaluation_label": eval_label,
            "display_state": card.get("display_state"),
            "is_completed": bool(final),
            "divination": extract_divination(card),
            "venue_adaptation": extract_venue(card),
            "radar": {
                "home": _pick_radar(card.get("home_radar"), card.get("home_ranking")),
                "away": _pick_radar(card.get("away_radar"), card.get("away_ranking")),
            },
            "form": {
                "home": card.get("home_form") or [],
                "away": card.get("away_form") or [],
            },
            "play_card": {
                "title": card.get("play_title") or play.get("share_title"),
                "hook": play.get("match_hook"),
                "watch_points": card.get("watch_points") or play.get("watch_points") or [],
                "risk_flags": card.get("risk_flags") or play.get("risk_flags") or [],
                "poster_angle": play.get("poster_angle"),
            },
            "analysis_layers": compact_layers(layers),
            "layer_count": len(layers) if isinstance(layers, list) else 0,
            "primary_error": card.get("primary_error")
            or (official_eval.get("primary_error") if isinstance(official_eval, dict) else None),
            "evidence_gaps": card.get("evidence_gaps") or [],
            "tianji_score_text": card.get("tianji_score_text"),
            "tianji_total_goals": card.get("tianji_total_goals"),
            "prediction_origin": card.get("prediction_origin") or card.get("prediction_source"),
            "edge_tier": card.get("edge_tier")
            or ((card.get("prediction") or {}).get("edge_tier") if isinstance(card.get("prediction"), dict) else None),
            "game_script": card.get("game_script")
            or ((card.get("prediction") or {}).get("game_script") if isinstance(card.get("prediction"), dict) else None),
            "result_confidence": card.get("result_confidence")
            or ((pred or {}).get("result_confidence") if pred else None),
            "score_confidence": card.get("score_confidence")
            or ((pred or {}).get("score_confidence") if pred else None),
        }
        matches.append(row)

    matches.sort(key=lambda x: x.get("kickoff_at") or "", reverse=True)

    # ---- stats ----
    eval_rows = [m for m in matches if m.get("evaluation")]
    perfect = sum(1 for m in eval_rows if m["evaluation"] == "perfect")
    result = sum(1 for m in eval_rows if m["evaluation"] in ("perfect", "result"))
    miss = sum(1 for m in eval_rows if m["evaluation"] == "miss")
    n = len(eval_rows)
    goals_hit = 0
    for m in eval_rows:
        ps = m["prediction"]["score"]
        fs = m["final_score"]
        if (ps["home"] + ps["away"]) == (fs["home"] + fs["away"]):
            goals_hit += 1

    by_phase = {}
    for phase in PHASE_ORDER:
        rows = [m for m in eval_rows if m["phase"] == phase]
        if not rows:
            # still show phase progress later
            continue
        bp = {
            "label": PHASE_LABEL.get(phase, phase),
            "total": len(rows),
            "perfect": sum(1 for m in rows if m["evaluation"] == "perfect"),
            "result": sum(
                1 for m in rows if m["evaluation"] in ("perfect", "result")
            ),
            "miss": sum(1 for m in rows if m["evaluation"] == "miss"),
        }
        bp["score_accuracy"] = round(bp["perfect"] / bp["total"] * 100, 1)
        bp["result_accuracy"] = round(bp["result"] / bp["total"] * 100, 1)
        by_phase[phase] = bp

    conf_buckets = {}
    for m in eval_rows:
        conf = ((m.get("prediction") or {}).get("confidence") or "unknown").lower()
        b = conf_buckets.setdefault(
            conf, {"total": 0, "perfect": 0, "result": 0, "miss": 0}
        )
        b["total"] += 1
        if m["evaluation"] == "perfect":
            b["perfect"] += 1
            b["result"] += 1
        elif m["evaluation"] == "result":
            b["result"] += 1
        else:
            b["miss"] += 1
    for conf, b in conf_buckets.items():
        b["score_accuracy"] = round(b["perfect"] / b["total"] * 100, 1) if b["total"] else 0
        b["result_accuracy"] = round(b["result"] / b["total"] * 100, 1) if b["total"] else 0

    by_date = defaultdict(list)
    for m in eval_rows:
        d = m.get("beijing_date") or (m.get("kickoff_at") or "")[:10]
        if d:
            by_date[d].append(m)

    trend = []
    cum_p = cum_r = cum_t = 0
    for d in sorted(by_date.keys()):
        rows = by_date[d]
        day_p = sum(1 for m in rows if m["evaluation"] == "perfect")
        day_r = sum(1 for m in rows if m["evaluation"] in ("perfect", "result"))
        cum_t += len(rows)
        cum_p += day_p
        cum_r += day_r
        trend.append(
            {
                "date": d,
                "day_total": len(rows),
                "day_perfect": day_p,
                "day_result": day_r,
                "cum_total": cum_t,
                "cum_perfect": cum_p,
                "cum_result": cum_r,
                "cum_score_accuracy": round(cum_p / cum_t * 100, 1),
                "cum_result_accuracy": round(cum_r / cum_t * 100, 1),
            }
        )

    # phase progress（跳过三四名）
    phase_progress = {}
    for m in matches:
        ph = m.get("phase") or "unknown"
        if ph in HIDDEN_PHASES:
            continue
        phase_progress.setdefault(ph, {"total": 0, "completed": 0, "predicted": 0})
        phase_progress[ph]["total"] += 1
        if m.get("final_score"):
            phase_progress[ph]["completed"] += 1
        if m.get("has_prediction"):
            phase_progress[ph]["predicted"] += 1

    # strength from rankings
    strength_bins = Counter()
    conf_dist = Counter()
    for m in matches:
        if m.get("prediction"):
            conf_dist[(m["prediction"].get("confidence") or "unknown").lower()] += 1
        for side in ("home_team", "away_team"):
            rank = (m.get(side) or {}).get("ranking")
            if isinstance(rank, (int, float)) and rank > 0:
                # invert rank into rough strength band by FIFA rank
                if rank <= 5:
                    strength_bins["Top5"] += 1
                elif rank <= 10:
                    strength_bins["6-10"] += 1
                elif rank <= 20:
                    strength_bins["11-20"] += 1
                elif rank <= 40:
                    strength_bins["21-40"] += 1
                else:
                    strength_bins["40+"] += 1

    hex_counter = Counter()
    fortune_home = Counter()
    for m in matches:
        d = m.get("divination") or {}
        if d.get("hexagram_name"):
            hex_counter[d["hexagram_name"]] += 1
        if d.get("combined_home_fortune"):
            fortune_home[d["combined_home_fortune"]] += 1

    # error tags from official dashboard if present
    model_issue_tags = dash.get("model_issue_tags") or []
    corrective_actions = dash.get("corrective_actions") or []
    official_summary = dash.get("summary") or {}
    daily_stats = dash.get("daily_stats") or dash.get("observation", {}).get("daily_trends") or []

    accuracy_rows = []
    for m in sorted(eval_rows, key=lambda x: x.get("kickoff_at") or "", reverse=True):
        ps = m["prediction"]["score"]
        fs = m["final_score"]
        accuracy_rows.append(
            {
                "match_id": m["match_id"],
                "phase": m["phase"],
                "kickoff_at": m["kickoff_at"],
                "beijing_date": m.get("beijing_date"),
                "home": m["home_team"]["name"],
                "away": m["away_team"]["name"],
                "pred": f"{ps['home']}-{ps['away']}",
                "actual": f"{fs['home']}-{fs['away']}",
                "confidence": m["prediction"].get("confidence"),
                "evaluation": m["evaluation"],
                "evaluation_label": m.get("evaluation_label"),
                "hexagram": (m.get("divination") or {}).get("hexagram_name"),
                "primary_error": m.get("primary_error") or "",
            }
        )

    today_matches = [
        m
        for m in matches
        if (m.get("beijing_date") == TODAY)
        or (m.get("kickoff_at") or "").startswith(TODAY)
    ]
    upcoming = [m for m in matches if m["has_prediction"] and not m["final_score"]]
    pending_eval = [
        m for m in matches if m["has_prediction"] and m["final_score"] and not m["evaluation"]
    ]
    not_predicted_done = [
        m for m in matches if m["final_score"] and not m["has_prediction"]
    ]

    out = {
        "generated_at": f"{TODAY}T16:30:00Z",
        "as_of": TODAY,
        "source": {
            "dashboard": dash_rel,
            "dashboard_generated_at": dash.get("generated_at"),
            "ledger_overlay": True,
        },
        "stats": {
            "total_predictions": sum(1 for m in matches if m["has_prediction"]),
            "total_matches": len(matches),
            "with_results": n,
            "perfect_hits": perfect,
            "result_hits": result,
            "misses": miss,
            "goals_hits": goals_hit,
            "score_accuracy": round(perfect / n * 100, 1) if n else 0,
            "result_accuracy": round(result / n * 100, 1) if n else 0,
            "goals_accuracy": round(goals_hit / n * 100, 1) if n else 0,
            "today_count": len(today_matches),
            "upcoming_count": len(upcoming),
            "not_predicted_completed": len(not_predicted_done),
            "placeholder_count": sum(1 for m in matches if m.get("hit_class") == "placeholder"),
            "by_phase": by_phase,
            "by_confidence": conf_buckets,
            "phase_progress": phase_progress,
            "strength_bins": dict(strength_bins),
            "confidence_dist": dict(conf_dist),
            "hexagram_top": hex_counter.most_common(10),
            "fortune_home": dict(fortune_home),
            "official_summary": {
                "evaluated_matches": official_summary.get("evaluated_matches"),
                "result_hit_rate": official_summary.get("result_hit_rate"),
                "score_hit_rate": official_summary.get("score_hit_rate"),
                "total_goals_hit_rate": official_summary.get("total_goals_hit_rate"),
                "score_hits": official_summary.get("score_hits"),
                "result_hits": official_summary.get("result_hits"),
                "predictions": official_summary.get("predictions"),
            },
        },
        "trend": trend,
        "daily_stats": daily_stats,
        "model_issue_tags": model_issue_tags[:12],
        "corrective_actions": corrective_actions[:12],
        "matches": matches,
        "accuracy_rows": accuracy_rows,
        "not_predicted": [
            {
                "match_id": m["match_id"],
                "home": m["home_team"]["name"],
                "away": m["away_team"]["name"],
                "actual": f"{m['final_score']['home']}-{m['final_score']['away']}",
                "phase": m["phase"],
            }
            for m in not_predicted_done
        ],
    }

    (OUT_DIR / "data.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / "data-embedded.js").write_text(
        "window.DASHBOARD_DATA = " + json.dumps(out, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    print(
        "stats preds",
        out["stats"]["total_predictions"],
        "eval",
        n,
        "result%",
        out["stats"]["result_accuracy"],
        "score%",
        out["stats"]["score_accuracy"],
        "goals%",
        out["stats"]["goals_accuracy"],
    )
    print(
        "upcoming",
        out["stats"]["upcoming_count"],
        "not_predicted_done",
        out["stats"]["not_predicted_completed"],
        "placeholder",
        out["stats"]["placeholder_count"],
    )
    print("by_phase", {k: v["result_accuracy"] for k, v in by_phase.items()})
    print("bytes", (OUT_DIR / "data-embedded.js").stat().st_size)
    # samples
    for mid in ["2026-QF-01", "2026-QF-02", "2026-QF-03", "2026-QF-04", "2026-SF-01", "2026-SF-02"]:
        m = next((x for x in matches if x["match_id"] == mid), None)
        if m:
            print(
                mid,
                m["home_team"]["name"],
                m["away_team"]["name"],
                "pred",
                (m.get("prediction") or {}).get("score_text"),
                "final",
                m.get("final_score"),
                "eval",
                m.get("evaluation"),
                m.get("evaluation_label"),
            )


if __name__ == "__main__":
    main()
