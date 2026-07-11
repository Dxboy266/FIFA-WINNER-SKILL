#!/usr/bin/env python3
"""MCP server exposing FIFA World Cup 2026 prediction capabilities as tools.

Transport: stdio (for use with Claude Desktop, Qoder, or any MCP client).

Usage:
    python skill/scripts/mcp_server.py

Configuration (mcp_config.json / claude_desktop_config.json):
    {
      "mcpServers": {
        "fifa-predictor": {
          "command": "python",
          "args": ["skill/scripts/mcp_server.py"],
          "cwd": "."
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure sibling modules are importable
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import TextContent, Tool  # noqa: E402

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
EDITION = "2026"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # skill/scripts -> skill -> project root


def _db_path() -> Path:
    """Resolve the SQLite database path using worldcup_core helper."""
    from worldcup_core import worldcup_db_path
    return worldcup_db_path(PROJECT_ROOT, EDITION)


def _get_conn() -> sqlite3.Connection:
    """Return a Row-factory connection to the project DB, ensuring tables exist."""
    from worldcup_db import get_db_connection, init_database
    db = _db_path()
    if not db.exists():
        raise FileNotFoundError(f"Database not found at {db}. Run init or pipeline first.")
    init_database(db)  # idempotent: CREATE TABLE IF NOT EXISTS
    return get_db_connection(db)


def _run_script(script: str, args: list[str], timeout: int = 300) -> dict:
    """Run a project script via subprocess and return structured output."""
    cmd = [sys.executable, str(SCRIPT_DIR / script)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
        )
        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr.strip() or f"Exit code {result.returncode}",
                "stdout": result.stdout.strip()[:2000],
            }
        # Try to parse JSON from stdout; fall back to raw text
        stdout = result.stdout.strip()
        try:
            data = json.loads(stdout)
            return {"success": True, "data": data}
        except (json.JSONDecodeError, ValueError):
            return {"success": True, "output": stdout[:4000]}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timed out after {timeout}s"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def _safe_query(conn: sqlite3.Connection, sql: str, params: tuple = (), fetchone: bool = False):
    """Execute a query, returning None/[] if the table does not exist yet."""
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone() if fetchone else cur.fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None if fetchone else []
        raise


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
app = Server("fifa-predictor")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="predict_matches",
            description="Run pre-match entertainment predictions for a given date. Generates prediction reports and stores them in the database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Match date in YYYY-MM-DD format",
                    },
                },
                "required": ["date"],
            },
        ),
        Tool(
            name="get_team_profile",
            description="Get comprehensive team profile including FIFA ranking, recent form, injuries, news sentiment, and metadata from the database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "team_id": {
                        "type": "string",
                        "description": "Team identifier (e.g. 'brazil', 'argentina', 'fra')",
                    },
                },
                "required": ["team_id"],
            },
        ),
        Tool(
            name="get_match_results",
            description="Query recorded match results from the database. Optionally filter by date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Optional date filter in YYYY-MM-DD format. If omitted, returns all completed matches.",
                    },
                },
            },
        ),
        Tool(
            name="get_prediction_accuracy",
            description="Get overall prediction accuracy statistics: result hit rate, score hit rate, total-goals hit rate, Brier score, and confidence calibration.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_daily_briefing",
            description="Get a briefing of all matches scheduled for a given date, including kickoff times, teams, venue, and group.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format",
                    },
                },
                "required": ["date"],
            },
        ),
        Tool(
            name="run_full_pipeline",
            description="Run the complete daily post-match pipeline: fetch results, evaluate predictions, reflection tuning, regenerate dashboard, and render profiles.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date context in YYYY-MM-DD format (used for logging; pipeline processes all scored dates)",
                    },
                },
                "required": ["date"],
            },
        ),
        Tool(
            name="regenerate_dashboard",
            description="Regenerate the visual prediction dashboard HTML from current database state.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "predict_matches":
            result = _tool_predict_matches(arguments)
        elif name == "get_team_profile":
            result = _tool_get_team_profile(arguments)
        elif name == "get_match_results":
            result = _tool_get_match_results(arguments)
        elif name == "get_prediction_accuracy":
            result = _tool_get_prediction_accuracy(arguments)
        elif name == "get_daily_briefing":
            result = _tool_get_daily_briefing(arguments)
        elif name == "run_full_pipeline":
            result = _tool_run_full_pipeline(arguments)
        elif name == "regenerate_dashboard":
            result = _tool_regenerate_dashboard(arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2, default=str))]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_predict_matches(args: dict) -> dict:
    """Run daily_prediction_runner.py for a given date."""
    date = args["date"]
    return _run_script(
        "daily_prediction_runner.py",
        ["run", "--edition", EDITION, "--date", date, "--root", str(PROJECT_ROOT)],
    )


def _tool_get_team_profile(args: dict) -> dict:
    """Query team data across multiple tables."""
    team_id = args["team_id"].lower().strip()
    conn = _get_conn()
    try:
        # Basic team info
        row = conn.execute(
            "SELECT * FROM teams WHERE team_id = ? OR LOWER(code) = ?",
            (team_id, team_id),
        ).fetchone()
        if not row:
            return {"error": f"Team '{team_id}' not found", "hint": "Use the team_id as stored in the teams table (e.g. 'brazil', 'argentina')."}

        team = _row_to_dict(row)
        tid = team["team_id"]

        # FIFA ranking
        ranking = _safe_query(
            conn,
            "SELECT rank, points, confederation, snapshot_date FROM rankings_snapshot WHERE team_id = ?",
            (tid,),
            fetchone=True,
        )
        team["ranking"] = _row_to_dict(ranking) if ranking else None

        # Recent form (last 5 matches)
        form_rows = _safe_query(
            conn,
            "SELECT match_id, match_date, opponent_id, goals_for, goals_against, result, is_home "
            "FROM team_form WHERE team_id = ? ORDER BY match_date DESC LIMIT 5",
            (tid,),
        )
        team["recent_form"] = [_row_to_dict(r) for r in form_rows]

        # Active injuries
        injury_rows = _safe_query(
            conn,
            "SELECT player_name, injury_type, reason, severity, status, expected_end_date "
            "FROM injuries WHERE team_id = ? AND status = 'active' ORDER BY severity DESC",
            (tid,),
        )
        team["injuries"] = [_row_to_dict(r) for r in injury_rows]

        # Latest news sentiment
        news_rows = _safe_query(
            conn,
            "SELECT date, headline, sentiment, impact, source "
            "FROM news_sentiment WHERE team_id = ? ORDER BY date DESC LIMIT 5",
            (tid,),
        )
        team["news_sentiment"] = [_row_to_dict(r) for r in news_rows]

        # Profile metadata (group, coach, formation, WC history)
        meta = _safe_query(
            conn,
            "SELECT group_name, head_coach, formation, wc_appearances, wc_best_result, last_updated "
            "FROM team_profile_meta WHERE team_id = ?",
            (tid,),
            fetchone=True,
        )
        team["profile_meta"] = _row_to_dict(meta) if meta else None

        return team
    finally:
        conn.close()


def _tool_get_match_results(args: dict) -> dict:
    """Query match results from the matches table."""
    date = args.get("date")
    conn = _get_conn()
    try:
        if date:
            rows = conn.execute(
                "SELECT m.match_id, m.kickoff_at, m.phase, m.group_name, m.venue, m.status, "
                "  m.home_team_id, m.away_team_id, m.final_score_home, m.final_score_away, "
                "  h.name_en AS home_name, a.name_en AS away_name "
                "FROM matches m "
                "LEFT JOIN teams h ON m.home_team_id = h.team_id "
                "LEFT JOIN teams a ON m.away_team_id = a.team_id "
                "WHERE DATE(m.kickoff_at) = ? AND m.final_score_home IS NOT NULL "
                "ORDER BY m.kickoff_at",
                (date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT m.match_id, m.kickoff_at, m.phase, m.group_name, m.venue, m.status, "
                "  m.home_team_id, m.away_team_id, m.final_score_home, m.final_score_away, "
                "  h.name_en AS home_name, a.name_en AS away_name "
                "FROM matches m "
                "LEFT JOIN teams h ON m.home_team_id = h.team_id "
                "LEFT JOIN teams a ON m.away_team_id = a.team_id "
                "WHERE m.final_score_home IS NOT NULL "
                "ORDER BY m.kickoff_at",
            ).fetchall()

        matches = [_row_to_dict(r) for r in rows]
        return {"count": len(matches), "date_filter": date, "matches": matches}
    finally:
        conn.close()


def _tool_get_prediction_accuracy(_args: dict) -> dict:
    """Aggregate prediction accuracy from daily_stats and evaluations."""
    conn = _get_conn()
    try:
        # Overall aggregates from daily_stats
        overall = conn.execute(
            "SELECT "
            "  SUM(matches_evaluated) AS total_matches_evaluated, "
            "  SUM(result_hits) AS total_result_hits, "
            "  SUM(score_hits) AS total_score_hits, "
            "  SUM(total_goals_hits) AS total_goals_hits, "
            "  AVG(result_hit_rate) AS avg_result_hit_rate, "
            "  AVG(score_hit_rate) AS avg_score_hit_rate, "
            "  AVG(total_goals_hit_rate) AS avg_total_goals_hit_rate, "
            "  AVG(brier_score_result) AS avg_brier_score "
            "FROM daily_stats",
        ).fetchone()

        result: dict = {}
        if overall and overall["total_matches_evaluated"]:
            total = overall["total_matches_evaluated"]
            result["overall"] = {
                "total_matches_evaluated": total,
                "result_hit_rate": round((overall["total_result_hits"] or 0) / total * 100, 1),
                "score_hit_rate": round((overall["total_score_hits"] or 0) / total * 100, 1),
                "total_goals_hit_rate": round((overall["total_goals_hits"] or 0) / total * 100, 1),
                "avg_brier_score": round(overall["avg_brier_score"], 4) if overall["avg_brier_score"] else None,
            }
        else:
            result["overall"] = {"total_matches_evaluated": 0, "message": "No evaluations yet"}

        # Confidence calibration from evaluations + predictions
        calibration = conn.execute(
            "SELECT "
            "  p.confidence, "
            "  COUNT(e.match_id) AS evaluated, "
            "  SUM(e.is_result_correct) AS result_hits "
            "FROM evaluations e "
            "JOIN predictions p ON e.match_id = p.match_id "
            "GROUP BY p.confidence "
            "ORDER BY evaluated DESC",
        ).fetchall()
        if calibration:
            result["confidence_calibration"] = []
            for row in calibration:
                evaluated = row["evaluated"]
                hits = row["result_hits"] or 0
                result["confidence_calibration"].append({
                    "confidence": row["confidence"] or "unknown",
                    "evaluated": evaluated,
                    "result_hits": hits,
                    "hit_rate": round(hits / evaluated * 100, 1) if evaluated else 0,
                })

        # Per-day breakdown (last 10 days)
        daily = conn.execute(
            "SELECT stat_date, matches_evaluated, result_hit_rate, score_hit_rate, "
            "  total_goals_hit_rate, top_error "
            "FROM daily_stats ORDER BY stat_date DESC LIMIT 10",
        ).fetchall()
        if daily:
            result["daily_breakdown"] = [_row_to_dict(r) for r in daily]

        return result
    finally:
        conn.close()


def _tool_get_daily_briefing(args: dict) -> dict:
    """Get matches scheduled for a given date."""
    date = args["date"]
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT m.match_id, m.kickoff_at, m.phase, m.group_name, m.venue, m.status, "
            "  m.home_team_id, m.away_team_id, m.final_score_home, m.final_score_away, "
            "  h.name_en AS home_name, a.name_en AS away_name "
            "FROM matches m "
            "LEFT JOIN teams h ON m.home_team_id = h.team_id "
            "LEFT JOIN teams a ON m.away_team_id = a.team_id "
            "WHERE DATE(m.kickoff_at) = ? "
            "ORDER BY m.kickoff_at",
            (date,),
        ).fetchall()

        matches = []
        for r in rows:
            match = _row_to_dict(r)
            # Attach prediction if available
            pred = conn.execute(
                "SELECT predicted_result, predicted_score_home, predicted_score_away, "
                "  confidence, divination_hexagram "
                "FROM predictions WHERE match_id = ?",
                (r["match_id"],),
            ).fetchone()
            if pred:
                match["prediction"] = _row_to_dict(pred)
            matches.append(match)

        return {"date": date, "match_count": len(matches), "matches": matches}
    finally:
        conn.close()


def _tool_run_full_pipeline(args: dict) -> dict:
    """Run the complete daily post-match pipeline."""
    date = args["date"]
    # Step 1: Fetch results for the date
    fetch_result = _run_script(
        "fetch_match_results.py",
        ["web", "--edition", EDITION, "--from", date, "--to", date, "--root", str(PROJECT_ROOT)],
        timeout=120,
    )
    if not fetch_result.get("success"):
        return {"success": False, "stage": "fetch_results", "error": fetch_result}

    # Step 2: Run the post-match pipeline (evaluate, tune, dashboard, profiles)
    pipeline_result = _run_script(
        "daily_postmatch_pipeline.py",
        ["--edition", EDITION, "--root", str(PROJECT_ROOT)],
        timeout=600,
    )
    return {
        "success": pipeline_result.get("success", False),
        "date": date,
        "fetch_results": fetch_result,
        "pipeline": pipeline_result,
    }


def _tool_regenerate_dashboard(_args: dict) -> dict:
    """Regenerate the visual prediction dashboard."""
    # Render profiles first, then visual dashboard
    profile_result = _run_script(
        "worldcup_profile_renderer.py",
        ["--edition", EDITION, "--root", str(PROJECT_ROOT)],
        timeout=120,
    )
    dashboard_result = _run_script(
        "prediction_visual_dashboard.py",
        ["write", "--edition", EDITION, "--root", str(PROJECT_ROOT)],
        timeout=180,
    )
    return {
        "success": dashboard_result.get("success", False),
        "profiles": profile_result,
        "dashboard": dashboard_result,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
