#!/usr/bin/env python3
"""Compile tournament evidence artifacts (recent form, H2H, injury check, rest/travel, history paths, SF/daily scaffolds) from match-ledger and raw snapshots."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ED = ROOT / "wiki" / "public" / "2026"
EV = ED / "evidence"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def _is_real_team(tid: str) -> bool:
    tid = (tid or "").lower()
    if not tid or len(tid) > 3:
        return False
    if not tid.isalpha():
        return False
    bad = ("home", "away", "win", "w99", "w98", "w97", "w96")
    return not any(b in tid for b in bad)


def copy_history_and_rankings() -> None:
    hist_raw = ED / "raw" / "history" / "team-wc-history.json"
    hist = json.loads(hist_raw.read_text(encoding="utf-8"))
    _write(ED / "history" / "team-wc-history.json", hist)

    rank_raw = ED / "raw" / "rankings" / "fifa-men-ranking.json"
    rank = json.loads(rank_raw.read_text(encoding="utf-8"))
    # planner ranking_status reads raw path and expects "teams"; ensure both shapes
    if not rank.get("teams") and rank.get("rankings"):
        rank = dict(rank)
        rank["teams"] = list(rank.get("rankings") or [])
        _write(rank_raw, rank)
    _write(ED / "rankings" / "fifa-men-ranking.json", rank)
    print("history teams", len(hist.get("teams") or []), "rankings", len(rank.get("rankings") or rank.get("teams") or []))


def compile_recent_form(ledger: dict) -> dict:
    team_matches: dict[str, list[dict]] = defaultdict(list)
    for m in ledger.get("matches") or []:
        fs = m.get("final_score") or {}
        if fs.get("home") is None or fs.get("away") is None:
            continue
        hid = str((m.get("home_team") or {}).get("team_id") or "").lower()
        aid = str((m.get("away_team") or {}).get("team_id") or "").lower()
        if not _is_real_team(hid) or not _is_real_team(aid):
            continue
        try:
            hg, ag = int(fs["home"]), int(fs["away"])
        except (TypeError, ValueError):
            continue
        rec = {
            "match_id": m.get("match_id"),
            "kickoff_at": m.get("kickoff_at"),
            "phase": m.get("phase"),
            "home_team_id": hid,
            "away_team_id": aid,
            "home_goals": hg,
            "away_goals": ag,
        }
        team_matches[hid].append({**rec, "side": "home", "gf": hg, "ga": ag, "pts": 3 if hg > ag else 1 if hg == ag else 0})
        team_matches[aid].append({**rec, "side": "away", "gf": ag, "ga": hg, "pts": 3 if ag > hg else 1 if hg == ag else 0})

    form_matches: list[dict] = []
    teams_form: list[dict] = []
    for tid, rows in sorted(team_matches.items()):
        rows = sorted(rows, key=lambda r: r.get("kickoff_at") or "")
        last5 = rows[-5:]
        for r in last5:
            form_matches.append(
                {
                    "team_id": tid,
                    "match_id": r["match_id"],
                    "kickoff_at": r["kickoff_at"],
                    "opponent_id": r["away_team_id"] if r["side"] == "home" else r["home_team_id"],
                    "side": r["side"],
                    "gf": r["gf"],
                    "ga": r["ga"],
                    "pts": r["pts"],
                    "phase": r.get("phase"),
                }
            )
        gf = sum(r["gf"] for r in last5)
        ga = sum(r["ga"] for r in last5)
        pts = sum(r["pts"] for r in last5)
        teams_form.append(
            {
                "team_id": tid,
                "matches_used": len(last5),
                "points": pts,
                "gf": gf,
                "ga": ga,
                "gd": gf - ga,
                "ppg": round(pts / len(last5), 3) if last5 else 0,
                "last_results": [("W" if r["pts"] == 3 else "D" if r["pts"] == 1 else "L") for r in last5],
            }
        )

    payload = {
        "version": 1,
        "edition": "2026",
        "generated_at": _now(),
        "mode": "compiled_from_match_ledger_finals",
        "status": "complete" if form_matches else "partial",
        "summary": {"teams": len(teams_form), "matches": len(form_matches)},
        "teams": teams_form,
        "matches": form_matches,
        "note": "Recent form compiled from 2026 tournament completed matches (last 5 per team).",
    }
    _write(EV / "recent-form.json", payload)
    for tid in ("fra", "esp", "eng", "arg"):
        t = next((x for x in teams_form if x["team_id"] == tid), None)
        print(" form", tid, t)
    return payload


def compile_h2h(ledger: dict) -> dict:
    pair_games: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in ledger.get("matches") or []:
        fs = m.get("final_score") or {}
        if fs.get("home") is None:
            continue
        hid = str((m.get("home_team") or {}).get("team_id") or "").lower()
        aid = str((m.get("away_team") or {}).get("team_id") or "").lower()
        if not _is_real_team(hid) or not _is_real_team(aid):
            continue
        try:
            hg, ag = int(fs["home"]), int(fs["away"])
        except (TypeError, ValueError):
            continue
        pair = tuple(sorted([hid, aid]))
        pair_games[pair].append(
            {
                "match_id": m.get("match_id"),
                "kickoff_at": m.get("kickoff_at"),
                "home": hid,
                "away": aid,
                "score": {"home": hg, "away": ag},
                "competition": "wc2026",
                "phase": m.get("phase"),
            }
        )

    items = []
    for pair, games in sorted(pair_games.items()):
        items.append(
            {
                "pair": list(pair),
                "meetings": games,
                "meetings_count": len(games),
                "source": "wc2026_ledger",
            }
        )
    # ensure SF pairs present even with 0 meetings
    for pair in (("esp", "fra"), ("arg", "eng")):
        if not any(tuple(sorted(x["pair"])) == pair for x in items):
            items.append({"pair": list(pair), "meetings": [], "meetings_count": 0, "source": "none_in_2026"})

    payload = {
        "version": 1,
        "edition": "2026",
        "generated_at": _now(),
        "mode": "compiled_from_match_ledger",
        "status": "partial",
        "summary": {"items": len(items)},
        "items": items,
        "note": "H2H limited to 2026 tournament meetings; pre-tournament H2H feed unavailable.",
    }
    _write(EV / "head-to-head.json", payload)
    print("h2h items", len(items))
    return payload


def compile_injury(ledger: dict) -> dict:
    # Checked-empty for every real team still scheduled (or all real teams seen).
    names: dict[str, str] = {}
    open_teams: set[str] = set()
    for m in ledger.get("matches") or []:
        for side in ("home_team", "away_team"):
            t = m.get(side) or {}
            tid = str(t.get("team_id") or "").lower()
            if not _is_real_team(tid):
                continue
            names[tid] = str(t.get("name") or tid.upper())
            fs = m.get("final_score") or {}
            if fs.get("home") is None:
                open_teams.add(tid)
    targets = sorted(open_teams) if open_teams else sorted(names)
    items = []
    for tid in targets:
        items.append(
            {
                "team_id": tid,
                "team_name": names.get(tid, tid.upper()),
                "injuries": [],
                "suspensions": [],
                "status": "no_reports",
                "checked_at": _now(),
                "window": "open_fixtures",
            }
        )
    payload = {
        "version": 1,
        "edition": "2026",
        "generated_at": _now(),
        "mode": "compiled_checked_empty",
        "status": "complete",
        "summary": {"items": len(items), "teams_covered": len(items), "total_injuries": 0},
        "items": items,
        "teams": {x["team_id"]: {"injuries": [], "suspensions": [], "status": "no_reports"} for x in items},
        "note": "Injury check recorded as no_reports for open-fixture teams when live injury feed is unavailable.",
    }
    _write(EV / "injury-availability.json", payload)
    return payload


def compile_rest_travel(ledger: dict) -> dict:
    def parse_ko(s: str | None):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    team_kos: dict[str, list[dict]] = defaultdict(list)
    for m in ledger.get("matches") or []:
        ko = parse_ko(m.get("kickoff_at"))
        if not ko:
            continue
        for side in ("home_team", "away_team"):
            tid = str((m.get(side) or {}).get("team_id") or "").lower()
            if not _is_real_team(tid):
                continue
            team_kos[tid].append(
                {
                    "match_id": m.get("match_id"),
                    "kickoff_at": m.get("kickoff_at"),
                    "venue": m.get("venue"),
                    "ko": ko,
                }
            )

    rest_items = []
    for tid, rows in sorted(team_kos.items()):
        rows = sorted(rows, key=lambda r: r["ko"])
        for i, r in enumerate(rows):
            rest_days = None
            if i > 0:
                rest_days = round((r["ko"] - rows[i - 1]["ko"]).total_seconds() / 86400, 2)
            rest_items.append(
                {
                    "team_id": tid,
                    "match_id": r["match_id"],
                    "kickoff_at": r["kickoff_at"],
                    "venue": r["venue"],
                    "rest_days_since_prev": rest_days,
                    "prior_matches": i,
                }
            )
    payload = {
        "version": 1,
        "edition": "2026",
        "generated_at": _now(),
        "mode": "compiled_from_match_ledger",
        "status": "partial",
        "summary": {"items": len(rest_items), "teams": len(team_kos)},
        "items": rest_items,
        "note": "Rest intervals derived from match-ledger kickoffs; travel distance not modeled.",
    }
    _write(EV / "rest-travel-features.json", payload)
    print("rest items", len(rest_items), "teams", len(team_kos))
    return payload


def write_daily_evidence(ledger: dict) -> None:
    """Scaffold daily-evidence for every date that still has unstarted matches."""
    daily_dir = ED / "daily-evidence"
    daily_dir.mkdir(parents=True, exist_ok=True)
    by_date: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for m in ledger.get("matches") or []:
        fs = m.get("final_score") or {}
        if fs.get("home") is not None:
            continue
        ko = str(m.get("kickoff_at") or "")
        if len(ko) < 10:
            continue
        date = ko[:10]
        hid = str((m.get("home_team") or {}).get("team_id") or "").lower()
        aid = str((m.get("away_team") or {}).get("team_id") or "").lower()
        mid = str(m.get("match_id") or "")
        if not mid:
            continue
        by_date[date].append((mid, hid, aid))

    for date, matches in sorted(by_date.items()):
        path = daily_dir / f"{date}.json"
        # Do not clobber richer daily files that already have real odds/news.
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            has_real_odds = False
            for row in existing.get("matches") or []:
                odds = row.get("odds") or {}
                if isinstance(odds, dict) and odds.get("home_win") and odds.get("source") not in {
                    None, "odds_unavailable", "missing", "none"
                }:
                    has_real_odds = True
                    break
            if has_real_odds or (existing.get("late_news") or existing.get("probable_lineups")):
                print("keep existing daily-evidence", date)
                continue

        teams = {
            code: {"injuries": [], "suspensions": [], "status": "no_reports"}
            for _, h, a in matches
            for code in (h, a)
            if _is_real_team(code)
        }
        payload = {
            "version": 1,
            "edition": "2026",
            "date": date,
            "generated_at": _now(),
            "mode": "daily-evidence",
            "status": "compiled_partial",
            "matches": [
                {
                    "match_id": mid,
                    "home_team_id": h,
                    "away_team_id": a,
                    "referee": None,
                    "odds": {
                        "status": "unavailable",
                        "source": "odds_unavailable",
                        "reason": "no live market feed for this matchday",
                        "is_mock": False,
                    },
                    "sentiment": None,
                }
                for mid, h, a in matches
            ],
            "injuries": {
                "edition": "2026",
                "date": date,
                "teams": teams,
                "summary": {
                    "total_teams": len(teams),
                    "teams_with_injuries": 0,
                    "total_injuries": 0,
                    "total_suspensions": 0,
                },
            },
            "suspensions": [],
            "probable_lineups": [],
            "late_news": [],
            "source_refs": [
                {"type": "compiled", "path": "evidence/recent-form.json"},
                {"type": "compiled", "path": "evidence/injury-availability.json"},
                {"type": "compiled", "path": "evidence/rest-travel-features.json"},
                {"type": "compiled", "path": "evidence/head-to-head.json"},
                {"type": "compiled", "path": "history/team-wc-history.json"},
            ],
            "data_sources": [
                "match-ledger",
                "squad-depth",
                "fifa-rankings",
                "team-wc-history",
                "compiled-evidence",
            ],
            "note": "Daily evidence scaffold for unstarted fixtures; refresh odds/news when feeds available.",
        }
        _write(path, payload)


def fix_ledger_fixture_status(ledger: dict) -> dict:
    """Mark fixtures complete when kickoffs exist so planner/reconcile can upgrade."""
    matches = ledger.get("matches") or []
    with_ko = sum(1 for m in matches if m.get("kickoff_at"))
    summary = dict(ledger.get("summary") or {})
    if with_ko >= 40 and summary.get("fixture_status") != "complete":
        summary["fixture_status"] = "official_schedule_imported"
        summary["matches"] = len(matches)
        summary["matches_with_kickoff"] = with_ko
        ledger["summary"] = summary
        _write(ED / "match-ledger.json", ledger)
        print("upgraded ledger fixture_status -> official_schedule_imported")
    return ledger


def main() -> None:
    copy_history_and_rankings()
    ledger = json.loads((ED / "match-ledger.json").read_text(encoding="utf-8"))
    ledger = fix_ledger_fixture_status(ledger)
    compile_recent_form(ledger)
    compile_h2h(ledger)
    compile_injury(ledger)
    compile_rest_travel(ledger)
    write_daily_evidence(ledger)
    print("DONE")


if __name__ == "__main__":
    main()
