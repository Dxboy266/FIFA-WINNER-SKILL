#!/usr/bin/env python3
"""Injury and suspension data fetcher - Web scraping version.

Uses free web sources (no API key required):
  1. ESPN RSS news + NLP extraction (primary - multilingual)
  2. ESPN roster API (checks for non-active players)

Usage:
    python fetch_injuries_api_football.py --edition 2026 --date 2026-06-15 --root .
    python fetch_injuries_api_football.py --edition 2026 --date 2026-06-15 --root . --teams BRA,ARG
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Load .env from project root (for local development)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

ESPN_RSS_URL = "https://www.espn.com/espn/rss/soccer/news"

# ESPN team ID mapping: FIFA code -> ESPN team ID (WC 2026)
ESPN_TEAM_IDS: dict[str, int] = {
    "ALG": 624, "ARG": 202, "AUS": 628, "AUT": 474, "BEL": 459,
    "BIH": 452, "BRA": 205, "CAN": 206, "CPV": 2597, "COL": 208,
    "COD": 2850, "CRO": 477, "CUW": 11678, "CZE": 450, "ECU": 209,
    "EGY": 2620, "ENG": 448, "FRA": 478, "GER": 481, "GHA": 4469,
    "HAI": 2654, "IRN": 469, "IRQ": 4375, "CIV": 4789, "JPN": 627,
    "JOR": 2917, "MEX": 203, "MAR": 2869, "NED": 449, "NZL": 2666,
    "NOR": 464, "PAN": 2659, "PAR": 210, "POR": 482, "QAT": 4398,
    "KSA": 655, "SCO": 580, "SEN": 654, "RSA": 467, "KOR": 451,
    "ESP": 164, "SWE": 466, "SUI": 475, "TUN": 659, "TUR": 465,
    "USA": 660, "URU": 212, "UZB": 2570,
}

TEAM_NAMES: dict[str, str] = {
    "ALG": "Algeria", "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria",
    "BEL": "Belgium", "BIH": "Bosnia-Herzegovina", "BRA": "Brazil",
    "CAN": "Canada", "CPV": "Cape Verde", "COL": "Colombia",
    "COD": "Congo DR", "CRO": "Croatia", "CUW": "Curacao", "CZE": "Czechia",
    "ECU": "Ecuador", "EGY": "Egypt", "ENG": "England", "FRA": "France",
    "GER": "Germany", "GHA": "Ghana", "HAI": "Haiti", "IRN": "Iran",
    "IRQ": "Iraq", "CIV": "Ivory Coast", "JPN": "Japan", "JOR": "Jordan",
    "MEX": "Mexico", "MAR": "Morocco", "NED": "Netherlands", "NZL": "New Zealand",
    "NOR": "Norway", "PAN": "Panama", "PAR": "Paraguay", "POR": "Portugal",
    "QAT": "Qatar", "KSA": "Saudi Arabia", "SCO": "Scotland", "SEN": "Senegal",
    "RSA": "South Africa", "KOR": "South Korea", "ESP": "Spain", "SWE": "Sweden",
    "SUI": "Switzerland", "TUN": "Tunisia", "TUR": "Turkey", "USA": "United States",
    "URU": "Uruguay", "UZB": "Uzbekistan",
}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
}

# Country name -> FIFA code for team guessing in news text
_COUNTRY_TO_CODE: dict[str, str] = {}
for _code, _name in TEAM_NAMES.items():
    _COUNTRY_TO_CODE[_name.lower()] = _code
    _COUNTRY_TO_CODE[_code.lower()] = _code
_COUNTRY_TO_CODE.update({
    "brazil": "BRA", "argentina": "ARG", "france": "FRA", "germany": "GER",
    "spain": "ESP", "england": "ENG", "portugal": "POR", "belgium": "BEL",
    "netherlands": "NED", "holland": "NED", "uruguay": "URU", "mexico": "MEX",
    "united states": "USA", "canada": "CAN", "south korea": "KOR", "korea": "KOR",
    "japan": "JPN", "sweden": "SWE", "tunisia": "TUN", "egypt": "EGY",
    "iran": "IRN", "new zealand": "NZL", "cape verde": "CPV",
    "saudi arabia": "KSA", "ivory coast": "CIV", "south africa": "RSA",
    "czech republic": "CZE", "czechia": "CZE", "turkey": "TUR",
    "ecuador": "ECU", "paraguay": "PAR", "australia": "AUS", "colombia": "COL",
    "senegal": "SEN", "ghana": "GHA", "morocco": "MAR", "croatia": "CRO",
    "switzerland": "SUI", "algeria": "ALG", "norway": "NOR", "scotland": "SCO",
    "brazilian": "BRA", "argentine": "ARG", "french": "FRA", "german": "GER",
    "spanish": "ESP", "english": "ENG", "portuguese": "POR", "belgian": "BEL",
    "dutch": "NED", "uruguayan": "URU", "mexican": "MEX", "japanese": "JPN",
    "korean": "KOR", "swedish": "SWE", "egyptian": "EGY",
})


# ---------------------------------------------------------------------------
# Severity assessment
# ---------------------------------------------------------------------------

def assess_severity(text: str) -> str:
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["fracture", "rupture", "torn", "surgery", "acl",
                                         "mcl", "cruciate", "achilles", "broken"]):
        return "high"
    if any(kw in text_lower for kw in ["strain", "sprain", "contusion", "knock",
                                         "muscle", "hamstring", "ligament"]):
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Source 1: ESPN RSS + NLP injury extraction
# ---------------------------------------------------------------------------

_INJURY_PATTERNS = [
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:is|has been|will be)\s+(?:injured|ruled out|sidelined|out)", re.I),
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:suffers|sustains|picks up)\s+(?:an?\s+)?(?:injury|knock|blow)", re.I),
    re.compile(r"injury\s+(?:to|concern\s+for)\s+(\w+(?:\s+\w+){1,2})", re.I),
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:will miss|misses|missed)\s+", re.I),
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:doubtful|questionable|uncertain)\s+for", re.I),
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:lesionado|fuera|baja)", re.I),
    re.compile(r"lesi[oó]n\s+de\s+(\w+(?:\s+\w+){1,2})", re.I),
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:lesionado|machucado|fora)", re.I),
    re.compile(r"(\w{2,6})\s*(?:因伤|受伤|伤缺|缺席|缺阵|无缘|退出)", re.I),
]

_SUSPENSION_PATTERNS = [
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:suspended|banned|serving\s+a\s+ban)", re.I),
    re.compile(r"(\w+(?:\s+\w+){1,2})\s+(?:red\s+card|sent\s+off)", re.I),
]

_BLOCKED_TOKENS = {
    "is", "has", "will", "the", "and", "for", "with", "from", "but", "not",
    "this", "that", "their", "team", "match", "game", "first", "second",
    "world", "cup", "group", "round", "stage", "final", "ruled", "suffers",
    "sustains", "picks", "injury", "knock", "blow",
}


def _clean_name(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", raw).strip().title()
    tokens = [t for t in name.split() if t.lower() not in _BLOCKED_TOKENS]
    return " ".join(tokens) if len(tokens) >= 2 else None


def _guess_team(text: str) -> str:
    text_lower = text.lower()
    for country, code in _COUNTRY_TO_CODE.items():
        if country in text_lower:
            return code
    return "unknown"


def extract_injuries_from_headlines(headlines: list[dict]) -> tuple[list[dict], list[dict]]:
    injuries: list[dict] = []
    suspensions: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for item in headlines:
        headline = item.get("headline", "")
        detail = item.get("detail", "")
        text = headline + " " + detail
        team_code = item.get("team_code") or _guess_team(text)

        for pattern in _INJURY_PATTERNS:
            for m in pattern.finditer(text):
                player_name = _clean_name(m.group(1))
                if not player_name:
                    continue
                key = (player_name.lower(), "injury")
                if key in seen:
                    continue
                seen.add(key)
                injuries.append({
                    "player_name": player_name,
                    "type": "injury",
                    "reason": headline[:120],
                    "severity": assess_severity(text),
                    "status": "out" if any(kw in text.lower() for kw in ["ruled out", "will miss", "sidelined"]) else
                              "doubtful" if any(kw in text.lower() for kw in ["questionable", "doubtful"]) else "unknown",
                    "source": "news_nlp",
                    "team_code": team_code,
                })

        for pattern in _SUSPENSION_PATTERNS:
            for m in pattern.finditer(text):
                player_name = _clean_name(m.group(1))
                if not player_name:
                    continue
                key = (player_name.lower(), "suspension")
                if key in seen:
                    continue
                seen.add(key)
                suspensions.append({
                    "player_name": player_name,
                    "reason": headline[:120],
                    "source": "news_nlp",
                    "team_code": team_code,
                })

    return injuries, suspensions


def fetch_espn_news() -> list[dict]:
    news_items: list[dict] = []
    try:
        req = urllib.request.Request(ESPN_RSS_URL, headers=REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()
        if not xml_data:
            return news_items
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item"):
            news_items.append({
                "headline": item.findtext("title") or "",
                "detail": item.findtext("description") or "",
                "url": item.findtext("link") or "",
            })
    except Exception as e:
        print(f"  Warning: ESPN RSS fetch failed: {e}", file=sys.stderr)
    return news_items


# ---------------------------------------------------------------------------
# Source 2: ESPN Roster API
# ---------------------------------------------------------------------------

def fetch_espn_roster(team_code: str) -> list[dict]:
    espn_id = ESPN_TEAM_IDS.get(team_code)
    if not espn_id:
        return []
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/teams/{espn_id}/roster"
    try:
        req = urllib.request.Request(url, headers=REQUEST_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    non_active: list[dict] = []
    for entry in data.get("athletes", []):
        ath = entry if "displayName" in entry else entry.get("athlete", entry)
        status = ath.get("status", {})
        if status.get("type", "active") != "active":
            non_active.append({
                "player_name": ath.get("displayName", "Unknown"),
                "type": "roster_status",
                "reason": status.get("name", status.get("type", "unknown")),
                "severity": "medium",
                "status": "out" if status.get("type") in ("out", "injured") else "doubtful",
                "source": "espn_roster",
                "team_code": team_code,
            })
    return non_active


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

class WebInjuryFetcher:
    """Fetch injury data from ESPN news NLP + roster API. No API key required."""

    def __init__(self, root_path: str = "."):
        self.root_path = Path(root_path).resolve()

    def fetch_all_injuries(self, edition: str, date: str, team_codes: list[str] | None = None) -> dict:
        if team_codes is None:
            team_codes = list(ESPN_TEAM_IDS.keys())

        all_injuries: dict[str, dict] = {}
        now_str = datetime.now(timezone.utc).isoformat() + "Z"

        # --- Source 1: ESPN RSS + NLP ---
        print("  [Source 1] ESPN RSS + NLP extraction...")
        news = fetch_espn_news()
        if news:
            relevant = []
            for item in news:
                text = (item.get("headline", "") + " " + item.get("detail", "")).lower()
                if any(name in text for name in _COUNTRY_TO_CODE):
                    item["team_code"] = _guess_team(text)
                    relevant.append(item)
            injuries, suspensions = extract_injuries_from_headlines(relevant)
            print(f"    {len(injuries)} injuries, {len(suspensions)} suspensions from {len(relevant)} articles")
            for inj in injuries:
                tc = inj.pop("team_code", "unknown").upper()
                all_injuries.setdefault(tc, {"injuries": [], "suspensions": []})
                all_injuries[tc]["injuries"].append(inj)
            for sus in suspensions:
                tc = sus.pop("team_code", "unknown").upper()
                all_injuries.setdefault(tc, {"injuries": [], "suspensions": []})
                all_injuries[tc]["suspensions"].append(sus)
        else:
            print("    No news fetched (ESPN RSS may be rate-limited)")

        # --- Source 2: ESPN Roster ---
        check_teams = team_codes[:15]
        print(f"  [Source 2] ESPN roster check ({len(check_teams)} teams)...")
        roster_hits = 0
        for tc in check_teams:
            issues = fetch_espn_roster(tc)
            if issues:
                all_injuries.setdefault(tc, {"injuries": [], "suspensions": []})
                all_injuries[tc]["injuries"].extend(issues)
                roster_hits += len(issues)
        print(f"    {roster_hits} non-active players found")

        # Build result
        teams_data: dict[str, dict] = {}
        total_inj = 0
        total_sus = 0
        teams_with = 0
        for tc, data in all_injuries.items():
            cnt = len(data["injuries"]) + len(data["suspensions"])
            if cnt > 0:
                teams_with += 1
                total_inj += len(data["injuries"])
                total_sus += len(data["suspensions"])
            teams_data[tc] = {
                "team_code": tc,
                "team_name": TEAM_NAMES.get(tc, tc),
                "injuries": data["injuries"],
                "suspensions": data["suspensions"],
                "total_count": cnt,
                "fetched_at": now_str,
            }

        return {
            "edition": edition,
            "date": date,
            "teams": teams_data,
            "summary": {
                "total_teams": len(team_codes),
                "teams_with_injuries": teams_with,
                "total_injuries": total_inj,
                "total_suspensions": total_sus,
            },
        }

    def save_to_daily_evidence(self, edition: str, date: str, injuries_data: dict) -> None:
        evidence_dir = self.root_path / "wiki" / "public" / edition / "daily-evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_file = evidence_dir / f"{date}.json"

        if evidence_file.exists():
            with open(evidence_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        else:
            existing = {"date": date, "edition": edition, "data_sources": []}

        existing["injuries"] = injuries_data
        existing.setdefault("data_sources", []).append({
            "type": "injuries",
            "source": "web (espn_rss_nlp + espn_roster)",
            "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
        })

        with open(evidence_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

        # Write to SQLite
        try:
            from worldcup_core import worldcup_db_path
            from worldcup_db import get_db_connection, init_database, save_injury

            db_path = worldcup_db_path(self.root_path, edition)
            init_database(db_path)
            conn = get_db_connection(db_path)
            try:
                with conn:
                    for team_code, td in injuries_data.get("teams", {}).items():
                        for inj in td.get("injuries", []):
                            iid = f"web-{team_code}-{inj.get('player_name','unknown')}-{date}".lower().replace(" ", "-")
                            save_injury(conn, {
                                "injury_id": iid, "team_id": team_code.lower(),
                                "player_name": inj.get("player_name"), "player_id": None,
                                "injury_type": inj.get("type", "injury"),
                                "reason": inj.get("reason", ""),
                                "severity": inj.get("severity", "low"),
                                "status": inj.get("status", "unknown"),
                                "start_date": inj.get("start_date"),
                                "expected_end_date": inj.get("end_date"),
                                "source": inj.get("source", "web_scrape"),
                                "source_url": None,
                                "confidence": "medium",
                                "recorded_at": datetime.now(timezone.utc).isoformat(),
                            })
                        for sus in td.get("suspensions", []):
                            iid = f"web-{team_code}-{sus.get('player_name','unknown')}-sus-{date}".lower().replace(" ", "-")
                            save_injury(conn, {
                                "injury_id": iid, "team_id": team_code.lower(),
                                "player_name": sus.get("player_name"), "player_id": None,
                                "injury_type": "suspension",
                                "reason": sus.get("reason", ""),
                                "severity": "medium", "status": "active",
                                "start_date": date, "expected_end_date": None,
                                "source": sus.get("source", "web_scrape"),
                                "source_url": None, "confidence": "medium",
                                "recorded_at": datetime.now(timezone.utc).isoformat(),
                            })
            finally:
                conn.close()
        except Exception as e:
            print(f"Warning: SQLite injury write failed: {e}", file=sys.stderr)

        print(f"\nSaved to: {evidence_file}")
        s = injuries_data["summary"]
        print(f"  - Teams with injuries: {s['teams_with_injuries']}")
        print(f"  - Total injuries: {s['total_injuries']}")
        print(f"  - Total suspensions: {s['total_suspensions']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch injury data via web scraping (no API key)")
    parser.add_argument("--edition", required=True, help="World Cup edition (e.g., 2026)")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--teams", help="Comma-separated team codes (e.g., BRA,ARG,FRA)")
    parser.add_argument("--root", default=".", help="Project root path")
    args = parser.parse_args()

    team_codes = [t.strip().upper() for t in args.teams.split(",")] if args.teams else None
    fetcher = WebInjuryFetcher(root_path=args.root)
    data = fetcher.fetch_all_injuries(args.edition, args.date, team_codes)
    fetcher.save_to_daily_evidence(args.edition, args.date, data)


if __name__ == "__main__":
    main()
