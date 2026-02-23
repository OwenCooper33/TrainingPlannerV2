# TrDupe

TrainerRoad-style training planner with:
- Automated cycling/running/triathlon plans
- Progression algorithm for next-workout selection (fitness/fatigue + compliance + periodization)
- Grid-style calendar as default landing page
- Workout detail popup with planned intensity graph and in-popup Zwift send button
- Strava import integration
- Zwift workout push/export integration (`.zwo` files)
- Persistent storage via SQLite (`app.db`)
- Auto-sync on app open

## Run

```bash
python3 server.py
```

Then open `http://localhost:8080`.

## Authentication

- You must log in to use the planner and API.
- Open `http://localhost:8080/login.html` to:
  - create an account
  - log in with email/password
- After login, app sessions are stored in an HTTP-only cookie.
- `Strava` and `Zwift` connect buttons are shown after login, and the app prompts you to connect each service when not yet connected.

## Integrations

- The app shows a startup `Server Config` section listing missing OAuth environment variables.
- If OAuth config is missing, clicking connect logs a clear reason in the app log.
- `Strava` OAuth login:
  - Set `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET`
  - In Strava app settings, set callback URL to: `http://localhost:8080/api/oauth/callback/strava`
  - Click `Connect Strava` in the UI
- `Zwift` OAuth-style login (provider-configurable):
  - Set `ZWIFT_CLIENT_ID`, `ZWIFT_CLIENT_SECRET`, `ZWIFT_AUTH_URL`, `ZWIFT_TOKEN_URL`
  - Optional: `ZWIFT_SCOPE` (default: `profile workouts`)
  - Set callback URL to: `http://localhost:8080/api/oauth/callback/zwift`
  - Click `Connect Zwift` in the UI
- Zwift workout push:
  - Configure `Zwift Upload URL` in app settings if you have an endpoint for workout upload.
  - If no upload URL is configured, the app still generates downloadable `.zwo` files.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```
