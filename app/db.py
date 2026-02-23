import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "app.db"


@contextmanager
def get_conn(db_path: Path | None = None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None):
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY,
                session_token TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS integration_tokens (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                access_token TEXT,
                refresh_token TEXT,
                expires_at INTEGER,
                metadata_json TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, provider),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS workout_library (
                id INTEGER PRIMARY KEY,
                discipline TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                tss INTEGER NOT NULL,
                intensity REAL NOT NULL,
                workout_type TEXT NOT NULL,
                zwo_content TEXT
            );

            CREATE TABLE IF NOT EXISTS plan_entries (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                discipline TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                duration_minutes INTEGER NOT NULL,
                tss INTEGER NOT NULL,
                intensity REAL NOT NULL,
                workout_type TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'planner',
                status TEXT NOT NULL DEFAULT 'planned',
                linked_workout_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(linked_workout_id) REFERENCES workout_library(id)
            );

            CREATE TABLE IF NOT EXISTS completed_workouts (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                external_id TEXT,
                discipline TEXT NOT NULL,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                tss INTEGER NOT NULL,
                intensity REAL NOT NULL,
                rpe REAL,
                hr_avg REAL,
                power_avg REAL,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(provider, external_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_plan_entries_date ON plan_entries(user_id, date);
            CREATE INDEX IF NOT EXISTS idx_completed_started ON completed_workouts(user_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, expires_at);
            """
        )

        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO users(id, name, created_at) VALUES(1, 'Athlete', ?)",
            (now,),
        )

        count = conn.execute("SELECT COUNT(*) AS c FROM workout_library").fetchone()["c"]
        if count == 0:
            seed_workout_library(conn)


def seed_workout_library(conn: sqlite3.Connection):
    rows = [
        # Cycling
        ("cycling", "Endurance 60", "Steady Z2 ride", 60, 45, 0.62, "endurance"),
        ("cycling", "Tempo 75", "Sustained tempo intervals", 75, 68, 0.76, "tempo"),
        ("cycling", "Sweet Spot 90", "3x12 sweet spot", 90, 90, 0.86, "threshold"),
        ("cycling", "VO2 60", "5x3 VO2 max", 60, 85, 0.94, "vo2"),
        ("cycling", "Recovery Spin 45", "Very easy spin", 45, 22, 0.5, "recovery"),
        # Running
        ("running", "Easy Run 45", "Easy aerobic run", 45, 48, 0.68, "endurance"),
        ("running", "Tempo Run 50", "2x15 tempo run", 50, 67, 0.81, "tempo"),
        ("running", "Threshold Run 60", "4x8 threshold", 60, 80, 0.88, "threshold"),
        ("running", "Intervals 45", "8x400m hard", 45, 72, 0.93, "vo2"),
        ("running", "Recovery Jog 30", "Easy recovery jog", 30, 20, 0.52, "recovery"),
        # Triathlon (combo sessions)
        ("triathlon", "Brick Easy", "Bike 45 + Run 20", 65, 58, 0.72, "brick"),
        ("triathlon", "Brick Tempo", "Bike tempo + short transition run", 80, 78, 0.83, "brick"),
        ("triathlon", "Long Tri Day", "Long bike + endurance run", 120, 110, 0.75, "endurance"),
        ("triathlon", "Tri Threshold", "Bike threshold + short run", 75, 88, 0.9, "threshold"),
        ("triathlon", "Tri Recovery", "Light aerobic multisport", 50, 28, 0.55, "recovery"),
    ]

    for discipline, name, desc, dur, tss, intensity, wtype in rows:
        zwo = build_basic_zwo(name, desc, dur, intensity) if discipline in {"cycling", "triathlon"} else None
        conn.execute(
            """
            INSERT INTO workout_library(
                discipline, name, description, duration_minutes, tss, intensity, workout_type, zwo_content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (discipline, name, desc, dur, tss, intensity, wtype, zwo),
        )


def build_basic_zwo(name: str, description: str, duration_minutes: int, intensity: float) -> str:
    warmup = 600
    cooldown = 300
    steady = max(duration_minutes * 60 - warmup - cooldown, 300)
    return f"""<workout_file><name>{name}</name><description>{description}</description><sportType>bike</sportType><workout><Warmup Duration=\"{warmup}\" PowerLow=\"0.5\" PowerHigh=\"{max(0.55, intensity - 0.15):.2f}\"/><SteadyState Duration=\"{steady}\" Power=\"{intensity:.2f}\"/><Cooldown Duration=\"{cooldown}\" PowerLow=\"{max(0.4, intensity - 0.2):.2f}\" PowerHigh=\"0.45\"/></workout></workout_file>"""


def row_to_dict(row: sqlite3.Row):
    out = dict(row)
    if "raw_json" in out and out["raw_json"]:
        try:
            out["raw_json"] = json.loads(out["raw_json"])
        except json.JSONDecodeError:
            pass
    return out
