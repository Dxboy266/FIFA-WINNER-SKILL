#!/usr/bin/env python3
"""Fetch actual match results and update match-ledger.

Supports multiple data sources (tried in priority order):
  1. **web** (free, no key needed) - Scrape public result sites
  2. **football-data.org** (free, requires FOOTBALL_DATA_API_KEY in .env)
  3. Manual JSON input (--results-json) or inline (--inline)

Usage:
  # Fetch from public web (no API key needed!)
  python fetch_match_results.py web --edition 2026 --from 2026-06-13 --to 2026-06-14

  # Auto-fetch (tries web first, then football-data.org API)
  python fetch_match_results.py fetch --edition 2026 --from 2026-06-13 --to 2026-06-14

  # Quick inline update (single match)
  python fetch_match_results.py inline --edition 2026 --home Brazil --away Morocco --score 1-1

  # Manual JSON file
  python fetch_match_results.py apply --edition 2026 --results-json results.json

  # Show which matches lack results
  python fetch_match_results.py status --edition 2026
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# Load .env file if present (for local development, NOT committed to git)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv not installed, env vars must be set manually

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    beijing_datetime,
    canonical_matches,
    edition_data_root,
    iso_now,
    load_json,
    load_match_ledger,
    parse_datetime,
    write_json,
    bump_public_version,
    worldcup_db_path,
)


# ---------------------------------------------------------------------------
# Team name normalization
# ---------------------------------------------------------------------------

# Mapping from ledger team_id to common API names
TEAM_ID_TO_NAMES: dict[str, list[str]] = {
    "mex": ["Mexico", "MEX"],
    "rsa": ["South Africa", "RSA"],
    "kor": ["South Korea", "Korea Republic", "KOR"],
    "cze": ["Czechia", "Czech Republic", "CZE"],
    "can": ["Canada", "CAN"],
    "bih": ["Bosnia and Herzegovina", "Bosnia-Herzegovina", "Bosnia-H.", "Bosnia-H", "BIH"],
    "qat": ["Qatar", "QAT"],
    "sui": ["Switzerland", "SUI"],
    "bra": ["Brazil", "BRA"],
    "mar": ["Morocco", "MAR"],
    "hai": ["Haiti", "HAI"],
    "sco": ["Scotland", "SCO"],
    "usa": ["United States", "USA", "US"],
    "par": ["Paraguay", "PAR"],
    "aus": ["Australia", "AUS"],
    "tur": ["Türkiye", "Turkey", "TUR"],
    "ger": ["Germany", "GER"],
    "cuw": ["Curaçao", "Curacao", "CUW"],
    "civ": ["Ivory Coast", "Côte d'Ivoire", "Côte D'Ivoire", "CIV"],
    "ecu": ["Ecuador", "ECU"],
    "ned": ["Netherlands", "NED", "Holland"],
    "jpn": ["Japan", "JPN"],
    "swe": ["Sweden", "SWE"],
    "tun": ["Tunisia", "TUN"],
    "bel": ["Belgium", "BEL"],
    "egy": ["Egypt", "EGY"],
    "irn": ["Iran", "IRN"],
    "nzl": ["New Zealand", "NZL"],
    "esp": ["Spain", "ESP"],
    "cpv": ["Cape Verde", "Cabo Verde", "CPV"],
    "ksa": ["Saudi Arabia", "KSA"],
    "uru": ["Uruguay", "URU"],
    "fra": ["France", "FRA"],
    "sen": ["Senegal", "SEN"],
    "irq": ["Iraq", "IRQ"],
    "nor": ["Norway", "NOR"],
    "arg": ["Argentina", "ARG"],
    "alg": ["Algeria", "ALG"],
    "aut": ["Austria", "AUT"],
    "jor": ["Jordan", "JOR"],
    "por": ["Portugal", "POR"],
    "cod": ["DR Congo", "Congo DR", "COD"],
    "uzb": ["Uzbekistan", "UZB"],
    "col": ["Colombia", "COL"],
    "eng": ["England", "ENG"],
    "cro": ["Croatia", "CRO"],
    "gha": ["Ghana", "GHA"],
    "pan": ["Panama", "PAN"],
    "chn": ["China", "CHN"],
    "ind": ["India", "IND"],
    "tha": ["Thailand", "THA"],
    "cmr": ["Cameroon", "CMR"],
    "nga": ["Nigeria", "NGA"],
    "mli": ["Mali", "MLI"],
    "tog": ["Togo", "TOG"],
    "gam": ["Gambia", "GAM"],
    "guu": ["Guinea", "GUU"],
    "bfu": ["Burkina Faso", "BFU"],
    "zam": ["Zambia", "ZAM"],
    "cgo": ["Congo", "CGO"],
    "tan": ["Tanzania", "TAN"],
    "ken": ["Kenya", "KEN"],
    "uga": ["Uganda", "UGA"],
    "rwa": ["Rwanda", "RWA"],
    "mrt": ["Mauritania", "MRT"],
    "com": ["Comoros", "COM"],
    "mad": ["Madagascar", "MAD"],
    "moz": ["Mozambique", "MOZ"],
    "mal": ["Malawi", "MAL"],
    "gab": ["Gabon", "GAB"],
    "lby": ["Libya", "LBY"],
    "sud": ["Sudan", "SUD"],
    "ssd": ["South Sudan", "SSD"],
    "eth": ["Ethiopia", "ETH"],
    "som": ["Somalia", "SOM"],
    "dji": ["Djibouti", "DJI"],
    "eri": ["Eritrea", "ERI"],
    "guy": ["Guinea-Bissau", "GAB"],
    "sle": ["Sierra Leone", "SLE"],
    "lib": ["Liberia", "LIB"],
    "civ": ["Ivory Coast", "Côte d'Ivoire", "Côte D'Ivoire", "CIV"],
    "bfa": ["Burkina Faso", "BFA"],
    "ner": ["Niger", "NER"],
    "cha": ["Chad", "CHA"],
    "cpv": ["Cape Verde", "CPV"],
    "stp": ["São Tomé and Príncipe", "STP"],
    "eqg": ["Equatorial Guinea", "EQG"],
}

def _normalize(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum() or c in " ")


# Reverse lookup: normalized common name -> team_id
_NAME_TO_ID: dict[str, str] = {}
for tid, names in TEAM_ID_TO_NAMES.items():
    for name in names:
        normalized = _normalize(name)
        if normalized:
            _NAME_TO_ID[normalized] = tid

def _find_team_id(name: str) -> str | None:
    n = _normalize(name)
    # Direct match first
    if n in _NAME_TO_ID:
        return _NAME_TO_ID[n]
    # Partial match
    for key, tid in _NAME_TO_ID.items():
        if n in key or key in n:
            return tid
    return None


def _result_matches_fixture(match: dict, result: dict, *, swapped: bool = False) -> bool:
    home_team = match.get("home_team", {})
    away_team = match.get("away_team", {})
    ledger_home_name = home_team.get("name", "") if isinstance(home_team, dict) else str(home_team)
    ledger_away_name = away_team.get("name", "") if isinstance(away_team, dict) else str(away_team)
    ledger_home_id = str(home_team.get("team_id", "") if isinstance(home_team, dict) else "").lower()
    ledger_away_id = str(away_team.get("team_id", "") if isinstance(away_team, dict) else "").lower()

    result_home = str(result.get("home_team", "") or "")
    result_away = str(result.get("away_team", "") or "")
    if swapped:
        result_home, result_away = result_away, result_home

    result_home_id = str(_find_team_id(result_home) or "").lower()
    result_away_id = str(_find_team_id(result_away) or "").lower()
    if ledger_home_id and ledger_away_id and result_home_id and result_away_id:
        if ledger_home_id == result_home_id and ledger_away_id == result_away_id:
            return True

    home_hit = (
        _normalize(ledger_home_name) in _normalize(result_home)
        or _normalize(result_home) in _normalize(ledger_home_name)
    )
    away_hit = (
        _normalize(ledger_away_name) in _normalize(result_away)
        or _normalize(result_away) in _normalize(ledger_away_name)
    )
    return home_hit and away_hit


def _score_pair(value: dict | None) -> dict[str, int | None]:
    value = value or {}
    return {
        "home": value.get("home"),
        "away": value.get("away"),
    }


def _has_score_pair(value: dict | None) -> bool:
    value = value or {}
    return value.get("home") is not None and value.get("away") is not None


def _subtract_score_pair(total: dict, segment: dict) -> dict[str, int | None]:
    if not (_has_score_pair(total) and _has_score_pair(segment)):
        return {"home": None, "away": None}
    home = int(total["home"]) - int(segment["home"])
    away = int(total["away"]) - int(segment["away"])
    if home < 0 or away < 0:
        return {"home": None, "away": None}
    return {"home": home, "away": away}


def _score_result(home_score: int | None, away_score: int | None) -> str:
    if home_score is None or away_score is None:
        return ""
    if home_score > away_score:
        return "home_win"
    if away_score > home_score:
        return "away_win"
    return "draw"


def _winner_side(value: str | None) -> str:
    text = str(value or "").strip().upper()
    if text == "HOME_TEAM":
        return "home"
    if text == "AWAY_TEAM":
        return "away"
    if text == "HOME_WIN":
        return "home"
    if text == "AWAY_WIN":
        return "away"
    return ""


def _swap_pair(value: dict | None) -> dict:
    value = value or {}
    swapped = dict(value)
    swapped["home"] = value.get("away")
    swapped["away"] = value.get("home")
    result = _score_result(swapped.get("home"), swapped.get("away"))
    if "result" in swapped:
        swapped["result"] = result
    return swapped


def _swap_knockout_result(knockout_result: dict | None) -> dict | None:
    if not knockout_result:
        return None
    swapped = {
        "regular_time": _swap_pair(knockout_result.get("regular_time")),
        "extra_time": _swap_pair(knockout_result.get("extra_time")),
        "penalties": _swap_pair(knockout_result.get("penalties")),
        "advance": dict(knockout_result.get("advance") or {}),
        "full_time": _swap_pair(knockout_result.get("full_time")),
    }
    for key in ("extra_time", "penalties"):
        if "played" in (knockout_result.get(key) or {}):
            swapped[key]["played"] = bool((knockout_result.get(key) or {}).get("played"))
    advance_winner = str((knockout_result.get("advance") or {}).get("winner") or "")
    if advance_winner == "home":
        swapped["advance"]["winner"] = "away"
    elif advance_winner == "away":
        swapped["advance"]["winner"] = "home"
    penalties_winner = str((knockout_result.get("penalties") or {}).get("winner") or "")
    if penalties_winner == "home":
        swapped["penalties"]["winner"] = "away"
    elif penalties_winner == "away":
        swapped["penalties"]["winner"] = "home"
    return swapped


def _apply_knockout_result_to_match(match: dict, result: dict) -> bool:
    knockout_result = result.get("knockout_result")
    if not knockout_result:
        return False
    if result.get("_swapped"):
        knockout_result = _swap_knockout_result(knockout_result)
    if not knockout_result:
        return False
    match["knockout_result"] = knockout_result
    return True


def _build_knockout_result_payload(score: dict, winner_value: str | None) -> dict | None:
    duration = str(score.get("duration") or "").upper()
    full_time = _score_pair(score.get("fullTime"))
    regular_time = _score_pair(score.get("regularTime"))
    extra_time = _score_pair(score.get("extraTime"))
    penalties = _score_pair(score.get("penalties"))

    # football-data.org is inconsistent for knockout ties:
    # - EXTRA_TIME may omit regularTime while fullTime is after 120' and extraTime is the ET segment.
    # - PENALTY_SHOOTOUT fullTime can include penalty goals, while penalties may be malformed.
    if not _has_score_pair(regular_time):
        if duration == "REGULAR":
            regular_time = dict(full_time)
        elif duration == "EXTRA_TIME":
            regular_time = _subtract_score_pair(full_time, extra_time)
        elif duration == "PENALTY_SHOOTOUT" and _has_score_pair(full_time):
            full_without_extra = _subtract_score_pair(full_time, extra_time)
            if _has_score_pair(full_without_extra):
                regular_time = full_without_extra

    if duration == "PENALTY_SHOOTOUT":
        shootout = _subtract_score_pair(_subtract_score_pair(full_time, regular_time), extra_time)
        if _has_score_pair(shootout):
            penalties = shootout

    has_extra_time = _has_score_pair(extra_time)
    has_penalties = _has_score_pair(penalties)
    has_regular = _has_score_pair(regular_time)
    if not (has_regular or has_extra_time or has_penalties):
        return None

    advance_side = _winner_side(winner_value)
    if not advance_side:
        advance_side = _winner_side(score.get("winner"))
    if not advance_side:
        advance_side = _winner_side(_score_result(full_time.get("home"), full_time.get("away")))

    return {
        "regular_time": {
            "home": regular_time.get("home"),
            "away": regular_time.get("away"),
            "result": _score_result(regular_time.get("home"), regular_time.get("away")),
        },
        "extra_time": {
            "played": has_extra_time,
            "home": extra_time.get("home"),
            "away": extra_time.get("away"),
            "result": _score_result(extra_time.get("home"), extra_time.get("away")),
        },
        "penalties": {
            "played": has_penalties,
            "home": penalties.get("home"),
            "away": penalties.get("away"),
            "winner": advance_side if has_penalties else "",
        },
        "advance": {
            "winner": advance_side,
        },
        "full_time": {
            "home": full_time.get("home"),
            "away": full_time.get("away"),
            "result": _score_result(full_time.get("home"), full_time.get("away")),
        },
    }


# ---------------------------------------------------------------------------
# Web scraping fetcher (free, no API key needed)
# ---------------------------------------------------------------------------

class _WorldCupResultsParser(HTMLParser):
    """Parse HTML from worldcuplocaltime.com to extract match results."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_text = ""
        self._cells: list[str] = []
        self._current_date = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if tag == "table" and "results" in cls.lower():
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._cells = []
        elif self._in_row and tag in ("td", "th"):
            self._in_cell = True
            self._cell_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif self._in_table and tag == "tr" and self._in_row:
            self._in_row = False
            self._try_parse_row()
        elif self._in_cell and tag in ("td", "th"):
            self._in_cell = False
            self._cells.append(self._cell_text.strip())

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text += data

    def _try_parse_row(self) -> None:
        # Expected: date | home_team | score | away_team | stadium (5+ cols)
        if len(self._cells) < 4:
            return

        # Try to detect date pattern in first cell
        first = self._cells[0]
        date_match = re.search(r"(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})", first)
        if date_match:
            self._current_date = date_match.group(1)

        if not self._current_date:
            return

        # Find score cell: looks like "X : Y" or "X-Y"
        score_val = ""
        home_team = ""
        away_team = ""
        score_idx = -1

        for i, cell in enumerate(self._cells):
            m = re.match(r"(\d+)\s*[:\-]\s*(\d+)", cell.replace("\u2009", "").strip())
            if m:
                score_idx = i
                score_val = f"{m.group(1)}-{m.group(2)}"
                break

        if score_idx < 0:
            return

        # Home is before score, away is after
        for i in range(score_idx):
            t = self._cells[i].strip()
            if t and not re.match(r"\d", t) and len(t) > 1:
                home_team = t

        for i in range(score_idx + 1, len(self._cells)):
            t = self._cells[i].strip()
            if t and not re.match(r"\d", t) and len(t) > 1 and not any(
                kw in t.lower() for kw in ["stadium", "arena", "park", "field", "venue"]
            ):
                away_team = t
                break

        if home_team and away_team and score_val:
            self.results.append({
                "home_team": home_team,
                "away_team": away_team,
                "home_score": int(score_val.split("-")[0]),
                "away_score": int(score_val.split("-")[1]),
                "status": "FT",
                "date": self._current_date,
                "source": "web_scrape",
            })


def _fetch_url(url: str, timeout: int = 15) -> str:
    """Fetch URL content as string."""
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_web_results(date_from: str, date_to: str) -> list[dict]:
    """Fetch World Cup 2026 results from public websites.

    This is a free method that requires no API keys.
    It scrapes worldcuplocaltime.com which has a clean results table.
    """
    all_results: list[dict] = []

    # Source 1: worldcuplocaltime.com (has structured results table)
    urls_to_try = [
        "https://worldcuplocaltime.com/fifa-world-cup-2026-results/",
        "https://www.fifa.com/tournaments/mens/worldcup/canadamexicousa2026/"
        "articles/match-schedule-fixtures-results-teams-stadiums",
    ]

    for url in urls_to_try:
        try:
            print(f"  Trying {url}...", file=sys.stderr)
            html = _fetch_url(url)
            parser = _WorldCupResultsParser()
            parser.feed(html)
            if parser.results:
                print(f"  Got {len(parser.results)} raw results", file=sys.stderr)
                # Filter by date range
                for r in parser.results:
                    d = r["date"]
                    # Normalize various date formats to YYYY-MM-DD
                    try:
                        dt = datetime.strptime(d, "%Y-%m-%d")
                        d_iso = d
                    except ValueError:
                        try:
                            dt = datetime.strptime(d, "%d %b %Y")
                            d_iso = dt.strftime("%Y-%m-%d")
                        except ValueError:
                            # Skip unparseable dates
                            continue
                    if date_from <= d_iso <= date_to:
                        r["date"] = d_iso
                        all_results.append(r)
                print(f"  Filtered to {len(all_results)} results in range", file=sys.stderr)
                if all_results:
                    break
        except urllib.error.URLError as e:
            print(f"  Failed: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  Error parsing {url}: {e}", file=sys.stderr)

    return all_results


def parse_inline_results(pairs: list[tuple[str, str, str]]) -> list[dict]:
    """Parse inline 'home away score' tuples into result dicts."""
    results = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for home, away, score_str in pairs:
        m = re.match(r"(\d+)\s*[-:]\s*(\d+)", score_str.strip())
        if m:
            results.append({
                "home_team": home,
                "away_team": away,
                "home_score": int(m.group(1)),
                "away_score": int(m.group(2)),
                "status": "FT",
                "date": now,
                "source": "inline_manual",
            })
    return results


# ---------------------------------------------------------------------------
# football-data.org fetcher
# ---------------------------------------------------------------------------

FD_BASE = "https://api.football-data.org/v4"


def fetch_footballdata_results(
    api_key: str,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """Fetch completed match results from football-data.org API."""
    # WC competition code = "WC"
    url = (
        f"{FD_BASE}/competitions/WC/matches"
        f"?dateFrom={date_from}&dateTo={date_to}"
    )
    headers = {"X-Auth-Token": api_key}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"football-data.org request failed: {e}", file=sys.stderr)
        return []
    except json.JSONDecodeError:
        print("football-data.org returned invalid JSON", file=sys.stderr)
        return []

    results = []
    for match in data.get("matches", []):
        status = match.get("status", "")
        if status not in ("FINISHED", "AWARDED"):
            continue

        home = match.get("homeTeam", {}).get("shortName") or match.get("homeTeam", {}).get("name", "")
        away = match.get("awayTeam", {}).get("shortName") or match.get("awayTeam", {}).get("name", "")
        score = match.get("score", {})
        ft = score.get("fullTime", {})
        duration = str(score.get("duration") or match.get("duration") or "").upper()
        winner_value = match.get("score", {}).get("winner") or match.get("winner")
        knockout_result = _build_knockout_result_payload(score, winner_value)

        home_goals = ft.get("home")
        away_goals = ft.get("away")
        if home_goals is not None and away_goals is not None:
            results.append({
                "home_team": home,
                "away_team": away,
                "home_score": home_goals,
                "away_score": away_goals,
                "status": "FT",
                "date": match.get("utcDate", "")[:10],
                "fixture_id": match.get("id"),
                "source": "football-data.org",
                "duration": duration,
                "knockout_result": knockout_result,
            })

    return results


# ---------------------------------------------------------------------------
# Match ledger update
# ---------------------------------------------------------------------------

def _match_result_to_ledger(match: dict, result: dict) -> bool:
    """Try to apply a result to a match in the ledger. Returns True if applied."""
    home_hit = _result_matches_fixture(match, result, swapped=False)
    away_hit = home_hit

    if not (home_hit and away_hit):
        home_hit2 = _result_matches_fixture(match, result, swapped=True)
        away_hit2 = home_hit2
        if home_hit2 and away_hit2:
            result_home = result.get("home_team", "")
            result_away = result.get("away_team", "")
            # Home/away swapped in the API - swap scores too
            match["final_score"] = {
                "home": result["away_score"],
                "away": result["home_score"],
                "status": "final",
                "recorded_at": iso_now(),
                "source_refs": [{
                    "source_id": result.get("source", "unknown"),
                    "tier": "T2",
                    "fixture_id": result.get("fixture_id"),
                    "observed_result": f"{result_away} {result['away_score']}-{result['home_score']} {result_home}",
                }],
            }
            _apply_knockout_result_to_match(match, {**result, "_swapped": True})
            match["status"] = "final"
            return True
        return False

    result_home = result.get("home_team", "")
    result_away = result.get("away_team", "")
    match["final_score"] = {
        "home": result["home_score"],
        "away": result["away_score"],
        "status": "final",
        "recorded_at": iso_now(),
        "source_refs": [{
            "source_id": result.get("source", "unknown"),
            "tier": "T2",
            "fixture_id": result.get("fixture_id"),
            "observed_result": f"{result_home} {result['home_score']}-{result['away_score']} {result_away}",
        }],
    }
    _apply_knockout_result_to_match(match, result)
    match["status"] = "final"
    return True


def apply_results_to_ledger(
    *,
    root: Path,
    edition: str,
    results: list[dict],
    source: str = "",
) -> dict:
    """Apply fetched results to match-ledger.json and SQLite DB."""
    ledger = load_match_ledger(root, edition)
    matches = canonical_matches(ledger.get("matches", []))

    applied = []
    skipped = []
    not_found = []

    for result in results:
        matched = False
        for match in matches:
            # Skip if already has final_score (but keep looking for this result!)
            fs = match.get("final_score")
            if fs and isinstance(fs, dict) and fs.get("home") is not None:
                # Only skip if THIS match would have been the target
                is_target = _result_matches_fixture(match, result, swapped=False)
                is_target_swapped = _result_matches_fixture(match, result, swapped=True)
                if is_target or is_target_swapped:
                    updated_knockout = False
                    if result.get("knockout_result") and not match.get("knockout_result"):
                        updated_knockout = _apply_knockout_result_to_match(
                            match,
                            {**result, "_swapped": is_target_swapped},
                        )
                    skipped.append({
                        "match_id": match["match_id"],
                        "reason": "already_has_final_score_knockout_backfilled" if updated_knockout else "already_has_final_score",
                        "existing": f"{fs['home']}-{fs['away']}",
                    })
                    if updated_knockout:
                        applied.append({
                            "match_id": match["match_id"],
                            "home": match["home_team"].get("name", ""),
                            "away": match["away_team"].get("name", ""),
                            "score": f"{fs['home']}-{fs['away']}",
                            "source": result.get("source", source),
                            "update": "knockout_result",
                        })
                    matched = True
                    break
                else:
                    continue  # Not our target match, keep looking

            if _match_result_to_ledger(match, result):
                applied.append({
                    "match_id": match["match_id"],
                    "home": match["home_team"].get("name", ""),
                    "away": match["away_team"].get("name", ""),
                    "score": f"{result['home_score']}-{result['away_score']}",
                    "source": result.get("source", source),
                })
                matched = True
                break

        if not matched:
            not_found.append({
                "home_team": result.get("home_team"),
                "away_team": result.get("away_team"),
                "score": f"{result.get('home_score')}-{result.get('away_score')}",
            })

    # Write updated ledger
    if applied:
        ledger_path = edition_data_root(root, edition) / "match-ledger.json"
        ledger["generated_at"] = iso_now()
        write_json(ledger_path, ledger)

        # Also update SQLite
        try:
            from worldcup_db import get_db_connection, init_database, save_match, save_team_form
            db_path = worldcup_db_path(root, edition)
            init_database(db_path)
            conn = get_db_connection(db_path)
            try:
                with conn:
                    for m in ledger["matches"]:
                        save_match(conn, m)

                    # Write team_form for newly completed matches
                    for entry in applied:
                        mid = entry["match_id"]
                        hs, as_ = entry["score"].split("-")
                        hs, as_ = int(hs), int(as_)
                        # Find the match in ledger to get team_ids
                        for m in ledger["matches"]:
                            if m.get("match_id") == mid:
                                home_team = m.get("home_team", {})
                                away_team = m.get("away_team", {})
                                home_id = home_team.get("team_id", "") if isinstance(home_team, dict) else ""
                                away_id = away_team.get("team_id", "") if isinstance(away_team, dict) else ""
                                match_date = m.get("date", "")
                                if home_id:
                                    h_result = "W" if hs > as_ else ("D" if hs == as_ else "L")
                                    save_team_form(conn, {
                                        "team_id": home_id.lower(),
                                        "match_id": mid,
                                        "match_date": match_date,
                                        "opponent_id": away_id.lower() if away_id else None,
                                        "goals_for": hs,
                                        "goals_against": as_,
                                        "result": h_result,
                                        "competition": f"world_cup_{edition}",
                                        "is_home": 1,
                                    })
                                if away_id:
                                    a_result = "W" if as_ > hs else ("D" if hs == as_ else "L")
                                    save_team_form(conn, {
                                        "team_id": away_id.lower(),
                                        "match_id": mid,
                                        "match_date": match_date,
                                        "opponent_id": home_id.lower() if home_id else None,
                                        "goals_for": as_,
                                        "goals_against": hs,
                                        "result": a_result,
                                        "competition": f"world_cup_{edition}",
                                        "is_home": 0,
                                    })
                                break
            finally:
                conn.close()
        except Exception as e:
            print(f"Warning: SQLite update failed: {e}", file=sys.stderr)

        # Bump public version
        bump_public_version(root, edition, fixture_update=True)

    return {
        "status": "results_applied",
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "not_found_count": len(not_found),
        "applied": applied,
        "skipped": skipped,
        "not_found": not_found,
    }


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------

def check_status(*, root: Path, edition: str, date_from: str = "", date_to: str = "") -> dict:
    """Show which matches have/lack results."""
    ledger = load_match_ledger(root, edition)
    matches = canonical_matches(ledger.get("matches", []))

    with_results = []
    without_results = []

    for m in matches:
        kickoff = m.get("kickoff_at", "")
        kickoff_local = beijing_datetime(parse_datetime(str(kickoff)))
        local_date = kickoff_local.date().isoformat() if kickoff_local else str(kickoff)[:10]
        if date_from and local_date < date_from:
            continue
        if date_to and local_date > date_to:
            continue

        fs = m.get("final_score")
        has_result = fs and isinstance(fs, dict) and fs.get("home") is not None
        entry = {
            "match_id": m["match_id"],
            "kickoff": kickoff,
            "local_date": local_date,
            "home": m.get("home_team", {}).get("name", ""),
            "away": m.get("away_team", {}).get("name", ""),
        }
        if has_result:
            entry["score"] = f"{fs['home']}-{fs['away']}"
            with_results.append(entry)
        else:
            # Check if match is in the past (should have result)
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry["should_have_result"] = kickoff < now_iso and kickoff > "2026-06-01"
            without_results.append(entry)

    return {
        "with_results": len(with_results),
        "without_results": len(without_results),
        "completed": with_results,
        "pending": without_results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    # web: free web scraping (no API key needed!)
    web = sub.add_parser("web", help="Fetch results from public websites (free, no key)")
    web.add_argument("--edition", required=True)
    web.add_argument("--from", dest="date_from", required=True, help="Start date YYYY-MM-DD")
    web.add_argument("--to", dest="date_to", required=True, help="End date YYYY-MM-DD")
    web.add_argument("--root", default=".")

    # fetch: auto-fetch (tries all sources in order)
    fetch = sub.add_parser("fetch", help="Auto-fetch from any available source")
    fetch.add_argument("--edition", required=True)
    fetch.add_argument("--from", dest="date_from", required=True, help="Start date YYYY-MM-DD")
    fetch.add_argument("--to", dest="date_to", required=True, help="End date YYYY-MM-DD")
    fetch.add_argument("--root", default=".")
    fetch.add_argument(
        "--source",
        choices=["web", "football-data", "auto"],
        default="auto",
        help="Data source to use (default: auto, tries web first then football-data.org)",
    )

    # inline: quick single-match input
    inline = sub.add_parser("inline", help="Quick inline result entry")
    inline.add_argument("--edition", required=True)
    inline.add_argument("--home", required=True)
    inline.add_argument("--away", required=True)
    inline.add_argument("--score", required=True, help="Score as 'H-A' e.g. '2-0'")
    inline.add_argument("--date", default="", help="Match date (default: today)")
    inline.add_argument("--root", default=".")

    # apply: manually apply results from JSON
    apply = sub.add_parser("apply", help="Apply match results from a JSON file")
    apply.add_argument("--edition", required=True)
    apply.add_argument("--results-json", required=True, help="Path to results JSON file")
    apply.add_argument("--root", default=".")

    # status: show which matches lack results
    status = sub.add_parser("status", help="Show which matches have/lack results")
    status.add_argument("--edition", required=True)
    status.add_argument("--from", dest="date_from", default="")
    status.add_argument("--to", dest="date_to", default="")
    status.add_argument("--root", default=".")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    edition = args.edition

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.command == "status":
        result = check_status(
            root=root,
            edition=edition,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "web":
        # Free web scraping - no API key needed
        date_from = args.date_from
        date_to = args.date_to
        print(f"Fetching from public web: {date_from} to {date_to}...", file=sys.stderr)
        results = fetch_web_results(date_from, date_to)
        if not results:
            print("No results found from web sources. Try --from/--to range or use inline.", file=sys.stderr)
            return 1
        result = apply_results_to_ledger(
            root=root, edition=edition, results=results, source="web_scrape",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "inline":
        # Quick single-match input
        results = parse_inline_results([(args.home, args.away, args.score)])
        if args.date:
            for r in results:
                r["date"] = args.date
        result = apply_results_to_ledger(
            root=root, edition=edition, results=results, source="inline_manual",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply":
        results = load_json(Path(args.results_json), [])
        if not isinstance(results, list):
            results = results.get("results", results.get("matches", []))
        result = apply_results_to_ledger(
            root=root, edition=edition, results=results, source="manual_json",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "fetch":
        date_from = args.date_from
        date_to = args.date_to
        source = args.source
        all_results: list[dict] = []

        # Priority 1: Web scraping (free)
        if source in ("web", "auto"):
            try:
                print(f"Trying public web (free): {date_from} to {date_to}...", file=sys.stderr)
                web_results = fetch_web_results(date_from, date_to)
                if web_results:
                    all_results.extend(web_results)
                    print(f"  Got {len(web_results)} results from web", file=sys.stderr)
                else:
                    print("  No results from web", file=sys.stderr)
            except Exception as e:
                print(f"  Web scrape failed: {e}", file=sys.stderr)

        # Priority 2: football-data.org
        if not all_results and source in ("football-data", "auto"):
            fd_key = os.environ.get("FOOTBALL_DATA_API_KEY")
            if fd_key:
                print(f"Fetching from football-data.org...", file=sys.stderr)
                fd_results = fetch_footballdata_results(fd_key, date_from, date_to)
                if fd_results:
                    all_results.extend(fd_results)
                    print(f"  Got {len(fd_results)} results from football-data.org", file=sys.stderr)
                else:
                    print("  No results from football-data.org", file=sys.stderr)
            else:
                print("FOOTBALL_DATA_API_KEY not set in .env or environment", file=sys.stderr)

        if not all_results:
            print(
                "No results fetched.\n"
                "  Try: python fetch_match_results.py inline --home Brazil --away Morocco --score 1-1\n"
                "  Or: python fetch_match_results.py apply --results-json results.json",
                file=sys.stderr,
            )
            return 1

        result = apply_results_to_ledger(root=root, edition=edition, results=all_results)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
