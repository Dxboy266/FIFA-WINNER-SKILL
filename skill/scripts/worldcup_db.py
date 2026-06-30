#!/usr/bin/env python3
"""SQLite database helper interface for World Cup predictor."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def get_db_connection(db_path: str | Path) -> sqlite3.Connection:
    """Get sqlite3 connection with foreign keys enabled."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str | Path) -> None:
    """Initialize SQLite tables and indexes."""
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection(db_path)
    try:
        with conn:
            # Create Teams table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    team_id TEXT PRIMARY KEY,
                    code TEXT,
                    name_en TEXT,
                    name_zh TEXT,
                    colors TEXT,
                    glow_color TEXT,
                    stars TEXT,
                    adjective TEXT
                )
            """)

            # Create Players table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    player_id TEXT PRIMARY KEY,
                    team_id TEXT,
                    shirt_number INTEGER,
                    position TEXT,
                    player_name TEXT,
                    name_on_shirt TEXT,
                    club TEXT,
                    dob TEXT,
                    height_cm INTEGER,
                    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_players_team_id ON players(team_id);")

            # Create Matches table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    edition TEXT,
                    phase TEXT,
                    group_name TEXT,
                    kickoff_at TEXT,
                    home_team_id TEXT,
                    away_team_id TEXT,
                    venue TEXT,
                    status TEXT DEFAULT 'fixture_official',
                    final_score_home INTEGER,
                    final_score_away INTEGER,
                    total_goals INTEGER GENERATED ALWAYS AS (
                        COALESCE(final_score_home, 0) + COALESCE(final_score_away, 0)
                    ) STORED,
                    FOREIGN KEY (home_team_id) REFERENCES teams(team_id),
                    FOREIGN KEY (away_team_id) REFERENCES teams(team_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_at);")

            # Create Predictions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    match_id               TEXT PRIMARY KEY,
                    prediction_date        TEXT,
                    prediction_status      TEXT DEFAULT 'locked_pre_match_prediction',
                    predicted_result       TEXT,
                    predicted_score_home   INTEGER,
                    predicted_score_away   INTEGER,
                    predicted_total_goals  INTEGER,
                    goals_line_2_5         TEXT,
                    confidence             TEXT,
                    divination_hexagram    TEXT,
                    evidence_quality       TEXT,
                    has_odds               INTEGER DEFAULT 0,
                    has_referee            INTEGER DEFAULT 0,
                    has_news               INTEGER DEFAULT 0,
                    report_json_path       TEXT,
                    generated_at           TEXT,
                    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS prediction_analysis_layers (
                    match_id      TEXT,
                    layer_id      TEXT,
                    title         TEXT,
                    verdict       TEXT,
                    confidence    TEXT,
                    payload_json  TEXT NOT NULL,
                    generated_at  TEXT,
                    PRIMARY KEY (match_id, layer_id),
                    FOREIGN KEY (match_id) REFERENCES predictions(match_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prediction_analysis_layer_id ON prediction_analysis_layers(layer_id);")

            # Create Evaluations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    match_id               TEXT PRIMARY KEY,
                    evaluation_date        TEXT,
                    actual_score_home      INTEGER,
                    actual_score_away      INTEGER,
                    predicted_score_home   INTEGER,
                    predicted_score_away   INTEGER,
                    predicted_total_goals  INTEGER,
                    actual_total_goals     INTEGER,
                    is_result_correct      INTEGER DEFAULT 0 CHECK(is_result_correct IN (0, 1)),
                    is_score_correct       INTEGER DEFAULT 0 CHECK(is_score_correct IN (0, 1)),
                    is_total_goals_correct INTEGER DEFAULT 0 CHECK(is_total_goals_correct IN (0, 1)),
                    goal_error_home        INTEGER,
                    goal_error_away        INTEGER,
                    goal_error_total       INTEGER,
                    goal_error_margin      INTEGER,
                    headline               TEXT,
                    plain_chinese          TEXT,
                    primary_error          TEXT,
                    model_issue_tags_str   TEXT,
                    error_analysis_json    TEXT,
                    review_json_path       TEXT,
                    evaluated_at           TEXT,
                    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                )
            """)

            # Create Root Causes table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS root_causes (
                    cause_id      TEXT PRIMARY KEY,
                    finding       TEXT NOT NULL,
                    impact        TEXT NOT NULL,
                    category      TEXT DEFAULT 'model',
                    created_at    TEXT DEFAULT (datetime('now')),
                    first_seen_in TEXT
                )
            """)

            # Create Corrective Actions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS corrective_actions (
                    action_id         TEXT PRIMARY KEY,
                    priority          TEXT DEFAULT 'P2',
                    description       TEXT NOT NULL,
                    target_cause_id   TEXT DEFAULT NULL,
                    status            TEXT DEFAULT 'open',
                    created_at        TEXT DEFAULT (datetime('now')),
                    closed_at         TEXT,
                    FOREIGN KEY (target_cause_id) REFERENCES root_causes(cause_id)
                )
            """)

            # Create Match Root Causes mapping table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS match_root_causes (
                    match_id  TEXT NOT NULL,
                    cause_id  TEXT NOT NULL,
                    PRIMARY KEY (match_id, cause_id),
                    FOREIGN KEY (match_id)  REFERENCES matches(match_id) ON DELETE CASCADE,
                    FOREIGN KEY (cause_id)  REFERENCES root_causes(cause_id)
                )
            """)

            # Create Match Actions mapping table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS match_actions (
                    match_id   TEXT NOT NULL,
                    action_id  TEXT NOT NULL,
                    PRIMARY KEY (match_id, action_id),
                    FOREIGN KEY (match_id)  REFERENCES matches(match_id) ON DELETE CASCADE,
                    FOREIGN KEY (action_id) REFERENCES corrective_actions(action_id)
                )
            """)

            # Create Model Issue Tags table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_issue_tags (
                    tag_id          TEXT,
                    match_id        TEXT NOT NULL,
                    tag             TEXT NOT NULL,
                    severity        TEXT DEFAULT 'medium',
                    first_seen_in   TEXT,
                    occurrence_count INTEGER DEFAULT 1,
                    PRIMARY KEY (tag_id, match_id),
                    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                )
            """)

            # Create Daily Stats table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    stat_date                TEXT PRIMARY KEY,
                    matches_evaluated        INTEGER DEFAULT 0,
                    result_hits              INTEGER DEFAULT 0,
                    score_hits               INTEGER DEFAULT 0,
                    total_goals_hits         INTEGER DEFAULT 0,
                    result_hit_rate          REAL DEFAULT 0.0,
                    score_hit_rate           REAL DEFAULT 0.0,
                    total_goals_hit_rate     REAL DEFAULT 0.0,
                    brier_score_result       REAL,
                    brier_score_total_goals  REAL,
                    avg_confidence           REAL,
                    high_confidence_hit_rate REAL,
                    medium_confidence_hit_rate REAL,
                    low_confidence_hit_rate  REAL,
                    top_error                TEXT,
                    updated_at               TEXT DEFAULT (datetime('now'))
                )
            """)

            # Create Injuries table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS injuries (
                    injury_id        TEXT PRIMARY KEY,
                    team_id          TEXT,
                    player_name      TEXT,
                    player_id        TEXT,
                    injury_type      TEXT,
                    reason           TEXT,
                    severity         TEXT,
                    status           TEXT,
                    start_date       TEXT,
                    expected_end_date TEXT,
                    source           TEXT,
                    source_url       TEXT,
                    confidence       TEXT,
                    recorded_at      TEXT,
                    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_injuries_team_id ON injuries(team_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_injuries_status ON injuries(status);")

            # Create Team Form table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS team_form (
                    team_id       TEXT,
                    match_id      TEXT,
                    match_date    TEXT,
                    opponent_id   TEXT,
                    goals_for     INTEGER,
                    goals_against INTEGER,
                    result        TEXT,
                    competition   TEXT DEFAULT 'world_cup_2026',
                    is_home       INTEGER,
                    PRIMARY KEY (team_id, match_id),
                    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE,
                    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_team_form_date ON team_form(match_date);")

            # Create News Sentiment table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS news_sentiment (
                    news_id      TEXT PRIMARY KEY,
                    date         TEXT,
                    team_id      TEXT,
                    headline     TEXT,
                    sentiment    TEXT,
                    impact       TEXT,
                    source       TEXT,
                    source_url   TEXT,
                    recorded_at  TEXT,
                    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE SET NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_sentiment_date ON news_sentiment(date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_sentiment_team ON news_sentiment(team_id);")

            # Create Rankings Snapshot table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rankings_snapshot (
                    team_id       TEXT PRIMARY KEY,
                    rank          INTEGER,
                    points        REAL,
                    confederation TEXT,
                    snapshot_date TEXT,
                    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                )
            """)

            # Create Team Profile Meta table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS team_profile_meta (
                    team_id         TEXT PRIMARY KEY,
                    group_name      TEXT,
                    head_coach      TEXT,
                    formation       TEXT,
                    wc_appearances  INTEGER,
                    wc_best_result  TEXT,
                    last_updated    TEXT,
                    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                )
            """)

            # Create Prediction Lessons table (long-term memory / experience loop)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prediction_lessons (
                    lesson_id TEXT PRIMARY KEY,
                    match_id TEXT,
                    team_id TEXT,
                    lesson_type TEXT,
                    summary TEXT,
                    detail TEXT,
                    confidence_adjustment REAL DEFAULT 0.0,
                    applicable_until TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    applied_count INTEGER DEFAULT 0,
                    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prediction_lessons_team_id ON prediction_lessons(team_id);")

            conn.commit()
    finally:
        conn.close()


def save_team(conn: sqlite3.Connection, team: dict) -> None:
    """Upsert a team record."""
    conn.execute("""
        INSERT INTO teams (team_id, code, name_en, name_zh, colors, glow_color, stars, adjective)
        VALUES (:team_id, :code, :name_en, :name_zh, :colors, :glow_color, :stars, :adjective)
        ON CONFLICT(team_id) DO UPDATE SET
            code = excluded.code,
            name_en = excluded.name_en,
            name_zh = excluded.name_zh,
            colors = COALESCE(excluded.colors, colors),
            glow_color = COALESCE(excluded.glow_color, glow_color),
            stars = COALESCE(excluded.stars, stars),
            adjective = COALESCE(excluded.adjective, adjective)
    """, {
        "team_id": str(team["team_id"]).lower(),
        "code": team.get("code") or team.get("team_id", "").upper(),
        "name_en": team.get("name_en") or team.get("name") or "Unknown Team",
        "name_zh": team.get("name_zh") or team.get("name") or "Unknown Team",
        "colors": team.get("colors"),
        "glow_color": team.get("glow_color"),
        "stars": team.get("stars"),
        "adjective": team.get("adjective")
    })


def save_player(conn: sqlite3.Connection, player: dict) -> None:
    """Upsert a player record."""
    conn.execute("""
        INSERT INTO players (player_id, team_id, shirt_number, position, player_name, name_on_shirt, club, dob, height_cm)
        VALUES (:player_id, :team_id, :shirt_number, :position, :player_name, :name_on_shirt, :club, :dob, :height_cm)
        ON CONFLICT(player_id) DO UPDATE SET
            team_id = excluded.team_id,
            shirt_number = excluded.shirt_number,
            position = excluded.position,
            player_name = excluded.player_name,
            name_on_shirt = excluded.name_on_shirt,
            club = excluded.club,
            dob = excluded.dob,
            height_cm = excluded.height_cm
    """, {
        "player_id": str(player["player_id"]).lower(),
        "team_id": str(player["team_id"]).lower(),
        "shirt_number": player.get("shirt_number"),
        "position": player.get("position"),
        "player_name": player.get("player_name"),
        "name_on_shirt": player.get("name_on_shirt"),
        "club": player.get("club"),
        "dob": player.get("dob"),
        "height_cm": player.get("height_cm")
    })


def save_match(conn: sqlite3.Connection, match: dict) -> None:
    """Upsert a match record."""
    home_id = match.get("home_team_id") or ""
    away_id = match.get("away_team_id") or ""

    if not home_id and isinstance(match.get("home_team"), dict):
        home_id = match["home_team"].get("team_id") or ""
    if not away_id and isinstance(match.get("away_team"), dict):
        away_id = match["away_team"].get("team_id") or ""

    home_id = str(home_id).lower()
    away_id = str(away_id).lower()

    for tid, tname in [(home_id, match.get("home_team")), (away_id, match.get("away_team"))]:
        if not tid:
            continue
        cur = conn.execute("SELECT 1 FROM teams WHERE team_id = ?", (tid,))
        if not cur.fetchone():
            name_str = tname if isinstance(tname, str) else (tname.get("name") if isinstance(tname, dict) else tid.upper())
            save_team(conn, {"team_id": tid, "name_en": name_str, "name_zh": name_str})

    final_score = match.get("final_score") or {}
    fs_home = final_score.get("home") if final_score else None
    fs_away = final_score.get("away") if final_score else None

    conn.execute("""
        INSERT INTO matches (match_id, edition, phase, group_name, kickoff_at, home_team_id, away_team_id, venue, status, final_score_home, final_score_away)
        VALUES (:match_id, :edition, :phase, :group_name, :kickoff_at, :home_team_id, :away_team_id, :venue, :status, :final_score_home, :final_score_away)
        ON CONFLICT(match_id) DO UPDATE SET
            edition = excluded.edition,
            phase = excluded.phase,
            group_name = excluded.group_name,
            kickoff_at = excluded.kickoff_at,
            home_team_id = excluded.home_team_id,
            away_team_id = excluded.away_team_id,
            venue = excluded.venue,
            status = excluded.status,
            final_score_home = COALESCE(excluded.final_score_home, final_score_home),
            final_score_away = COALESCE(excluded.final_score_away, final_score_away)
    """, {
        "match_id": match["match_id"],
        "edition": match.get("edition") or "2026",
        "phase": match.get("phase") or "group",
        "group_name": match.get("group") or match.get("group_name") or "",
        "kickoff_at": match.get("kickoff_at"),
        "home_team_id": home_id or None,
        "away_team_id": away_id or None,
        "venue": match.get("venue"),
        "status": match.get("status") or "fixture_official",
        "final_score_home": fs_home,
        "final_score_away": fs_away
    })


def save_prediction(conn: sqlite3.Connection, prediction: dict) -> None:
    """Upsert a prediction record with rich evidence metadata."""
    pred_info = prediction.get("prediction") or {}
    score = pred_info.get("score") or {}
    divination = prediction.get("divination_overlay") or {}

    predicted_total_goals = pred_info.get("total_goals")
    goals_line_2_5 = pred_info.get("goals_line_2_5")
    evidence_quality = pred_info.get("evidence_quality") or prediction.get("evidence_quality")
    
    market_status = prediction.get("market_odds_status") or {}
    has_usable_market_odds = bool(prediction.get("market_odds")) and not market_status.get("is_mock")
    has_odds = 1 if (has_usable_market_odds or pred_info.get("has_odds")) else 0
    has_referee = 1 if (prediction.get("referee_analysis") or pred_info.get("has_referee")) else 0
    has_news = 1 if (prediction.get("daily_evidence") or pred_info.get("has_news") or prediction.get("late_news")) else 0
    report_json_path = prediction.get("report_json_path") or prediction.get("prediction_report")

    conn.execute("""
        INSERT INTO predictions (
            match_id, prediction_date, prediction_status, predicted_result, 
            predicted_score_home, predicted_score_away, predicted_total_goals, 
            goals_line_2_5, confidence, divination_hexagram, evidence_quality, 
            has_odds, has_referee, has_news, report_json_path, generated_at
        )
        VALUES (
            :match_id, :prediction_date, :prediction_status, :predicted_result, 
            :predicted_score_home, :predicted_score_away, :predicted_total_goals, 
            :goals_line_2_5, :confidence, :divination_hexagram, :evidence_quality, 
            :has_odds, :has_referee, :has_news, :report_json_path, :generated_at
        )
        ON CONFLICT(match_id) DO UPDATE SET
            prediction_date = excluded.prediction_date,
            prediction_status = excluded.prediction_status,
            predicted_result = excluded.predicted_result,
            predicted_score_home = excluded.predicted_score_home,
            predicted_score_away = excluded.predicted_score_away,
            predicted_total_goals = excluded.predicted_total_goals,
            goals_line_2_5 = excluded.goals_line_2_5,
            confidence = excluded.confidence,
            divination_hexagram = excluded.divination_hexagram,
            evidence_quality = excluded.evidence_quality,
            has_odds = excluded.has_odds,
            has_referee = excluded.has_referee,
            has_news = excluded.has_news,
            report_json_path = excluded.report_json_path,
            generated_at = excluded.generated_at
    """, {
        "match_id": prediction["match_id"],
        "prediction_date": prediction.get("prediction_date") or prediction.get("generated_at", "")[:10],
        "prediction_status": prediction.get("status") or "locked_pre_match_prediction",
        "predicted_result": pred_info.get("result") or pred_info.get("predicted_outcome"),
        "predicted_score_home": score.get("home"),
        "predicted_score_away": score.get("away"),
        "predicted_total_goals": predicted_total_goals,
        "goals_line_2_5": goals_line_2_5,
        "confidence": pred_info.get("confidence"),
        "divination_hexagram": divination.get("hexagram"),
        "evidence_quality": evidence_quality,
        "has_odds": has_odds,
        "has_referee": has_referee,
        "has_news": has_news,
        "report_json_path": report_json_path,
        "generated_at": prediction.get("generated_at")
    })


def save_prediction_analysis_layers(conn: sqlite3.Connection, prediction: dict) -> None:
    """Store explainability layers as a query index while keeping JSON reports canonical."""
    match_id = prediction["match_id"]
    generated_at = prediction.get("generated_at")
    for layer in prediction.get("analysis_layers", []) or []:
        layer_id = str(layer.get("layer_id") or "")
        if not layer_id:
            continue
        conn.execute("""
            INSERT INTO prediction_analysis_layers
                (match_id, layer_id, title, verdict, confidence, payload_json, generated_at)
            VALUES
                (:match_id, :layer_id, :title, :verdict, :confidence, :payload_json, :generated_at)
            ON CONFLICT(match_id, layer_id) DO UPDATE SET
                title = excluded.title,
                verdict = excluded.verdict,
                confidence = excluded.confidence,
                payload_json = excluded.payload_json,
                generated_at = excluded.generated_at
        """, {
            "match_id": match_id,
            "layer_id": layer_id,
            "title": layer.get("title"),
            "verdict": layer.get("verdict"),
            "confidence": layer.get("confidence"),
            "payload_json": json.dumps(layer, ensure_ascii=False, sort_keys=True),
            "generated_at": generated_at,
        })


def save_evaluation(conn: sqlite3.Connection, evaluation: dict) -> None:
    """Upsert an evaluation record and automatically sync issue tags and stats."""
    match_id = evaluation["match_id"]
    
    cursor = conn.execute("""
        SELECT predicted_score_home, predicted_score_away, confidence
        FROM predictions WHERE match_id = ?
    """, (match_id,))
    pred_row = cursor.fetchone()
    
    pred_home = evaluation.get("predicted_score_home")
    pred_away = evaluation.get("predicted_score_away")
    pred_total = evaluation.get("predicted_total_goals")
    
    if pred_row:
        if pred_home is None:
            pred_home = pred_row["predicted_score_home"]
        if pred_away is None:
            pred_away = pred_row["predicted_score_away"]
            
    actual_home = evaluation["actual_score_home"]
    actual_away = evaluation["actual_score_away"]
    actual_total = actual_home + actual_away
    
    if pred_home is not None and pred_away is not None:
        pred_total = pred_home + pred_away
        is_score_correct = 1 if (pred_home == actual_home and pred_away == actual_away) else 0
        goal_error_home = actual_home - pred_home
        goal_error_away = actual_away - pred_away
        goal_error_total = actual_total - pred_total
        goal_error_margin = abs(goal_error_home) + abs(goal_error_away)
    else:
        is_score_correct = evaluation.get("is_score_correct", 0)
        goal_error_home = None
        goal_error_away = None
        goal_error_total = None
        goal_error_margin = None
        
    is_result_correct = evaluation.get("is_result_correct", 0)
    is_total_goals_correct = 1 if (pred_total == actual_total) else 0 if pred_total is not None else 0
    
    primary_error = evaluation.get("primary_error") or ""
    model_issue_tags_str = evaluation.get("model_issue_tags_str") or ""
    
    error_analysis_json = evaluation.get("error_analysis_json")
    if isinstance(error_analysis_json, dict):
        error_analysis_json = json.dumps(error_analysis_json, ensure_ascii=False)
        
    conn.execute("""
        INSERT INTO evaluations (
            match_id, evaluation_date, actual_score_home, actual_score_away,
            predicted_score_home, predicted_score_away, predicted_total_goals, actual_total_goals,
            is_result_correct, is_score_correct, is_total_goals_correct,
            goal_error_home, goal_error_away, goal_error_total, goal_error_margin,
            headline, plain_chinese, primary_error, model_issue_tags_str,
            error_analysis_json, review_json_path, evaluated_at
        )
        VALUES (
            :match_id, :evaluation_date, :actual_score_home, :actual_score_away,
            :predicted_score_home, :predicted_score_away, :predicted_total_goals, :actual_total_goals,
            :is_result_correct, :is_score_correct, :is_total_goals_correct,
            :goal_error_home, :goal_error_away, :goal_error_total, :goal_error_margin,
            :headline, :plain_chinese, :primary_error, :model_issue_tags_str,
            :error_analysis_json, :review_json_path, :evaluated_at
        )
        ON CONFLICT(match_id) DO UPDATE SET
            evaluation_date = COALESCE(excluded.evaluation_date, evaluation_date),
            actual_score_home = excluded.actual_score_home,
            actual_score_away = excluded.actual_score_away,
            predicted_score_home = COALESCE(excluded.predicted_score_home, predicted_score_home),
            predicted_score_away = COALESCE(excluded.predicted_score_away, predicted_score_away),
            predicted_total_goals = COALESCE(excluded.predicted_total_goals, predicted_total_goals),
            actual_total_goals = excluded.actual_total_goals,
            is_result_correct = excluded.is_result_correct,
            is_score_correct = excluded.is_score_correct,
            is_total_goals_correct = excluded.is_total_goals_correct,
            goal_error_home = COALESCE(excluded.goal_error_home, goal_error_home),
            goal_error_away = COALESCE(excluded.goal_error_away, goal_error_away),
            goal_error_total = COALESCE(excluded.goal_error_total, goal_error_total),
            goal_error_margin = COALESCE(excluded.goal_error_margin, goal_error_margin),
            headline = COALESCE(excluded.headline, headline),
            plain_chinese = COALESCE(excluded.plain_chinese, plain_chinese),
            primary_error = COALESCE(excluded.primary_error, primary_error),
            model_issue_tags_str = COALESCE(excluded.model_issue_tags_str, model_issue_tags_str),
            error_analysis_json = COALESCE(excluded.error_analysis_json, error_analysis_json),
            review_json_path = COALESCE(excluded.review_json_path, review_json_path),
            evaluated_at = excluded.evaluated_at
    """, {
        "match_id": match_id,
        "evaluation_date": evaluation.get("evaluation_date") or evaluation.get("evaluated_at", "")[:10],
        "actual_score_home": actual_home,
        "actual_score_away": actual_away,
        "predicted_score_home": pred_home,
        "predicted_score_away": pred_away,
        "predicted_total_goals": pred_total,
        "actual_total_goals": actual_total,
        "is_result_correct": 1 if is_result_correct else 0,
        "is_score_correct": 1 if is_score_correct else 0,
        "is_total_goals_correct": is_total_goals_correct,
        "goal_error_home": goal_error_home,
        "goal_error_away": goal_error_away,
        "goal_error_total": goal_error_total,
        "goal_error_margin": goal_error_margin,
        "headline": evaluation.get("headline"),
        "plain_chinese": evaluation.get("plain_chinese"),
        "primary_error": primary_error,
        "model_issue_tags_str": model_issue_tags_str,
        "error_analysis_json": error_analysis_json,
        "review_json_path": evaluation.get("review_json_path"),
        "evaluated_at": evaluation.get("evaluated_at")
    })

    # If tags are provided, split and save to model_issue_tags
    if model_issue_tags_str:
        tags = [t.strip() for t in model_issue_tags_str.split(",") if t.strip()]
        for tag in tags:
            tag_id = f"{match_id}_{tag}"
            conn.execute("""
                INSERT INTO model_issue_tags (tag_id, match_id, tag, severity, first_seen_in, occurrence_count)
                VALUES (:tag_id, :match_id, :tag, 'medium', :first_seen_in, 1)
                ON CONFLICT(tag_id, match_id) DO UPDATE SET
                    occurrence_count = occurrence_count + 1
            """, {
                "tag_id": tag_id,
                "match_id": match_id,
                "tag": tag,
                "first_seen_in": match_id
            })

    # Rebuild daily stats for the day
    stat_date = evaluation.get("evaluation_date") or evaluation.get("evaluated_at", "")[:10]
    if stat_date:
        rebuild_daily_stats(conn, stat_date)


def rebuild_daily_stats(conn: sqlite3.Connection, stat_date: str) -> None:
    """Aggregate evaluations and predictions on a given date to rebuild daily stats."""
    cursor = conn.execute("""
        SELECT 
            COUNT(e.match_id) as matches_evaluated,
            SUM(e.is_result_correct) as result_hits,
            SUM(e.is_score_correct) as score_hits,
            SUM(e.is_total_goals_correct) as total_goals_hits
        FROM evaluations e
        WHERE e.evaluation_date = ? OR SUBSTR(e.evaluated_at, 1, 10) = ?
    """, (stat_date, stat_date))
    row = cursor.fetchone()
    
    if not row or row["matches_evaluated"] == 0:
        return
        
    matches_evaluated = row["matches_evaluated"]
    result_hits = row["result_hits"] or 0
    score_hits = row["score_hits"] or 0
    total_goals_hits = row["total_goals_hits"] or 0
    
    result_hit_rate = (result_hits / matches_evaluated) * 100.0
    score_hit_rate = (score_hits / matches_evaluated) * 100.0
    total_goals_hit_rate = (total_goals_hits / matches_evaluated) * 100.0
    
    cursor2 = conn.execute("""
        SELECT e.is_result_correct, p.confidence
        FROM evaluations e
        JOIN predictions p ON e.match_id = p.match_id
        WHERE e.evaluation_date = ? OR SUBSTR(e.evaluated_at, 1, 10) = ?
    """, (stat_date, stat_date))
    rows2 = cursor2.fetchall()
    
    brier_sum = 0.0
    conf_sum = 0.0
    conf_map = {"high": 0.75, "medium": 0.60, "low": 0.45, "unknown": 0.50}
    
    high_hits, high_total = 0, 0
    med_hits, med_total = 0, 0
    low_hits, low_total = 0, 0
    
    for r in rows2:
        conf_str = (r["confidence"] or "medium").lower()
        prob = conf_map.get(conf_str, 0.60)
        conf_sum += prob
        
        hit = r["is_result_correct"]
        brier_sum += (prob - float(hit)) ** 2
        
        if conf_str == "high":
            high_total += 1
            if hit: high_hits += 1
        elif conf_str == "medium":
            med_total += 1
            if hit: med_hits += 1
        elif conf_str == "low":
            low_total += 1
            if hit: low_hits += 1
            
    brier_score_result = brier_sum / len(rows2) if rows2 else None
    avg_confidence = conf_sum / len(rows2) if rows2 else None
    
    high_confidence_hit_rate = (high_hits / high_total) * 100.0 if high_total else None
    medium_confidence_hit_rate = (med_hits / med_total) * 100.0 if med_total else None
    low_confidence_hit_rate = (low_hits / low_total) * 100.0 if low_total else None
    
    # Get top primary error (if any)
    cursor3 = conn.execute("""
        SELECT primary_error, COUNT(primary_error) as err_count
        FROM evaluations
        WHERE (evaluation_date = ? OR SUBSTR(evaluated_at, 1, 10) = ?) AND primary_error IS NOT NULL AND primary_error != ''
        GROUP BY primary_error
        ORDER BY err_count DESC
        LIMIT 1
    """, (stat_date, stat_date))
    err_row = cursor3.fetchone()
    top_error = err_row["primary_error"] if err_row else None
    
    conn.execute("""
        INSERT INTO daily_stats (
            stat_date, matches_evaluated, result_hits, score_hits, total_goals_hits,
            result_hit_rate, score_hit_rate, total_goals_hit_rate,
            brier_score_result, brier_score_total_goals, avg_confidence,
            high_confidence_hit_rate, medium_confidence_hit_rate, low_confidence_hit_rate,
            top_error, updated_at
        )
        VALUES (
            :stat_date, :matches_evaluated, :result_hits, :score_hits, :total_goals_hits,
            :result_hit_rate, :score_hit_rate, :total_goals_hit_rate,
            :brier_score_result, NULL, :avg_confidence,
            :high_confidence_hit_rate, :medium_confidence_hit_rate, :low_confidence_hit_rate,
            :top_error, datetime('now')
        )
        ON CONFLICT(stat_date) DO UPDATE SET
            matches_evaluated = excluded.matches_evaluated,
            result_hits = excluded.result_hits,
            score_hits = excluded.score_hits,
            total_goals_hits = excluded.total_goals_hits,
            result_hit_rate = excluded.result_hit_rate,
            score_hit_rate = excluded.score_hit_rate,
            total_goals_hit_rate = excluded.total_goals_hit_rate,
            brier_score_result = excluded.brier_score_result,
            avg_confidence = excluded.avg_confidence,
            high_confidence_hit_rate = excluded.high_confidence_hit_rate,
            medium_confidence_hit_rate = excluded.medium_confidence_hit_rate,
            low_confidence_hit_rate = excluded.low_confidence_hit_rate,
            top_error = excluded.top_error,
            updated_at = excluded.updated_at
    """, {
        "stat_date": stat_date,
        "matches_evaluated": matches_evaluated,
        "result_hits": result_hits,
        "score_hits": score_hits,
        "total_goals_hits": total_goals_hits,
        "result_hit_rate": result_hit_rate,
        "score_hit_rate": score_hit_rate,
        "total_goals_hit_rate": total_goals_hit_rate,
        "brier_score_result": brier_score_result,
        "avg_confidence": avg_confidence,
        "high_confidence_hit_rate": high_confidence_hit_rate,
        "medium_confidence_hit_rate": medium_confidence_hit_rate,
        "low_confidence_hit_rate": low_confidence_hit_rate,
        "top_error": top_error
    })


def save_corrective_action(conn: sqlite3.Connection, action: dict) -> None:
    """Upsert a corrective action."""
    conn.execute("""
        INSERT INTO corrective_actions (action_id, priority, description, target_cause_id, status, created_at, closed_at)
        VALUES (:action_id, :priority, :description, :target_cause_id, :status, :created_at, :closed_at)
        ON CONFLICT(action_id) DO UPDATE SET
            priority = excluded.priority,
            description = excluded.description,
            target_cause_id = excluded.target_cause_id,
            status = excluded.status,
            closed_at = excluded.closed_at
    """, {
        "action_id": action["action_id"],
        "priority": action.get("priority", "P2"),
        "description": action["description"],
        "target_cause_id": action.get("target_cause_id"),
        "status": action.get("status", "open"),
        "created_at": action.get("created_at"),
        "closed_at": action.get("closed_at")
    })


def save_model_issue_tag(conn: sqlite3.Connection, tag: dict) -> None:
    """Upsert a model issue tag."""
    conn.execute("""
        INSERT INTO model_issue_tags (tag_id, match_id, tag, severity, first_seen_in, occurrence_count)
        VALUES (:tag_id, :match_id, :tag, :severity, :first_seen_in, :occurrence_count)
        ON CONFLICT(tag_id, match_id) DO UPDATE SET
            severity = excluded.severity,
            occurrence_count = excluded.occurrence_count
    """, {
        "tag_id": tag.get("tag_id") or f"{tag['match_id']}_{tag['tag']}",
        "match_id": tag["match_id"],
        "tag": tag["tag"],
        "severity": tag.get("severity", "medium"),
        "first_seen_in": tag.get("first_seen_in") or tag["match_id"],
        "occurrence_count": tag.get("occurrence_count", 1)
    })


def save_root_cause(conn: sqlite3.Connection, cause: dict) -> None:
    """Upsert a root cause."""
    conn.execute("""
        INSERT INTO root_causes (cause_id, finding, impact, category, created_at, first_seen_in)
        VALUES (:cause_id, :finding, :impact, :category, :created_at, :first_seen_in)
        ON CONFLICT(cause_id) DO UPDATE SET
            finding = excluded.finding,
            impact = excluded.impact,
            category = excluded.category,
            first_seen_in = excluded.first_seen_in
    """, {
        "cause_id": cause["cause_id"],
        "finding": cause["finding"],
        "impact": cause["impact"],
        "category": cause.get("category", "model"),
        "created_at": cause.get("created_at"),
        "first_seen_in": cause.get("first_seen_in")
    })


def save_match_root_cause(conn: sqlite3.Connection, match_id: str, cause_id: str) -> None:
    """Link a match to a root cause."""
    conn.execute("""
        INSERT OR IGNORE INTO match_root_causes (match_id, cause_id)
        VALUES (?, ?)
    """, (match_id, cause_id))


def save_match_action(conn: sqlite3.Connection, match_id: str, action_id: str) -> None:
    """Link a match to a corrective action."""
    conn.execute("""
        INSERT OR IGNORE INTO match_actions (match_id, action_id)
        VALUES (?, ?)
    """, (match_id, action_id))


def save_injury(conn: sqlite3.Connection, injury: dict) -> None:
    """Upsert an injury/suspension record."""
    conn.execute("""
        INSERT INTO injuries (
            injury_id, team_id, player_name, player_id, injury_type, reason,
            severity, status, start_date, expected_end_date,
            source, source_url, confidence, recorded_at
        )
        VALUES (
            :injury_id, :team_id, :player_name, :player_id, :injury_type, :reason,
            :severity, :status, :start_date, :expected_end_date,
            :source, :source_url, :confidence, :recorded_at
        )
        ON CONFLICT(injury_id) DO UPDATE SET
            player_name = COALESCE(excluded.player_name, injuries.player_name),
            severity = COALESCE(excluded.severity, injuries.severity),
            status = excluded.status,
            expected_end_date = COALESCE(excluded.expected_end_date, injuries.expected_end_date),
            source = excluded.source,
            source_url = excluded.source_url,
            confidence = excluded.confidence,
            recorded_at = excluded.recorded_at
    """, {
        "injury_id": injury["injury_id"],
        "team_id": injury.get("team_id"),
        "player_name": injury.get("player_name"),
        "player_id": injury.get("player_id"),
        "injury_type": injury.get("injury_type", "injury"),
        "reason": injury.get("reason"),
        "severity": injury.get("severity"),
        "status": injury.get("status", "active"),
        "start_date": injury.get("start_date"),
        "expected_end_date": injury.get("expected_end_date"),
        "source": injury.get("source"),
        "source_url": injury.get("source_url"),
        "confidence": injury.get("confidence", "high"),
        "recorded_at": injury.get("recorded_at"),
    })


def save_team_form(conn: sqlite3.Connection, form: dict) -> None:
    """Upsert a team form record (one per team per match)."""
    conn.execute("""
        INSERT INTO team_form (
            team_id, match_id, match_date, opponent_id,
            goals_for, goals_against, result, competition, is_home
        )
        VALUES (
            :team_id, :match_id, :match_date, :opponent_id,
            :goals_for, :goals_against, :result, :competition, :is_home
        )
        ON CONFLICT(team_id, match_id) DO UPDATE SET
            match_date = excluded.match_date,
            opponent_id = excluded.opponent_id,
            goals_for = excluded.goals_for,
            goals_against = excluded.goals_against,
            result = excluded.result,
            competition = excluded.competition,
            is_home = excluded.is_home
    """, {
        "team_id": form["team_id"],
        "match_id": form["match_id"],
        "match_date": form.get("match_date"),
        "opponent_id": form.get("opponent_id"),
        "goals_for": form.get("goals_for"),
        "goals_against": form.get("goals_against"),
        "result": form.get("result"),
        "competition": form.get("competition", "world_cup_2026"),
        "is_home": form.get("is_home", 1),
    })


def save_news_sentiment(conn: sqlite3.Connection, news: dict) -> None:
    """Upsert a news sentiment record."""
    conn.execute("""
        INSERT INTO news_sentiment (
            news_id, date, team_id, headline, sentiment, impact,
            source, source_url, recorded_at
        )
        VALUES (
            :news_id, :date, :team_id, :headline, :sentiment, :impact,
            :source, :source_url, :recorded_at
        )
        ON CONFLICT(news_id) DO UPDATE SET
            headline = excluded.headline,
            sentiment = excluded.sentiment,
            impact = excluded.impact,
            source = excluded.source,
            source_url = excluded.source_url,
            recorded_at = excluded.recorded_at
    """, {
        "news_id": news["news_id"],
        "date": news.get("date"),
        "team_id": news.get("team_id"),
        "headline": news.get("headline"),
        "sentiment": news.get("sentiment", "neutral"),
        "impact": news.get("impact"),
        "source": news.get("source"),
        "source_url": news.get("source_url"),
        "recorded_at": news.get("recorded_at"),
    })


def save_rankings_snapshot(conn: sqlite3.Connection, ranking: dict) -> None:
    """Upsert a FIFA rankings snapshot record."""
    conn.execute("""
        INSERT INTO rankings_snapshot (team_id, rank, points, confederation, snapshot_date)
        VALUES (:team_id, :rank, :points, :confederation, :snapshot_date)
        ON CONFLICT(team_id) DO UPDATE SET
            rank = excluded.rank,
            points = excluded.points,
            confederation = excluded.confederation,
            snapshot_date = excluded.snapshot_date
    """, {
        "team_id": ranking["team_id"],
        "rank": ranking.get("rank"),
        "points": ranking.get("points"),
        "confederation": ranking.get("confederation"),
        "snapshot_date": ranking.get("snapshot_date"),
    })


def save_team_profile_meta(conn: sqlite3.Connection, meta: dict) -> None:
    """Upsert team profile metadata."""
    conn.execute("""
        INSERT INTO team_profile_meta (
            team_id, group_name, head_coach, formation,
            wc_appearances, wc_best_result, last_updated
        )
        VALUES (
            :team_id, :group_name, :head_coach, :formation,
            :wc_appearances, :wc_best_result, :last_updated
        )
        ON CONFLICT(team_id) DO UPDATE SET
            group_name = COALESCE(excluded.group_name, team_profile_meta.group_name),
            head_coach = COALESCE(excluded.head_coach, team_profile_meta.head_coach),
            formation = COALESCE(excluded.formation, team_profile_meta.formation),
            wc_appearances = COALESCE(excluded.wc_appearances, team_profile_meta.wc_appearances),
            wc_best_result = COALESCE(excluded.wc_best_result, team_profile_meta.wc_best_result),
            last_updated = excluded.last_updated
    """, {
        "team_id": meta["team_id"],
        "group_name": meta.get("group_name"),
        "head_coach": meta.get("head_coach"),
        "formation": meta.get("formation"),
        "wc_appearances": meta.get("wc_appearances"),
        "wc_best_result": meta.get("wc_best_result"),
        "last_updated": meta.get("last_updated"),
    })


def save_lesson(conn: sqlite3.Connection, lesson: dict) -> None:
    """Upsert a prediction lesson."""
    conn.execute("""
        INSERT INTO prediction_lessons (
            lesson_id, match_id, team_id, lesson_type, summary, detail,
            confidence_adjustment, applicable_until, created_at, applied_count
        )
        VALUES (
            :lesson_id, :match_id, :team_id, :lesson_type, :summary, :detail,
            :confidence_adjustment, :applicable_until, :created_at, :applied_count
        )
        ON CONFLICT(lesson_id) DO UPDATE SET
            match_id = excluded.match_id,
            team_id = excluded.team_id,
            lesson_type = excluded.lesson_type,
            summary = excluded.summary,
            detail = excluded.detail,
            confidence_adjustment = excluded.confidence_adjustment,
            applicable_until = excluded.applicable_until,
            created_at = excluded.created_at,
            applied_count = excluded.applied_count
    """, {
        "lesson_id": lesson["lesson_id"],
        "match_id": lesson.get("match_id"),
        "team_id": lesson.get("team_id"),
        "lesson_type": lesson.get("lesson_type"),
        "summary": lesson.get("summary"),
        "detail": lesson.get("detail"),
        "confidence_adjustment": lesson.get("confidence_adjustment", 0.0),
        "applicable_until": lesson.get("applicable_until"),
        "created_at": lesson.get("created_at"),
        "applied_count": lesson.get("applied_count", 0),
    })


def query_lessons(conn: sqlite3.Connection, team_id: str = None, lesson_type: str = None, limit: int = 10) -> list[dict]:
    """Query relevant lessons, excluding expired ones."""
    conditions = ["(applicable_until IS NULL OR applicable_until > date('now'))"]
    params: dict = {}

    if team_id:
        conditions.append("team_id = :team_id")
        params["team_id"] = team_id
    if lesson_type:
        conditions.append("lesson_type = :lesson_type")
        params["lesson_type"] = lesson_type

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT lesson_id, match_id, team_id, lesson_type, summary, detail,
               confidence_adjustment, applicable_until, created_at, applied_count
        FROM prediction_lessons
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit
    """
    params["limit"] = limit
    cursor = conn.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]
