#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http import cookies
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.auth import (
    hash_password,
    new_session_token,
    session_expiry,
    verify_password,
)
from app.db import DB_PATH, get_conn, init_db, row_to_dict
from app.integrations import (
    build_generic_authorize_url,
    build_strava_authorize_url,
    exchange_oauth_code,
    exchange_strava_code,
    fetch_strava_activities,
    generate_oauth_state,
    normalize_strava_activity,
    refresh_strava_token,
    send_workout_to_zwift,
)
from app.planner import generate_week_structure, select_next_workout

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
OAUTH_STATE_TTL_SECONDS = 900
oauth_states: dict[str, dict] = {}


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def parse_date(text: str | None, fallback: date) -> date:
    if not text:
        return fallback
    return datetime.fromisoformat(text).date()


def json_dumps(data) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def build_base_url(handler: BaseHTTPRequestHandler) -> str:
    proto = handler.headers.get("X-Forwarded-Proto") or "http"
    host = handler.headers.get("Host") or "localhost:8080"
    return f"{proto}://{host}"


def store_oauth_state(state: str, provider: str):
    oauth_states[state] = {"provider": provider, "expires_at": now_unix() + OAUTH_STATE_TTL_SECONDS}


def consume_oauth_state(state: str, provider: str) -> bool:
    data = oauth_states.pop(state, None)
    if not data:
        return False
    if data.get("provider") != provider:
        return False
    return int(data.get("expires_at") or 0) > now_unix()


def read_tokens(conn, user_id: int, provider: str):
    row = conn.execute(
        "SELECT * FROM integration_tokens WHERE user_id=? AND provider=?", (user_id, provider)
    ).fetchone()
    return dict(row) if row else None


def token_metadata(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        return json.loads(row.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        return {}


def provider_config_status() -> dict:
    strava_required = ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"]
    zwift_required = ["ZWIFT_CLIENT_ID", "ZWIFT_CLIENT_SECRET", "ZWIFT_AUTH_URL", "ZWIFT_TOKEN_URL"]
    strava_missing = [k for k in strava_required if not os.getenv(k)]
    zwift_missing = [k for k in zwift_required if not os.getenv(k)]
    return {
        "strava": {
            "ready": len(strava_missing) == 0,
            "missing": strava_missing,
        },
        "zwift": {
            "ready": len(zwift_missing) == 0,
            "missing": zwift_missing,
        },
    }


def upsert_tokens(conn, user_id: int, provider: str, payload: dict):
    conn.execute(
        """
        INSERT INTO integration_tokens(
          user_id, provider, access_token, refresh_token, expires_at, metadata_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
          access_token=excluded.access_token,
          refresh_token=excluded.refresh_token,
          expires_at=excluded.expires_at,
          metadata_json=excluded.metadata_json,
          updated_at=excluded.updated_at
        """,
        (
            user_id,
            provider,
            payload.get("access_token"),
            payload.get("refresh_token"),
            payload.get("expires_at"),
            json.dumps(payload.get("metadata") or {}),
            now_iso(),
        ),
    )


def list_plan(conn, user_id: int, start_day: date, end_day: date):
    rows = conn.execute(
        """
        SELECT * FROM plan_entries
        WHERE user_id=? AND date >= ? AND date <= ?
        ORDER BY date ASC, id ASC
        """,
        (user_id, start_day.isoformat(), end_day.isoformat()),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def build_workout_profile(entry: dict) -> list[dict]:
    duration = max(20, int(entry.get("duration_minutes") or 60))
    wtype = entry.get("workout_type") or "endurance"
    templates = {
        "recovery": [("warmup", 0.20, 0.48), ("steady", 0.60, 0.52), ("cooldown", 0.20, 0.45)],
        "endurance": [("warmup", 0.15, 0.58), ("steady", 0.70, 0.67), ("cooldown", 0.15, 0.5)],
        "tempo": [("warmup", 0.15, 0.6), ("tempo_1", 0.28, 0.8), ("easy", 0.12, 0.6), ("tempo_2", 0.3, 0.82), ("cooldown", 0.15, 0.5)],
        "threshold": [("warmup", 0.18, 0.62), ("threshold_1", 0.22, 0.88), ("easy", 0.10, 0.6), ("threshold_2", 0.22, 0.9), ("easy", 0.10, 0.6), ("threshold_3", 0.08, 0.9), ("cooldown", 0.10, 0.5)],
        "vo2": [("warmup", 0.20, 0.62), ("vo2_1", 0.08, 0.95), ("easy", 0.06, 0.55), ("vo2_2", 0.08, 0.96), ("easy", 0.06, 0.55), ("vo2_3", 0.08, 0.97), ("easy", 0.06, 0.55), ("vo2_4", 0.08, 0.95), ("cooldown", 0.30, 0.5)],
        "brick": [("bike_warmup", 0.15, 0.6), ("bike_tempo", 0.45, 0.82), ("transition", 0.05, 0.5), ("run_tempo", 0.2, 0.84), ("cooldown", 0.15, 0.5)],
    }
    blocks = templates.get(wtype, templates["endurance"])
    out = []
    minute_cursor = 0
    for name, share, intensity in blocks:
        block_minutes = max(1, int(round(duration * share)))
        out.append(
            {
                "name": name,
                "start_minute": minute_cursor,
                "end_minute": minute_cursor + block_minutes,
                "intensity": intensity,
            }
        )
        minute_cursor += block_minutes

    if out:
        out[-1]["end_minute"] = duration
    return out


def list_completed(conn, user_id: int, limit: int = 400):
    rows = conn.execute(
        "SELECT * FROM completed_workouts WHERE user_id=? ORDER BY started_at ASC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def list_library(conn):
    rows = conn.execute("SELECT * FROM workout_library ORDER BY discipline, tss").fetchall()
    return [row_to_dict(r) for r in rows]


def match_and_complete_plan(conn, user_id: int, workout: dict):
    day = datetime.fromisoformat(workout["started_at"].replace("Z", "+00:00")).date().isoformat()
    row = conn.execute(
        """
        SELECT id FROM plan_entries
        WHERE user_id=? AND date=? AND discipline=? AND status != 'completed'
        ORDER BY ABS(tss - ?) ASC LIMIT 1
        """,
        (user_id, day, workout["discipline"], workout["tss"]),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE plan_entries SET status='completed', updated_at=? WHERE id=?",
            (now_iso(), row["id"]),
        )


def import_completed_workout(conn, user_id: int, workout: dict):
    conn.execute(
        """
        INSERT OR IGNORE INTO completed_workouts(
          user_id, provider, external_id, discipline, name, started_at,
          duration_minutes, tss, intensity, rpe, hr_avg, power_avg, raw_json, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            workout.get("provider", "manual"),
            workout.get("external_id"),
            workout["discipline"],
            workout["name"],
            workout["started_at"],
            int(workout["duration_minutes"]),
            int(workout["tss"]),
            float(workout["intensity"]),
            workout.get("rpe"),
            workout.get("hr_avg"),
            workout.get("power_avg"),
            json.dumps(workout.get("raw_json")) if workout.get("raw_json") else None,
            now_iso(),
        ),
    )
    if conn.total_changes:
        match_and_complete_plan(conn, user_id, workout)


def generate_plan(conn, user_id: int, start_day: date, weeks: int, disciplines: list[str]):
    end_day = start_day + timedelta(days=weeks * 7 - 1)
    conn.execute(
        "DELETE FROM plan_entries WHERE user_id=? AND date >= ? AND date <= ? AND status='planned'",
        (user_id, start_day.isoformat(), end_day.isoformat()),
    )

    library = list_library(conn)
    completed = list_completed(conn, user_id)
    existing_planned = list_plan(conn, user_id, start_day - timedelta(days=14), end_day)
    structure = generate_week_structure(disciplines)

    created = []
    day = start_day
    while day <= end_day:
        discipline = structure[day.weekday()]
        selected, rationale = select_next_workout(
            discipline=discipline,
            target_day=day,
            library=library,
            completed=completed,
            planned=existing_planned,
        )
        title = selected["name"]
        desc = f"{selected['description']} | Planner target: {rationale['target_type']} {rationale['target_tss']} TSS"
        now = now_iso()
        conn.execute(
            """
            INSERT INTO plan_entries(
              user_id, date, discipline, title, description, duration_minutes, tss, intensity,
              workout_type, source, status, linked_workout_id, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'planner', 'planned', ?, ?, ?)
            """,
            (
                user_id,
                day.isoformat(),
                discipline,
                title,
                desc,
                selected["duration_minutes"],
                selected["tss"],
                selected["intensity"],
                selected["workout_type"],
                selected["id"],
                now,
                now,
            ),
        )
        created.append(day.isoformat())
        existing_planned.append(
            {
                "date": day.isoformat(),
                "status": "planned",
                "discipline": discipline,
                "tss": selected["tss"],
            }
        )
        day += timedelta(days=1)

    return {"start": start_day.isoformat(), "end": end_day.isoformat(), "days_created": len(created)}


def sync_all(conn, user_id: int):
    status = {"strava": {"status": "not_connected"}, "zwift": {"status": "not_connected"}}

    strava = read_tokens(conn, user_id, "strava")
    if strava and strava.get("access_token"):
        try:
            refreshed = refresh_strava_token(
                strava,
                os.getenv("STRAVA_CLIENT_ID"),
                os.getenv("STRAVA_CLIENT_SECRET"),
            )
            if refreshed != strava:
                upsert_tokens(
                    conn,
                    user_id,
                    "strava",
                    {
                        "access_token": refreshed.get("access_token"),
                        "refresh_token": refreshed.get("refresh_token"),
                        "expires_at": refreshed.get("expires_at"),
                        "metadata": json.loads(refreshed.get("metadata_json") or "{}"),
                    },
                )

            last = conn.execute(
                "SELECT MAX(started_at) AS last FROM completed_workouts WHERE user_id=? AND provider='strava'",
                (user_id,),
            ).fetchone()["last"]
            after_unix = None
            if last:
                after_unix = int(datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()) - 86400
            activities = fetch_strava_activities(refreshed["access_token"], after_unix)

            imported = 0
            for a in activities:
                w = normalize_strava_activity(a)
                before = conn.total_changes
                import_completed_workout(conn, user_id, w)
                if conn.total_changes > before:
                    imported += 1

            status["strava"] = {
                "status": "ok",
                "fetched": len(activities),
                "imported": imported,
            }
        except Exception as exc:
            status["strava"] = {"status": "error", "message": str(exc)}

    zwift = read_tokens(conn, user_id, "zwift")
    if zwift and zwift.get("access_token"):
        status["zwift"] = {"status": "ok", "message": "Connected. Workout push available."}

    return status


class Handler(BaseHTTPRequestHandler):
    server_version = "TrDupe/0.1"

    def log_message(self, fmt, *args):
        return

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _send(self, code: int, data, content_type: str = "application/json", extra_headers: list[tuple[str, str]] | None = None):
        body = data if isinstance(data, (bytes, bytearray)) else json_dumps(data)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, extra_headers: list[tuple[str, str]] | None = None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()

    def _session_cookie_header(self, token: str | None, clear: bool = False) -> tuple[str, str]:
        jar = cookies.SimpleCookie()
        jar["trdupe_session"] = token or ""
        jar["trdupe_session"]["path"] = "/"
        jar["trdupe_session"]["httponly"] = True
        jar["trdupe_session"]["samesite"] = "Lax"
        if clear:
            jar["trdupe_session"]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
            jar["trdupe_session"]["max-age"] = "0"
        return ("Set-Cookie", jar.output(header="").strip())

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = cookies.SimpleCookie()
        jar.load(raw)
        morsel = jar.get("trdupe_session")
        return morsel.value if morsel else None

    def _current_user_id(self) -> int | None:
        token = self._session_token()
        if not token:
            return None
        with get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE session_token=?",
                (token,),
            ).fetchone()
            if not row:
                return None
            if int(row["expires_at"]) <= now_unix():
                conn.execute("DELETE FROM sessions WHERE session_token=?", (token,))
                return None
            return int(row["user_id"])

    def _require_auth(self) -> int | None:
        user_id = self._current_user_id()
        if user_id is None:
            self._send(401, {"error": "Unauthorized"})
            return None
        return user_id

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        user_id = self._current_user_id()

        if path == "/":
            self._redirect("/index.html" if user_id else "/login.html")
            return

        if path == "/login.html" and user_id:
            self._redirect("/index.html")
            return

        if path == "/index.html" and not user_id:
            self._redirect("/login.html")
            return

        if path == "/api/auth/me":
            if not user_id:
                self._send(401, {"error": "Unauthorized"})
                return
            with get_conn() as conn:
                row = conn.execute("SELECT id, name FROM users WHERE id=?", (user_id,)).fetchone()
                email_row = conn.execute("SELECT email FROM auth_users WHERE id=?", (user_id,)).fetchone()
            self._send(200, {"user": {"id": row["id"], "name": row["name"], "email": email_row["email"]}})
            return

        if path.startswith("/api/") and path not in {"/api/oauth/callback/strava", "/api/oauth/callback/zwift"} and not user_id:
            self._send(401, {"error": "Unauthorized"})
            return

        if path == "/api/bootstrap":
            today = date.today()
            start = today.replace(day=1)
            end = start + timedelta(days=41)
            cfg = provider_config_status()
            with get_conn() as conn:
                plan = list_plan(conn, user_id, start, end)
                workouts = list_completed(conn, user_id, limit=90)
                strava_token = read_tokens(conn, user_id, "strava")
                zwift_token = read_tokens(conn, user_id, "zwift")
                zwift_meta = token_metadata(zwift_token)
                user_row = conn.execute("SELECT id, name FROM users WHERE id=?", (user_id,)).fetchone()
                email_row = conn.execute("SELECT email FROM auth_users WHERE id=?", (user_id,)).fetchone()
                integrations = {
                    "strava": {
                        "connected": bool(strava_token and strava_token.get("access_token")),
                        "oauth_enabled": cfg["strava"]["ready"],
                    },
                    "zwift": {
                        "connected": bool(zwift_token and zwift_token.get("access_token")),
                        "oauth_enabled": cfg["zwift"]["ready"],
                        "upload_url": zwift_meta.get("upload_url"),
                    },
                }
            self._send(
                200,
                {
                    "today": today.isoformat(),
                    "calendar_start": start.isoformat(),
                    "calendar_end": end.isoformat(),
                    "plan": plan,
                    "completed": workouts[-20:],
                    "integrations": integrations,
                    "config": cfg,
                    "user": {"id": user_row["id"], "name": user_row["name"], "email": email_row["email"]},
                },
            )
            return

        if path == "/api/config":
            self._send(200, {"config": provider_config_status()})
            return

        if path == "/api/plan":
            q = parse_qs(parsed.query)
            start = parse_date((q.get("start") or [None])[0], date.today().replace(day=1))
            end = parse_date((q.get("end") or [None])[0], start + timedelta(days=41))
            with get_conn() as conn:
                plan = list_plan(conn, user_id, start, end)
            self._send(200, {"plan": plan})
            return

        if path.startswith("/api/plan-entry/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                try:
                    entry_id = int(parts[2])
                except ValueError:
                    self._send(400, {"error": "Invalid entry id"})
                    return
                with get_conn() as conn:
                    row = conn.execute(
                        """
                        SELECT pe.*, wl.zwo_content
                        FROM plan_entries pe
                        LEFT JOIN workout_library wl ON wl.id = pe.linked_workout_id
                        WHERE pe.user_id=? AND pe.id=?
                        """,
                        (user_id, entry_id),
                    ).fetchone()
                if not row:
                    self._send(404, {"error": "Plan entry not found"})
                    return
                entry = row_to_dict(row)
                self._send(200, {"entry": entry, "profile": build_workout_profile(entry)})
                return

        if path == "/api/integrations/strava/connect":
            if not user_id:
                self._redirect("/login.html")
                return
            client_id = os.getenv("STRAVA_CLIENT_ID")
            client_secret = os.getenv("STRAVA_CLIENT_SECRET")
            if not client_id or not client_secret:
                self._redirect("/index.html?connect_error=strava:missing_server_config")
                return

            state = generate_oauth_state()
            store_oauth_state(state, f"strava:{user_id}")
            redirect_uri = f"{build_base_url(self)}/api/oauth/callback/strava"
            url = build_strava_authorize_url(client_id, redirect_uri, state)
            self._redirect(url)
            return

        if path == "/api/integrations/zwift/connect":
            if not user_id:
                self._redirect("/login.html")
                return
            client_id = os.getenv("ZWIFT_CLIENT_ID")
            client_secret = os.getenv("ZWIFT_CLIENT_SECRET")
            auth_url = os.getenv("ZWIFT_AUTH_URL")
            token_url = os.getenv("ZWIFT_TOKEN_URL")
            scope = os.getenv("ZWIFT_SCOPE", "profile workouts")
            if not client_id or not client_secret or not auth_url or not token_url:
                self._redirect("/index.html?connect_error=zwift:missing_server_config")
                return

            state = generate_oauth_state()
            store_oauth_state(state, f"zwift:{user_id}")
            redirect_uri = f"{build_base_url(self)}/api/oauth/callback/zwift"
            url = build_generic_authorize_url(auth_url, client_id, redirect_uri, state, scope)
            self._redirect(url)
            return

        if path == "/api/oauth/callback/strava":
            if not user_id:
                self._redirect("/login.html?connect_error=strava:not_logged_in")
                return
            q = parse_qs(parsed.query)
            if q.get("error"):
                self._redirect(f"/index.html?connect_error=strava:{q['error'][0]}")
                return
            code = (q.get("code") or [None])[0]
            state = (q.get("state") or [None])[0]
            if not code or not state or not consume_oauth_state(state, f"strava:{user_id}"):
                self._redirect("/index.html?connect_error=strava:invalid_state")
                return

            client_id = os.getenv("STRAVA_CLIENT_ID")
            client_secret = os.getenv("STRAVA_CLIENT_SECRET")
            if not client_id or not client_secret:
                self._redirect("/index.html?connect_error=strava:server_not_configured")
                return

            redirect_uri = f"{build_base_url(self)}/api/oauth/callback/strava"
            try:
                token = exchange_strava_code(code, client_id, client_secret, redirect_uri)
            except Exception:
                self._redirect("/index.html?connect_error=strava:token_exchange_failed")
                return

            with get_conn() as conn:
                upsert_tokens(
                    conn,
                    user_id,
                    "strava",
                    {
                        "access_token": token.get("access_token"),
                        "refresh_token": token.get("refresh_token"),
                        "expires_at": token.get("expires_at"),
                        "metadata": {"athlete": token.get("athlete")},
                    },
                )
            self._redirect("/index.html?connected=strava")
            return

        if path == "/api/oauth/callback/zwift":
            if not user_id:
                self._redirect("/login.html?connect_error=zwift:not_logged_in")
                return
            q = parse_qs(parsed.query)
            if q.get("error"):
                self._redirect(f"/index.html?connect_error=zwift:{q['error'][0]}")
                return
            code = (q.get("code") or [None])[0]
            state = (q.get("state") or [None])[0]
            if not code or not state or not consume_oauth_state(state, f"zwift:{user_id}"):
                self._redirect("/index.html?connect_error=zwift:invalid_state")
                return

            client_id = os.getenv("ZWIFT_CLIENT_ID")
            client_secret = os.getenv("ZWIFT_CLIENT_SECRET")
            token_url = os.getenv("ZWIFT_TOKEN_URL")
            if not client_id or not client_secret or not token_url:
                self._redirect("/index.html?connect_error=zwift:server_not_configured")
                return

            redirect_uri = f"{build_base_url(self)}/api/oauth/callback/zwift"
            try:
                token = exchange_oauth_code(token_url, code, client_id, client_secret, redirect_uri)
            except Exception:
                self._redirect("/index.html?connect_error=zwift:token_exchange_failed")
                return

            with get_conn() as conn:
                existing = read_tokens(conn, user_id, "zwift")
                metadata = token_metadata(existing)
                upsert_tokens(
                    conn,
                    user_id,
                    "zwift",
                    {
                        "access_token": token.get("access_token"),
                        "refresh_token": token.get("refresh_token"),
                        "expires_at": token.get("expires_at"),
                        "metadata": metadata,
                    },
                )
            self._redirect("/index.html?connected=zwift")
            return

        if path.startswith("/api/workouts/") and path.endswith("/zwo"):
            parts = path.strip("/").split("/")
            if len(parts) >= 4:
                workout_id = int(parts[2])
                with get_conn() as conn:
                    row = conn.execute(
                        """
                        SELECT wl.zwo_content, pe.title
                        FROM plan_entries pe
                        JOIN workout_library wl ON wl.id = pe.linked_workout_id
                        WHERE pe.user_id=? AND pe.id=?
                        """,
                        (user_id, workout_id),
                    ).fetchone()
                if not row or not row["zwo_content"]:
                    self._send(404, {"error": "ZWO not available"})
                    return
                filename = f"{(row['title'] or 'workout').replace(' ', '_')}.zwo"
                body = row["zwo_content"].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Disposition", f"attachment; filename={filename}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        payload = self._read_json()
        user_id = self._current_user_id()

        if path == "/api/auth/register":
            email = (payload.get("email") or "").strip().lower()
            password = payload.get("password") or ""
            name = (payload.get("name") or "Athlete").strip() or "Athlete"
            if "@" not in email or len(password) < 8:
                self._send(400, {"error": "Use a valid email and password (8+ chars)."})
                return
            salt, hashed = hash_password(password)
            with get_conn() as conn:
                exists = conn.execute("SELECT id FROM auth_users WHERE email=?", (email,)).fetchone()
                if exists:
                    self._send(409, {"error": "Email already registered."})
                    return
                cur = conn.execute("INSERT INTO users(name, created_at) VALUES(?, ?)", (name, now_iso()))
                new_user_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO auth_users(id, email, password_salt, password_hash, created_at) VALUES(?, ?, ?, ?, ?)",
                    (new_user_id, email, salt, hashed, now_iso()),
                )
                token = new_session_token()
                conn.execute(
                    "INSERT INTO sessions(session_token, user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
                    (token, new_user_id, session_expiry(), now_iso()),
                )
            self._send(
                200,
                {"ok": True, "user": {"id": new_user_id, "email": email, "name": name}},
                extra_headers=[self._session_cookie_header(token)],
            )
            return

        if path == "/api/auth/login":
            email = (payload.get("email") or "").strip().lower()
            password = payload.get("password") or ""
            with get_conn() as conn:
                row = conn.execute(
                    """
                    SELECT au.id, au.password_salt, au.password_hash, u.name
                    FROM auth_users au
                    JOIN users u ON u.id = au.id
                    WHERE au.email=?
                    """,
                    (email,),
                ).fetchone()
                if not row or not verify_password(password, row["password_salt"], row["password_hash"]):
                    self._send(401, {"error": "Invalid credentials."})
                    return
                token = new_session_token()
                conn.execute(
                    "INSERT INTO sessions(session_token, user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
                    (token, row["id"], session_expiry(), now_iso()),
                )
            self._send(
                200,
                {"ok": True, "user": {"id": row["id"], "email": email, "name": row["name"]}},
                extra_headers=[self._session_cookie_header(token)],
            )
            return

        if path == "/api/auth/logout":
            token = self._session_token()
            if token:
                with get_conn() as conn:
                    conn.execute("DELETE FROM sessions WHERE session_token=?", (token,))
            self._send(200, {"ok": True}, extra_headers=[self._session_cookie_header(None, clear=True)])
            return

        if path.startswith("/api/") and not user_id:
            self._send(401, {"error": "Unauthorized"})
            return

        if path == "/api/plan/generate":
            start = parse_date(payload.get("start_date"), date.today())
            weeks = int(payload.get("weeks") or 8)
            disciplines = payload.get("disciplines") or ["cycling", "running", "triathlon"]
            with get_conn() as conn:
                result = generate_plan(conn, user_id, start, weeks, disciplines)
            self._send(200, {"ok": True, "result": result})
            return

        if path == "/api/workouts/complete":
            with get_conn() as conn:
                started_at = payload.get("started_at") or datetime.now(timezone.utc).isoformat()
                workout = {
                    "provider": payload.get("provider") or "manual",
                    "external_id": payload.get("external_id") or f"manual-{int(datetime.now().timestamp())}",
                    "discipline": payload["discipline"],
                    "name": payload.get("name") or "Completed Workout",
                    "started_at": started_at,
                    "duration_minutes": int(payload["duration_minutes"]),
                    "tss": int(payload["tss"]),
                    "intensity": float(payload.get("intensity") or 0.7),
                    "rpe": payload.get("rpe"),
                    "hr_avg": payload.get("hr_avg"),
                    "power_avg": payload.get("power_avg"),
                    "raw_json": payload,
                }
                import_completed_workout(conn, user_id, workout)

                plan_entry_id = payload.get("plan_entry_id")
                if plan_entry_id:
                    conn.execute(
                        "UPDATE plan_entries SET status='completed', updated_at=? WHERE user_id=? AND id=?",
                        (now_iso(), user_id, int(plan_entry_id)),
                    )
            self._send(200, {"ok": True})
            return

        if path == "/api/integrations/strava/token":
            with get_conn() as conn:
                existing = read_tokens(conn, user_id, "strava")
                metadata = token_metadata(existing)
                metadata.update(payload.get("metadata") or {})
                upsert_tokens(
                    conn,
                    user_id,
                    "strava",
                    {
                        "access_token": payload.get("access_token"),
                        "refresh_token": payload.get("refresh_token"),
                        "expires_at": payload.get("expires_at"),
                        "metadata": metadata,
                    },
                )
            self._send(200, {"ok": True})
            return

        if path == "/api/integrations/zwift":
            with get_conn() as conn:
                existing = read_tokens(conn, user_id, "zwift")
                metadata = token_metadata(existing)
                if payload.get("upload_url"):
                    metadata["upload_url"] = payload.get("upload_url")
                upsert_tokens(
                    conn,
                    user_id,
                    "zwift",
                    {
                        "access_token": payload.get("access_token"),
                        "refresh_token": payload.get("refresh_token"),
                        "expires_at": payload.get("expires_at"),
                        "metadata": metadata,
                    },
                )
            self._send(200, {"ok": True})
            return

        if path == "/api/integrations/zwift/config":
            with get_conn() as conn:
                existing = read_tokens(conn, user_id, "zwift")
                metadata = token_metadata(existing)
                metadata["upload_url"] = payload.get("upload_url")
                upsert_tokens(
                    conn,
                    user_id,
                    "zwift",
                    {
                        "access_token": (existing or {}).get("access_token"),
                        "refresh_token": (existing or {}).get("refresh_token"),
                        "expires_at": (existing or {}).get("expires_at"),
                        "metadata": metadata,
                    },
                )
            self._send(200, {"ok": True})
            return

        if path == "/api/sync/all":
            with get_conn() as conn:
                result = sync_all(conn, user_id)
            self._send(200, {"ok": True, "result": result})
            return

        if path == "/api/workouts/send-to-zwift":
            plan_entry_id = int(payload["plan_entry_id"])
            with get_conn() as conn:
                row = conn.execute(
                    """
                    SELECT pe.id, pe.title, wl.zwo_content
                    FROM plan_entries pe
                    JOIN workout_library wl ON wl.id = pe.linked_workout_id
                    WHERE pe.user_id=? AND pe.id = ?
                    """,
                    (user_id, plan_entry_id),
                ).fetchone()
                if not row or not row["zwo_content"]:
                    self._send(404, {"error": "Workout has no ZWO payload"})
                    return

                zwift = read_tokens(conn, user_id, "zwift")
                upload_url = None
                token = None
                if zwift:
                    token = zwift.get("access_token")
                    meta = json.loads(zwift.get("metadata_json") or "{}")
                    upload_url = meta.get("upload_url")

                result = send_workout_to_zwift(row["zwo_content"], upload_url, token)
            self._send(200, {"ok": True, "result": result, "download": f"/api/workouts/{plan_entry_id}/zwo"})
            return

        self._send(404, {"error": "Not found"})

    def _serve_static(self, path: str):
        safe = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(safe).startswith(str(STATIC_DIR)) or not safe.exists() or not safe.is_file():
            self._send(404, {"error": "Not found"})
            return

        ctype = "text/plain"
        if safe.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif safe.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif safe.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"

        body = safe.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(port: int = 8080):
    init_db(DB_PATH)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"TrDupe running at http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run(int(os.getenv("PORT", "8080")))
