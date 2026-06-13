#!/usr/bin/env python3
"""Fetch historical World Cup data from OpenFootball via raw.githubusercontent.com.

Uses raw content URLs instead of the GitHub API to avoid rate-limit issues.
Repository: https://github.com/openfootball/worldcup
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    edition_data_root,
    iso_now,
    load_json,
    raw_edition_root,
    write_json,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_RAW_URL = "https://raw.githubusercontent.com/openfootball/worldcup/master"
USER_AGENT = "fifa-winner-skill/1.0"

EDITIONS: list[dict] = [
    {"year": 1930, "host": "uruguay", "slug": "1930--uruguay"},
    {"year": 1934, "host": "italy", "slug": "1934--italy"},
    {"year": 1938, "host": "france", "slug": "1938--france"},
    {"year": 1950, "host": "brazil", "slug": "1950--brazil"},
    {"year": 1954, "host": "switzerland", "slug": "1954--switzerland"},
    {"year": 1958, "host": "sweden", "slug": "1958--sweden"},
    {"year": 1962, "host": "chile", "slug": "1962--chile"},
    {"year": 1966, "host": "england", "slug": "1966--england"},
    {"year": 1970, "host": "mexico", "slug": "1970--mexico"},
    {"year": 1974, "host": "west-germany", "slug": "1974--west-germany"},
    {"year": 1978, "host": "argentina", "slug": "1978--argentina"},
    {"year": 1982, "host": "spain", "slug": "1982--spain"},
    {"year": 1986, "host": "mexico", "slug": "1986--mexico"},
    {"year": 1990, "host": "italy", "slug": "1990--italy"},
    {"year": 1994, "host": "usa", "slug": "1994--usa"},
    {"year": 1998, "host": "france", "slug": "1998--france"},
    {"year": 2002, "host": "south-korea-n-japan", "slug": "2002--south-korea-n-japan"},
    {"year": 2006, "host": "germany", "slug": "2006--germany"},
    {"year": 2010, "host": "south-africa", "slug": "2010--south-africa"},
    {"year": 2014, "host": "brazil", "slug": "2014--brazil"},
    {"year": 2018, "host": "russia", "slug": "2018--russia"},
    {"year": 2022, "host": "qatar", "slug": "2022--qatar"},
]

# Maps OpenFootball team names (normalized key) -> canonical 2026 roster name.
# Historical names that no longer exist are mapped to their modern successor
# so that wc_titles / wc_appearances aggregate correctly.
# Only names that differ between OpenFootball and the roster need an entry;
# identical names match automatically via _normalize_key.
TEAM_NAME_ALIASES: dict[str, str] = {
    "west germany": "Germany",
    "east germany": "Germany",
    "germany fr": "Germany",
    "germany dr": "Germany",
    "ussr": "Russia",
    "soviet union": "Russia",
    "yugoslavia": "Serbia",
    "fr yugoslavia": "Serbia",
    "serbia and montenegro": "Serbia",
    "czechoslovakia": "Czechia",
    "czech republic": "Czechia",
    "south korea": "Korea Republic",
    "korea": "Korea Republic",
    "korea rep": "Korea Republic",
    "usa": "USA",
    "united states": "USA",
    "iran": "IR Iran",
    "ir iran": "IR Iran",
    "ivory coast": "Cote dIvoire",
    "dr congo": "Congo DR",
    "zaire": "Congo DR",
    "democratic republic of the congo": "Congo DR",
    "bosnia-herzegovina": "Bosnia And Herzegovina",
    "turkey": "Turkiye",
    "chinese pr": "China",
    "china pr": "China",
    "north korea": "North Korea",
    "korea dpr": "North Korea",
    "holland": "Netherlands",
}

# Stage ranking for "best result" comparison (higher = better).
_STAGE_RANK: dict[str, int] = {
    "group_stage": 1,
    "round_of_16": 2,
    "quarterfinal": 3,
    "fourth": 4,
    "third": 5,
    "semi_final": 4,   # same tier as fourth; refined when 3rd-place exists
    "runner_up": 6,
    "winner": 7,
}

_RESULT_LABEL_ORDER = [
    "winner",
    "runner_up",
    "third",
    "fourth",
    "semi_final",
    "quarterfinal",
    "round_of_16",
    "group_stage",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    """Remove diacritical marks for accent-insensitive matching."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _normalize_key(name: str) -> str:
    """Produce a canonical lookup key: lowercase, ASCII, no special chars.

    This strips accents, removes apostrophes, and collapses whitespace so
    that ``"Cote d'Ivoire"``, ``"C\u00f4te D'Ivoire"``, and ``"Cote DIvoire"``
    all produce the same key.
    """
    text = _strip_accents(name).lower().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_team(raw_name: str) -> str:
    """Map an OpenFootball team name to the canonical 2026 roster name."""
    name = raw_name.strip()
    if not name:
        return name
    key = _normalize_key(name)
    # Try direct alias lookup.
    if key in TEAM_NAME_ALIASES:
        return TEAM_NAME_ALIASES[key]
    # Try without common suffixes.
    for suffix in (" Rep.", " Rep"):
        stripped = name.replace(suffix, "").strip()
        skey = _normalize_key(stripped)
        if skey in TEAM_NAME_ALIASES:
            return TEAM_NAME_ALIASES[skey]
    return name


def _edition_slug(year: int) -> str | None:
    for ed in EDITIONS:
        if ed["year"] == year:
            return ed["slug"]
    return None


def _raw_url(slug: str, filename: str) -> str:
    return f"{BASE_RAW_URL}/{slug}/{filename}"


def _fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _url_exists(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _polite_delay(seconds: float = 0.5) -> None:
    time.sleep(seconds)


def _find_edition_snapshots(raw_root: Path, year: int) -> list[Path]:
    """Return all OpenFootball snapshot files for a given WC year."""
    snap_dir = raw_root / "snapshots"
    if not snap_dir.exists():
        return []
    return sorted(
        p
        for p in snap_dir.glob(f"openfootball-wc-{year}-*.txt")
        if not p.name.endswith("-manifest.json")
    )


def _load_team_roster(data_root: Path) -> list[dict]:
    """Load the official 2026 team roster."""
    teams_path = data_root / "teams.json"
    if not teams_path.exists():
        return []
    data = load_json(teams_path, {})
    return data.get("teams", []) if isinstance(data, dict) else []


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------


def cmd_plan(
    *,
    root: Path,
    edition: str,
    now: str | None = None,
) -> dict:
    """Discover available OpenFootball historical data files."""
    generated_at = iso_now(now)
    raw_root = raw_edition_root(root, edition)
    plan_path = raw_root / "evidence-packets" / "openfootball-history-plan.json"

    editions_plan: list[dict] = []
    for ed in EDITIONS:
        slug = ed["slug"]
        cup_url = _raw_url(slug, "cup.txt")
        finals_url = _raw_url(slug, "cup_finals.txt")
        entry: dict = {
            "year": ed["year"],
            "host": ed["host"],
            "slug": slug,
            "cup_url": cup_url,
            "finals_url": finals_url,
        }
        try:
            entry["cup_available"] = _url_exists(cup_url)
        except Exception:  # noqa: BLE001
            entry["cup_available"] = False
        try:
            entry["finals_available"] = _url_exists(finals_url)
        except Exception:  # noqa: BLE001
            entry["finals_available"] = False
        entry["status"] = (
            "available"
            if entry["cup_available"]
            else "missing"
        )
        editions_plan.append(entry)
        _polite_delay()

    result = {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "openfootball-history-plan",
        "source": "openfootball/worldcup (raw.githubusercontent.com)",
        "plan_path": str(plan_path),
        "editions": editions_plan,
        "summary": {
            "total": len(editions_plan),
            "available": sum(1 for e in editions_plan if e["status"] == "available"),
            "missing": sum(1 for e in editions_plan if e["status"] == "missing"),
        },
        "safety_invariants": [
            "plan_mode_does_not_download_any_data",
            "plan_uses_head_requests_only_to_check_availability",
        ],
    }
    write_json(plan_path, result)
    return result


# ---------------------------------------------------------------------------
# Subcommand: fetch
# ---------------------------------------------------------------------------


def _write_snapshot(
    *,
    raw_root: Path,
    edition: str,
    year: int,
    date_slug: str,
    content: bytes,
    url: str,
    generated_at: str,
    file_tag: str,
) -> dict:
    """Persist downloaded bytes and write a success manifest."""
    snap_dir = raw_root / "snapshots"
    manifest_dir = raw_root / "evidence-packets"
    snap_path = snap_dir / f"openfootball-wc-{year}-{file_tag}-{date_slug}.txt"
    manifest_path = (
        manifest_dir
        / f"openfootball-wc-{year}-{file_tag}-{date_slug}-snapshot-manifest.json"
    )

    snap_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    snap_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    manifest = {
        "version": 1,
        "edition": edition,
        "wc_year": year,
        "file_tag": file_tag,
        "url": url,
        "generated_at": generated_at,
        "mode": "openfootball-snapshot",
        "status": "snapshot_written",
        "snapshot_path": str(snap_path),
        "manifest_path": str(manifest_path),
        "bytes": len(content),
        "sha256": digest,
        "summary": {"fetches_performed": 1, "raw_writes_performed": 2},
        "safety_invariants": [
            "raw_snapshot_preserves_original_source_bytes",
            "snapshot_manifest_records_url_hash_and_byte_count",
        ],
    }
    write_json(manifest_path, manifest)
    return manifest


def _write_failed_manifest(
    *,
    raw_root: Path,
    edition: str,
    year: int,
    date_slug: str,
    url: str,
    generated_at: str,
    exc: Exception,
    file_tag: str = "cup",
) -> dict:
    """Record a fetch failure as evidence instead of silently succeeding."""
    manifest_dir = raw_root / "evidence-packets"
    manifest_path = (
        manifest_dir
        / f"openfootball-wc-{year}-{file_tag}-{date_slug}-snapshot-manifest.json"
    )
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": 1,
        "edition": edition,
        "wc_year": year,
        "url": url,
        "generated_at": generated_at,
        "mode": "openfootball-snapshot",
        "status": "blocked_fetch_failed",
        "snapshot_path": "",
        "manifest_path": str(manifest_path),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "summary": {"fetches_performed": 1, "raw_writes_performed": 1},
        "blockers": ["openfootball_fetch_failed"],
        "safety_invariants": [
            "failed_fetches_write_manifest_instead_of_silent_success",
            "blocked_fetch_failed_does_not_create_raw_snapshot_bytes",
        ],
    }
    write_json(manifest_path, manifest)
    return manifest


def _fetch_one_edition(
    *,
    root: Path,
    edition: str,
    year: int,
    now: str | None,
) -> list[dict]:
    """Fetch cup.txt (and cup_finals.txt when present) for a single WC year."""
    generated_at = iso_now(now)
    date_slug = generated_at[:10]
    raw_root = raw_edition_root(root, edition)
    slug = _edition_slug(year)
    if not slug:
        return [
            {
                "year": year,
                "status": "error",
                "error": f"unknown World Cup year: {year}",
            }
        ]

    results: list[dict] = []

    # -- cup.txt (always expected) --
    cup_url = _raw_url(slug, "cup.txt")
    try:
        content = _fetch_bytes(cup_url)
        manifest = _write_snapshot(
            raw_root=raw_root,
            edition=edition,
            year=year,
            date_slug=date_slug,
            content=content,
            url=cup_url,
            generated_at=generated_at,
            file_tag="cup",
        )
        results.append({"year": year, "file": "cup.txt", **manifest})
    except Exception as exc:  # noqa: BLE001
        manifest = _write_failed_manifest(
            raw_root=raw_root,
            edition=edition,
            year=year,
            date_slug=date_slug,
            url=cup_url,
            generated_at=generated_at,
            exc=exc,
            file_tag="cup",
        )
        results.append({"year": year, "file": "cup.txt", **manifest})

    _polite_delay()

    # -- cup_finals.txt (optional, exists for some years) --
    finals_url = _raw_url(slug, "cup_finals.txt")
    try:
        content = _fetch_bytes(finals_url)
        manifest = _write_snapshot(
            raw_root=raw_root,
            edition=edition,
            year=year,
            date_slug=date_slug,
            content=content,
            url=finals_url,
            generated_at=generated_at,
            file_tag="finals",
        )
        results.append({"year": year, "file": "cup_finals.txt", **manifest})
    except Exception as exc:  # noqa: BLE001
        if "404" not in str(exc):
            manifest = _write_failed_manifest(
                raw_root=raw_root,
                edition=edition,
                year=year,
                date_slug=date_slug,
                url=finals_url,
                generated_at=generated_at,
                exc=exc,
                file_tag="finals",
            )
            results.append({"year": year, "file": "cup_finals.txt", **manifest})
        # 404 is expected for years without a separate finals file -- skip.

    return results


def cmd_fetch(
    *,
    root: Path,
    edition: str,
    wc_year: int | None = None,
    fetch_all: bool = False,
    now: str | None = None,
) -> dict:
    """Fetch historical OpenFootball data for one or all World Cup editions."""
    generated_at = iso_now(now)

    years: list[int] = []
    if fetch_all:
        years = [ed["year"] for ed in EDITIONS]
    elif wc_year:
        years = [wc_year]
    else:
        return {
            "version": 1,
            "edition": edition,
            "generated_at": generated_at,
            "mode": "openfootball-fetch",
            "status": "error",
            "error": "specify --wc-year YYYY or --all",
            "results": [],
        }

    all_results: list[dict] = []
    for i, year in enumerate(years):
        results = _fetch_one_edition(
            root=root, edition=edition, year=year, now=now
        )
        all_results.extend(results)
        if i < len(years) - 1:
            _polite_delay()

    fetched = sum(
        1 for r in all_results if r.get("status") == "snapshot_written"
    )
    failed = sum(
        1 for r in all_results if r.get("status") == "blocked_fetch_failed"
    )

    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "openfootball-fetch",
        "status": "ok" if failed == 0 else "partial",
        "results": all_results,
        "summary": {
            "editions_requested": len(years),
            "snapshots_written": fetched,
            "fetches_failed": failed,
        },
    }


# ---------------------------------------------------------------------------
# Match parsing
# ---------------------------------------------------------------------------

# Matches a score like  3-1  or  0-2  (with surrounding whitespace).
_SCORE_RE = re.compile(r"\b(\d+)\s*-\s*(\d+)\b")
# Half-time score in parentheses:  (2-0)
_HT_RE = re.compile(r"\((\d+)\s*-\s*(\d+)[^)]*\)")
# Penalty shootout:  , 3-4 pen.  or  , 3-4 pen
_PEN_RE = re.compile(r",\s*(\d+)\s*-\s*(\d+)\s*pen\.?")
# Extra time marker
_AET_RE = re.compile(r"\ba\.?e\.?t\.?", re.IGNORECASE)
# Group header line (with optional leading bullet like ▪, *, -, etc.)
_GROUP_HEADER_RE = re.compile(r"^[\u25AA\u25AB\u25CF\u2022\*\-\s]*group\s+(\w+)", re.IGNORECASE)


def _parse_match_line(line: str, current_stage: str) -> dict | None:
    """Try to extract a match result from *line*.

    Returns a dict with home_team, away_team, home_goals, away_goals,
    home_pen, away_pen, stage -- or ``None`` if the line is not a match.
    """
    # Every real match line contains  @  (venue separator).
    if "@" not in line:
        return None

    # Need at least one score-like token.
    if not _SCORE_RE.search(line):
        return None

    # ---- Split on the score token ----
    m = _SCORE_RE.search(line)
    if not m:
        return None  # unreachable but keeps type-checkers happy

    home_part = line[: m.start()].strip()
    rest = line[m.end() :].strip()  # "(ht) away @ venue ..."

    # ---- Home team: strip leading time and date prefixes ----
    home_team = re.sub(r"^\d{1,2}:\d{2}\s*", "", home_part).strip()
    home_team = re.sub(r"^\d{1,2}\s+[A-Za-z]+\s*", "", home_team).strip()
    if not home_team:
        return None

    # ---- Penalty shootout (must be checked BEFORE HT extraction) ----
    home_pen: int | None = None
    away_pen: int | None = None
    pen_m = _PEN_RE.search(rest)
    if pen_m:
        home_pen = int(pen_m.group(1))
        away_pen = int(pen_m.group(2))

    # ---- Half-time (strip before team-name extraction) ----
    rest_clean = _HT_RE.sub("", rest)
    # Remove a.e.t. marker
    rest_clean = _AET_RE.sub("", rest_clean)
    # Remove penalty text
    rest_clean = re.sub(r",?\s*\d+\s*-\s*\d+\s*pen\.?", "", rest_clean)

    # ---- Away team: everything before  @  ----
    at_idx = rest_clean.find("@")
    away_team = rest_clean[:at_idx].strip() if at_idx > 0 else rest_clean.strip()
    # Strip stray dashes (left over from removed tokens).
    away_team = re.sub(r"^[\s\-]+|[\s\-]+$", "", away_team)

    if not away_team:
        return None

    home_goals = int(m.group(1))
    away_goals = int(m.group(2))

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_pen": home_pen,
        "away_pen": away_pen,
        "stage": current_stage,
    }


def _update_stage_tracker(line: str, stripped: str, current: str) -> str:
    """Return a possibly-updated stage label based on section headers."""
    low = line.lower()
    # Skip pipe-delimited table rows (e.g. "Group A | Team1 Team2 ...")
    # which are tournament structure summaries, not section headers.
    if "|" in line:
        return current
    # Explicit knockout headers (order matters -- final before semi, etc.)
    if re.search(r"\bfinal\b", low) and "semi" not in low and "third" not in low:
        return "Final"
    if "match for third place" in low or "third place" in low or "third-place" in low:
        return "Third Place"
    if re.search(r"\bsemi[\s\-]*final", low):
        return "Semi-final"
    if "quarter" in low and "final" in low:
        return "Quarter-final"
    if re.search(r"round\s+of\s+16", low):
        return "Round of 16"
    if re.search(r"round\s+of\s+32", low):
        return "Round of 32"
    if "second round" in low or "2nd round" in low or "second group" in low:
        return "Second Round"
    # Group header line (only non-pipe lines).
    gm = _GROUP_HEADER_RE.match(stripped)
    if gm:
        return f"Group {gm.group(1)}"
    return current


def parse_matches(text: str) -> list[dict]:
    """Parse all match results from OpenFootball cup text."""
    matches: list[dict] = []
    stage = "Group"

    for raw_line in text.split("\n"):
        # Strip comments.
        comment_idx = raw_line.find("#")
        line = raw_line[:comment_idx] if comment_idx >= 0 else raw_line
        stripped = line.strip()
        if not stripped:
            continue

        # Section separator (==== or ----) resets stage detection.
        if re.match(r"^[=\-]{3,}$", stripped):
            stage = "Group"
            continue

        stage = _update_stage_tracker(line, stripped, stage)

        match = _parse_match_line(stripped, stage)
        if match:
            matches.append(match)

    return matches


# ---------------------------------------------------------------------------
# Stage / result classification
# ---------------------------------------------------------------------------


def _classify_knockout_stage(
    matches: list[dict],
    year: int,
) -> dict[str, dict[str, str]]:
    """For each team, determine the furthest knockout stage reached.

    Returns ``{normalized_key: {str(year): result_label}}``.
    """
    results: dict[str, dict[str, str]] = {}

    # Collect matches by detected stage.
    final_matches: list[dict] = []
    third_matches: list[dict] = []
    semi_matches: list[dict] = []
    qf_matches: list[dict] = []
    r16_matches: list[dict] = []
    group_matches: list[dict] = []

    for match in matches:
        stg = match.get("stage", "")
        stg_low = stg.lower()
        if stg_low == "final":
            final_matches.append(match)
        elif "third" in stg_low:
            third_matches.append(match)
        elif "semi" in stg_low:
            semi_matches.append(match)
        elif "quarter" in stg_low:
            qf_matches.append(match)
        elif "round of 16" in stg_low or "round of 32" in stg_low:
            r16_matches.append(match)
        else:
            group_matches.append(match)

    yr = str(year)

    def _set(team: str, label: str) -> None:
        key = _normalize_key(team)
        if not key:
            return
        results.setdefault(key, {})
        existing = results[key].get(yr)
        if existing is None or _STAGE_RANK.get(label, 0) > _STAGE_RANK.get(existing, 0):
            results[key][yr] = label

    # -- Final --
    for m in final_matches:
        winner, loser = _match_winner(m)
        if winner:
            _set(winner, "winner")
        if loser:
            _set(loser, "runner_up")

    # -- Third place --
    for m in third_matches:
        winner, loser = _match_winner(m)
        if winner:
            _set(winner, "third")
        if loser:
            _set(loser, "fourth")

    # -- Semi-finals (losers) --
    for m in semi_matches:
        winner, loser = _match_winner(m)
        if loser:
            _set(loser, "semi_final")

    # -- Quarter-finals (losers) --
    for m in qf_matches:
        _, loser = _match_winner(m)
        if loser:
            _set(loser, "quarterfinal")

    # -- Round of 16 (losers) --
    for m in r16_matches:
        _, loser = _match_winner(m)
        if loser:
            _set(loser, "round_of_16")

    # -- Group stage participants --
    for m in group_matches:
        _set(m["home_team"], "group_stage")
        _set(m["away_team"], "group_stage")

    # -- 1950 special case: no official final; the decisive match was in the
    #    final group (Uruguay 2-1 Brazil).  Override if our generic parser
    #    missed it.
    if year == 1950:
        ukey = _normalize_key("Uruguay")
        bkey = _normalize_key("Brazil")
        if results.get(ukey, {}).get(yr, "group_stage") not in ("winner", "runner_up"):
            results.setdefault(ukey, {})[yr] = "winner"
        if results.get(bkey, {}).get(yr, "group_stage") not in ("winner", "runner_up"):
            results.setdefault(bkey, {})[yr] = "runner_up"

    return results


def _match_winner(match: dict) -> tuple[str | None, str | None]:
    """Return (winner, loser) team names.  Ties return (None, None)."""
    hp = match.get("home_pen")
    ap = match.get("away_pen")
    if hp is not None and ap is not None:
        if hp > ap:
            return match["home_team"], match["away_team"]
        if ap > hp:
            return match["away_team"], match["home_team"]
        return None, None
    hg, ag = match["home_goals"], match["away_goals"]
    if hg > ag:
        return match["home_team"], match["away_team"]
    if ag > hg:
        return match["away_team"], match["home_team"]
    return None, None


def _better_result(a: str, b: str) -> str:
    """Return whichever label represents the better tournament finish."""
    return a if _STAGE_RANK.get(a, 0) >= _STAGE_RANK.get(b, 0) else b


# ---------------------------------------------------------------------------
# Subcommand: compile
# ---------------------------------------------------------------------------


def cmd_compile(
    *,
    root: Path,
    edition: str,
    now: str | None = None,
) -> dict:
    """Compile fetched historical data into per-team WC history features."""
    generated_at = iso_now(now)
    raw_root = raw_edition_root(root, edition)
    data_root = edition_data_root(root, edition)
    history_dir = data_root / "history"
    history_path = history_dir / "team-wc-history.json"

    # -- Discover all downloaded snapshots --
    snap_dir = raw_root / "snapshots"
    if not snap_dir.exists():
        snap_dir.mkdir(parents=True, exist_ok=True)

    # -- Per-year results accumulator --
    # {normalized_key: {str(year): result_label}}
    year_results: dict[str, dict[str, str]] = {}
    # {normalized_key: {wc_total_matches, wc_wins, ...}}
    stats: dict[str, dict] = {}
    # Track which years have been processed.
    processed_years: set[int] = set()
    # Track which years had no parseable data.
    empty_years: list[int] = []

    for ed in EDITIONS:
        year = ed["year"]
        snapshots = _find_edition_snapshots(raw_root, year)
        if not snapshots:
            continue

        processed_years.add(year)
        text_parts: list[str] = []
        for snap in snapshots:
            raw_bytes = snap.read_bytes()
            text_parts.append(raw_bytes.decode("utf-8", errors="replace"))

        combined = "\n".join(text_parts)
        matches = parse_matches(combined)
        if not matches:
            empty_years.append(year)
            continue

        # Classify knockout stages.
        knockout = _classify_knockout_stage(matches, year)
        # Merge into global accumulator.
        for team_key, yr_map in knockout.items():
            year_results.setdefault(team_key, {}).update(yr_map)

        # Update match-level statistics.
        for match in matches:
            for side in ("home", "away"):
                raw_name = match[f"{side}_team"]
                key = _normalize_key(raw_name)
                if not key:
                    continue
                stats.setdefault(
                    key,
                    {
                        "wc_total_matches": 0,
                        "wc_wins": 0,
                        "wc_draws": 0,
                        "wc_losses": 0,
                        "wc_goals_for": 0,
                        "wc_goals_against": 0,
                    },
                )
                s = stats[key]
                s["wc_total_matches"] += 1
                own_goals = match[f"{side}_goals"]
                opp_side = "away" if side == "home" else "home"
                opp_goals = match[f"{opp_side}_goals"]
                s["wc_goals_for"] += own_goals
                s["wc_goals_against"] += opp_goals

                # Determine W/D/L from penalty results if available, else goals.
                hp = match.get("home_pen")
                ap = match.get("away_pen")
                if hp is not None and ap is not None:
                    own_pen = hp if side == "home" else ap
                    opp_pen = ap if side == "home" else hp
                    if own_pen > opp_pen:
                        s["wc_wins"] += 1
                    elif opp_pen > own_pen:
                        s["wc_losses"] += 1
                    else:
                        s["wc_draws"] += 1
                else:
                    if own_goals > opp_goals:
                        s["wc_wins"] += 1
                    elif own_goals < opp_goals:
                        s["wc_losses"] += 1
                    else:
                        s["wc_draws"] += 1

    # -- Build team history for each 2026 roster team --
    roster_teams = _load_team_roster(data_root)
    if not roster_teams:
        return {
            "version": 1,
            "edition": edition,
            "generated_at": generated_at,
            "mode": "openfootball-compile",
            "status": "error",
            "error": "teams.json not found; run worldcup_edition_init.py first",
            "output_path": str(history_path),
        }

    team_histories: list[dict] = []
    for team in roster_teams:
        name = team.get("name", "")
        key = _normalize_key(name)
        ts = stats.get(key, {})

        # Appearances = number of distinct WC years with at least one match.
        yr_map = year_results.get(key, {})
        appearances_set = {y for y, lbl in yr_map.items() if lbl != "did_not_participate"}
        wc_appearances = len(appearances_set)

        # Titles.
        wc_titles = sum(1 for lbl in yr_map.values() if lbl == "winner")

        # Best result.
        wc_best_result = "none"
        for lbl in yr_map.values():
            if wc_best_result == "none":
                wc_best_result = lbl
            else:
                wc_best_result = _better_result(wc_best_result, lbl)

        # Recent form (2014, 2018, 2022).
        wc_recent_form: dict[str, str | None] = {}
        for ry in [2014, 2018, 2022]:
            wc_recent_form[str(ry)] = yr_map.get(str(ry))

        history = {
            "team_id": team.get("team_id", ""),
            "name": name,
            "code": team.get("code", ""),
            "wc_appearances": wc_appearances,
            "wc_titles": wc_titles,
            "wc_best_result": wc_best_result,
            "wc_recent_form": wc_recent_form,
            "wc_total_matches": ts.get("wc_total_matches", 0),
            "wc_wins": ts.get("wc_wins", 0),
            "wc_draws": ts.get("wc_draws", 0),
            "wc_losses": ts.get("wc_losses", 0),
            "wc_goals_for": ts.get("wc_goals_for", 0),
            "wc_goals_against": ts.get("wc_goals_against", 0),
        }
        team_histories.append(history)

    result = {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "openfootball-compile",
        "status": "compiled",
        "output_path": str(history_path),
        "summary": {
            "editions_processed": len(processed_years),
            "editions_with_data": len(processed_years) - len(empty_years),
            "editions_empty": empty_years,
            "teams_with_history": sum(1 for h in team_histories if h["wc_appearances"] > 0),
            "teams_without_history": sum(1 for h in team_histories if h["wc_appearances"] == 0),
            "total_teams": len(team_histories),
        },
        "teams": team_histories,
        "safety_invariants": [
            "compile_only_reads_snapshots_never_fetches",
            "team_history_uses_2026_roster_as_reference_set",
            "missing_team_names_get_zero_stats_not_errors",
        ],
    }
    write_json(history_path, result)
    return result


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(
    *,
    root: Path,
    edition: str,
    now: str | None = None,
) -> dict:
    """Report which historical World Cup editions have been fetched."""
    generated_at = iso_now(now)
    raw_root = raw_edition_root(root, edition)

    fetched: list[dict] = []
    blocked: list[dict] = []
    missing: list[dict] = []

    for ed in EDITIONS:
        year = ed["year"]
        snapshots = _find_edition_snapshots(raw_root, year)
        if snapshots:
            fetched.append(
                {
                    "year": year,
                    "host": ed["host"],
                    "files": [str(p.name) for p in snapshots],
                    "status": "fetched",
                }
            )
        else:
            # Check for failed manifests.
            manifest_dir = raw_root / "evidence-packets"
            failed = (
                list(manifest_dir.glob(f"openfootball-wc-{year}-*-snapshot-manifest.json"))
                if manifest_dir.exists()
                else []
            )
            failed_manifests = [
                p
                for p in failed
                if isinstance(load_json(p, {}), dict)
                and load_json(p, {}).get("status") == "blocked_fetch_failed"
            ]
            if failed_manifests:
                blocked.append(
                    {
                        "year": year,
                        "host": ed["host"],
                        "status": "blocked",
                        "manifests": [str(p.name) for p in failed_manifests],
                    }
                )
            else:
                missing.append(
                    {
                        "year": year,
                        "host": ed["host"],
                        "status": "not_fetched",
                    }
                )

    # Check compiled output.
    data_root = edition_data_root(root, edition)
    history_path = data_root / "history" / "team-wc-history.json"
    compiled = history_path.exists()

    return {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "openfootball-status",
        "fetched": fetched,
        "blocked": blocked,
        "missing": missing,
        "compiled_history": {
            "available": compiled,
            "path": str(history_path),
        },
        "summary": {
            "total_editions": len(EDITIONS),
            "fetched": len(fetched),
            "blocked": len(blocked),
            "missing": len(missing),
            "completeness": f"{len(fetched)}/{len(EDITIONS)}",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    # -- plan --
    p_plan = sub.add_parser("plan", help="List available historical WC data files")
    p_plan.add_argument("--edition", required=True)
    p_plan.add_argument("--root", default=".")
    p_plan.add_argument("--now")

    # -- fetch --
    p_fetch = sub.add_parser("fetch", help="Fetch historical data for an edition")
    p_fetch.add_argument("--edition", required=True)
    p_fetch.add_argument("--root", default=".")
    p_fetch.add_argument("--now")
    p_fetch.add_argument("--wc-year", type=int, default=None, help="World Cup year (e.g. 2022)")
    p_fetch.add_argument(
        "--all", action="store_true", dest="fetch_all", help="Fetch all editions"
    )

    # -- compile --
    p_compile = sub.add_parser("compile", help="Compile team history features from snapshots")
    p_compile.add_argument("--edition", required=True)
    p_compile.add_argument("--root", default=".")
    p_compile.add_argument("--now")

    # -- status --
    p_status = sub.add_parser("status", help="Show acquisition status")
    p_status.add_argument("--edition", required=True)
    p_status.add_argument("--root", default=".")
    p_status.add_argument("--now")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    edition: str = args.edition
    now: str | None = args.now

    if args.command == "plan":
        result = cmd_plan(root=root, edition=edition, now=now)
    elif args.command == "fetch":
        result = cmd_fetch(
            root=root,
            edition=edition,
            wc_year=args.wc_year,
            fetch_all=args.fetch_all,
            now=now,
        )
    elif args.command == "compile":
        result = cmd_compile(root=root, edition=edition, now=now)
    elif args.command == "status":
        result = cmd_status(root=root, edition=edition, now=now)
    else:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    status = str(result.get("status", ""))
    return 0 if not status.startswith("blocked") and status != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
