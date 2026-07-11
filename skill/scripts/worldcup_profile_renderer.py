#!/usr/bin/env python3
"""Render team and player profile markdown files from SQLite database.

This script reads structured data from the SQLite database and generates
enriched markdown profile pages for teams and players.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import (  # noqa: E402
    iso_now,
    load_json,
    slugify,
    wiki_edition_root,
    worldcup_db_path,
)
from worldcup_db import get_db_connection, init_database  # noqa: E402


def _safe(value: object) -> str:
    """Escape HTML special characters."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _result_emoji(result: str) -> str:
    """Return emoji for match result."""
    return {"W": "✅", "D": "🟡", "L": "❌"}.get(result, "⚪")


def get_team_data(conn, team_id: str) -> dict:
    """Fetch all data for a team from the database."""
    team = conn.execute(
        "SELECT * FROM teams WHERE team_id = ?", (team_id,)
    ).fetchone()
    if not team:
        return {}

    players = conn.execute(
        "SELECT * FROM players WHERE team_id = ? ORDER BY shirt_number",
        (team_id,),
    ).fetchall()

    # Recent form (last 5 matches)
    form = conn.execute(
        """SELECT tf.*, m.home_team_id, m.away_team_id,
                  ht.name_en as home_name, at.name_en as away_name
           FROM team_form tf
           JOIN matches m ON tf.match_id = m.match_id
           LEFT JOIN teams ht ON m.home_team_id = ht.team_id
           LEFT JOIN teams at ON m.away_team_id = at.team_id
           WHERE tf.team_id = ?
           ORDER BY tf.match_date DESC
           LIMIT 5""",
        (team_id,),
    ).fetchall()

    # Active injuries
    injuries = conn.execute(
        """SELECT * FROM injuries
           WHERE team_id = ? AND status = 'active'
           ORDER BY severity DESC, player_name""",
        (team_id,),
    ).fetchall()

    # Recent news sentiment
    news = conn.execute(
        """SELECT * FROM news_sentiment
           WHERE team_id = ?
           ORDER BY date DESC, recorded_at DESC
           LIMIT 10""",
        (team_id,),
    ).fetchall()

    # Profile metadata
    meta = conn.execute(
        "SELECT * FROM team_profile_meta WHERE team_id = ?", (team_id,)
    ).fetchone()

    # Ranking
    ranking = conn.execute(
        "SELECT * FROM rankings_snapshot WHERE team_id = ?", (team_id,)
    ).fetchone()

    # Upcoming matches
    upcoming = conn.execute(
        """SELECT m.*, ht.name_en as home_name, at.name_en as away_name
           FROM matches m
           LEFT JOIN teams ht ON m.home_team_id = ht.team_id
           LEFT JOIN teams at ON m.away_team_id = at.team_id
           WHERE (m.home_team_id = ? OR m.away_team_id = ?)
             AND m.final_score_home IS NULL
           ORDER BY m.kickoff_at
           LIMIT 3""",
        (team_id, team_id),
    ).fetchall()

    return {
        "team": dict(team),
        "players": [dict(p) for p in players],
        "form": [dict(f) for f in form],
        "injuries": [dict(i) for i in injuries],
        "news": [dict(n) for n in news],
        "meta": dict(meta) if meta else {},
        "ranking": dict(ranking) if ranking else {},
        "upcoming": [dict(u) for u in upcoming],
    }


def render_team_profile(data: dict, edition: str, generated_at: str) -> str:
    """Generate markdown for a team profile."""
    team = data["team"]
    meta = data.get("meta", {})
    ranking = data.get("ranking", {})
    form = data.get("form", [])
    injuries = data.get("injuries", [])
    news = data.get("news", [])
    upcoming = data.get("upcoming", [])
    players = data.get("players", [])

    team_name = team.get("name_en") or team.get("name_zh") or team["team_id"]
    team_id = team["team_id"]
    group = meta.get("group_name") or ""

    lines = [
        "---",
        "type: entity",
        "entity_type: national_team",
        f"edition: {edition}",
        "status: enriched",
        f"updated: {generated_at[:10]}",
        "---",
        "",
        f"# {team_name}",
        "",
        "## 基本信息",
        "",
        f"- **Team ID**: `{team_id}`",
    ]

    if group:
        lines.append(f"- **小组**: {group}")
    if ranking:
        lines.append(f"- **FIFA 排名**: #{ranking.get('rank', 'N/A')} ({ranking.get('points', 'N/A')} 分)")
    if meta.get("head_coach"):
        lines.append(f"- **主教练**: {meta['head_coach']}")
    if meta.get("formation"):
        lines.append(f"- **常用阵型**: {meta['formation']}")
    if meta.get("wc_appearances"):
        best = meta.get("wc_best_result", "")
        lines.append(f"- **世界杯参赛**: {meta['wc_appearances']}次 | 最佳: {best or 'N/A'}")

    lines.append("")

    # Recent form
    if form:
        lines.extend(["## 近期状态", ""])
        lines.append("| 日期 | 对手 | 比分 | 结果 |")
        lines.append("|------|------|------|------|")
        for f_match in form:
            date = f_match.get("match_date", "")
            is_home = f_match.get("is_home", 1)
            if is_home:
                opponent = f_match.get("away_name") or f_match.get("opponent_id", "?")
            else:
                opponent = f_match.get("home_name") or f_match.get("opponent_id", "?")
            score = f"{f_match.get('goals_for', '?')}-{f_match.get('goals_against', '?')}"
            result = _result_emoji(f_match.get("result", ""))
            lines.append(f"| {date} | {opponent} | {score} | {result} |")
        lines.append("")

        # Form summary
        results = [f.get("result", "") for f in form]
        wins = results.count("W")
        draws = results.count("D")
        losses = results.count("L")
        lines.append(f"**近 {len(form)} 场**: {wins}胜 {draws}平 {losses}负")
        lines.append("")

    # Active injuries
    if injuries:
        lines.extend(["## 当前伤停", ""])
        lines.append("| 球员 | 类型 | 原因 | 严重程度 | 状态 |")
        lines.append("|------|------|------|----------|------|")
        for inj in injuries:
            player = inj.get("player_name", "Unknown")
            inj_type = "🤕 伤" if inj.get("injury_type") == "injury" else "🟥 停"
            reason = inj.get("reason") or "-"
            severity = inj.get("severity", "-")
            status = inj.get("status", "active")
            lines.append(f"| {player} | {inj_type} | {reason} | {severity} | {status} |")
        lines.append("")

    # News sentiment
    if news:
        lines.extend(["## 舆论风向", ""])
        positive = sum(1 for n in news if n.get("sentiment") == "positive")
        negative = sum(1 for n in news if n.get("sentiment") == "negative")
        neutral = len(news) - positive - negative
        lines.append(f"近期报道情绪: 🟢 正面 {positive} | 🔴 负面 {negative} | ⚪ 中性 {neutral}")
        lines.append("")
        for n in news[:5]:
            headline = n.get("headline", "")
            sentiment = n.get("sentiment", "neutral")
            icon = "🟢" if sentiment == "positive" else ("🔴" if sentiment == "negative" else "⚪")
            lines.append(f"- {icon} {headline}")
        lines.append("")

    # Upcoming matches
    if upcoming:
        lines.extend(["##  upcoming 比赛", ""])
        for match in upcoming:
            kickoff = match.get("kickoff_at", "")
            home = match.get("home_name") or match.get("home_team_id", "?")
            away = match.get("away_name") or match.get("away_team_id", "?")
            venue = match.get("venue", "")
            lines.append(f"- **{kickoff}**: {home} vs {away}" + (f" @ {venue}" if venue else ""))
        lines.append("")

    # Squad
    if players:
        lines.extend(["## 阵容", ""])
        by_position = {}
        for p in players:
            pos = p.get("position", "Other")
            by_position.setdefault(pos, []).append(p)

        for pos in ["GK", "DF", "MF", "FW"]:
            pos_players = by_position.get(pos, [])
            if pos_players:
                pos_name = {"GK": "门将", "DF": "后卫", "MF": "中场", "FW": "前锋"}.get(pos, pos)
                lines.append(f"### {pos_name}")
                lines.append("")
                for p in pos_players:
                    num = p.get("shirt_number", "-")
                    name = p.get("player_name") or p.get("name_on_shirt") or p["player_id"]
                    club = p.get("club", "")
                    lines.append(f"- **#{num}** {name}" + (f" ({club})" if club else ""))
                lines.append("")

    lines.extend([
        "---",
        "",
        f"*Generated at {generated_at}*",
    ])

    return "\n".join(lines)


def render_player_profile(player: dict, team: dict, injuries: list, edition: str, generated_at: str) -> str:
    """Generate markdown for a player profile."""
    player_name = player.get("player_name") or player.get("name_on_shirt") or player["player_id"]
    team_name = team.get("name_en") or team.get("name_zh") or team.get("team_id", "")

    # Check if player has active injuries
    player_injuries = [
        i for i in injuries
        if (i.get("player_name", "").lower() == player_name.lower() or
            i.get("player_id") == player.get("player_id"))
    ]

    lines = [
        "---",
        "type: entity",
        "entity_type: player",
        f"edition: {edition}",
        "status: enriched",
        f"updated: {generated_at[:10]}",
        "---",
        "",
        f"# {player_name}",
        "",
        "## 基础信息",
        "",
        f"- **Player ID**: `{player['player_id']}`",
        f"- **国家队**: {team_name}",
        f"- **位置**: {player.get('position', 'N/A')}",
        f"- **号码**: #{player.get('shirt_number', 'N/A')}",
    ]

    if player.get("club"):
        lines.append(f"- **俱乐部**: {player['club']}")
    if player.get("dob"):
        lines.append(f"- **出生日期**: {player['dob']}")
    if player.get("height_cm"):
        lines.append(f"- **身高**: {player['height_cm']}cm")

    lines.append("")

    # Injury status
    if player_injuries:
        lines.extend(["## 伤停状态", ""])
        for inj in player_injuries:
            inj_type = "伤" if inj.get("injury_type") == "injury" else "停赛"
            reason = inj.get("reason") or "未说明"
            severity = inj.get("severity", "未知")
            status = inj.get("status", "active")
            lines.append(f"- **{inj_type}**: {reason}")
            lines.append(f"  - 严重程度: {severity}")
            lines.append(f"  - 状态: {status}")
            if inj.get("expected_end_date"):
                lines.append(f"  - 预计恢复: {inj['expected_end_date']}")
        lines.append("")
    else:
        lines.extend(["## 当前状态", "", "✅ 健康可用", ""])

    lines.extend([
        "---",
        "",
        f"*Generated at {generated_at}*",
    ])

    return "\n".join(lines)


def render_all_profiles(root: Path, edition: str, now: str | None = None) -> dict:
    """Render all team and player profiles."""
    generated_at = now or iso_now()
    db_path = worldcup_db_path(root, edition)
    wiki_root = wiki_edition_root(root, edition)

    teams_dir = wiki_root / "entities" / "teams"
    players_dir = wiki_root / "entities" / "players"
    teams_dir.mkdir(parents=True, exist_ok=True)
    players_dir.mkdir(parents=True, exist_ok=True)

    init_database(db_path)
    conn = get_db_connection(db_path)

    teams_rendered = 0
    players_rendered = 0

    try:
        # Get all teams
        teams = conn.execute("SELECT * FROM teams ORDER BY team_id").fetchall()

        for team_row in teams:
            team_id = team_row["team_id"]
            team_data = get_team_data(conn, team_id)

            if not team_data.get("team"):
                continue

            # Render team profile
            team_md = render_team_profile(team_data, edition, generated_at)
            team_file = teams_dir / f"{slugify(team_id)}.md"
            team_file.write_text(team_md, encoding="utf-8")
            teams_rendered += 1

            # Render player profiles
            for player in team_data.get("players", []):
                player_md = render_player_profile(
                    player,
                    team_data["team"],
                    team_data.get("injuries", []),
                    edition,
                    generated_at,
                )
                player_file = players_dir / f"{slugify(player['player_id'])}.md"
                player_file.write_text(player_md, encoding="utf-8")
                players_rendered += 1

    finally:
        conn.close()

    return {
        "status": "profiles_rendered",
        "teams_rendered": teams_rendered,
        "players_rendered": players_rendered,
        "generated_at": generated_at,
    }


def main():
    parser = argparse.ArgumentParser(description="Render team/player profiles from database")
    parser.add_argument("--edition", required=True, help="World Cup edition (e.g., 2026)")
    parser.add_argument("--root", default=".", help="Project root path")
    parser.add_argument("--team", help="Render only this team (team_id)")
    parser.add_argument("--now", help="Override current timestamp")

    args = parser.parse_args()

    if args.team:
        # Single team mode - TODO: implement if needed
        print(f"Single team rendering not yet implemented: {args.team}")
        sys.exit(1)

    result = render_all_profiles(Path(args.root), args.edition, args.now)
    print(f"Rendered {result['teams_rendered']} teams, {result['players_rendered']} players")
    print(f"Generated at: {result['generated_at']}")


if __name__ == "__main__":
    main()
