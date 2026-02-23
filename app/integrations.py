from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def build_strava_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all",
        "state": state,
    }
    return f"{STRAVA_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_strava_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_generic_authorize_url(
    auth_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str | None = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if scope:
        params["scope"] = scope
    return f"{auth_url}?{urllib.parse.urlencode(params)}"


def exchange_oauth_code(
    token_url: str,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(token_url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def refresh_strava_token(token_row: dict, client_id: str | None, client_secret: str | None) -> dict:
    if not client_id or not client_secret or not token_row.get("refresh_token"):
        return token_row

    now_ts = int(datetime.now(timezone.utc).timestamp())
    expires_at = token_row.get("expires_at") or 0
    if expires_at > now_ts + 120:
        return token_row

    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token_row["refresh_token"],
        }
    ).encode("utf-8")

    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {
        **token_row,
        "access_token": data.get("access_token", token_row.get("access_token")),
        "refresh_token": data.get("refresh_token", token_row.get("refresh_token")),
        "expires_at": data.get("expires_at", token_row.get("expires_at")),
    }


def fetch_strava_activities(access_token: str, after_unix: int | None = None) -> list[dict]:
    params = {"per_page": 100, "page": 1}
    if after_unix:
        params["after"] = after_unix

    url = f"{STRAVA_ACTIVITIES_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")

    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def normalize_strava_activity(a: dict) -> dict:
    moving_minutes = int(round((a.get("moving_time") or 0) / 60.0))
    weighted = a.get("average_watts") or 180
    duration_h = max(0.25, moving_minutes / 60.0)
    if a.get("type") in {"Run", "TrailRun"}:
        discipline = "running"
        tss = int(duration_h * 55 * (1 + min(0.5, (a.get("average_heartrate") or 140) / 190.0)))
    elif a.get("type") in {"Ride", "VirtualRide"}:
        discipline = "cycling"
        tss = int(duration_h * (weighted / 200) * 65)
    else:
        discipline = "triathlon"
        tss = int(duration_h * 60)

    intensity = min(0.98, max(0.45, tss / max(20, moving_minutes * 1.5) / 100 + 0.55))
    return {
        "provider": "strava",
        "external_id": str(a.get("id")),
        "discipline": discipline,
        "name": a.get("name") or f"{discipline.title()} Workout",
        "started_at": a.get("start_date_local") or a.get("start_date"),
        "duration_minutes": moving_minutes,
        "tss": max(15, tss),
        "intensity": round(intensity, 2),
        "hr_avg": a.get("average_heartrate"),
        "power_avg": a.get("average_watts"),
        "raw_json": a,
    }


def send_workout_to_zwift(zwo_content: str, upload_url: str | None, access_token: str | None) -> dict:
    if not upload_url or not access_token:
        return {
            "status": "queued",
            "message": "Zwift credentials/upload URL missing. Returning workout file for manual import.",
        }

    payload = json.dumps({"zwo": zwo_content}).encode("utf-8")
    req = urllib.request.Request(upload_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
        return {"status": "sent", "response": parsed}
    except urllib.error.URLError as exc:
        return {"status": "failed", "message": str(exc)}
