from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math


@dataclass
class TrainingState:
    ctl: float
    atl: float
    tsb: float
    completion_rate: float
    discipline_loads: dict[str, float]
    recent_types: list[str]


def _parse_date(text: str) -> datetime:
    if "T" in text:
        return datetime.fromisoformat(text)
    return datetime.fromisoformat(f"{text}T00:00:00")


def compute_training_state(completed: list[dict], planned: list[dict]) -> TrainingState:
    today = datetime.utcnow().date()
    daily_tss = defaultdict(float)
    discipline_loads = defaultdict(float)
    recent_types = []

    for w in completed:
        day = _parse_date(w["started_at"]).date()
        tss = float(w.get("tss") or 0)
        daily_tss[day] += tss
        if (today - day).days <= 28:
            discipline_loads[w.get("discipline", "cycling")] += tss
        if (today - day).days <= 10 and w.get("workout_type"):
            recent_types.append(w["workout_type"])

    days = [today - timedelta(days=i) for i in range(60, -1, -1)]
    ctl = 0.0
    atl = 0.0
    ctl_tau = 42.0
    atl_tau = 7.0

    for day in days:
        tss = daily_tss.get(day, 0.0)
        ctl = ctl + (tss - ctl) / ctl_tau
        atl = atl + (tss - atl) / atl_tau

    tsb = ctl - atl

    planned_last_14 = [p for p in planned if 0 <= (today - _parse_date(p["date"]).date()).days <= 14]
    done_last_14 = [p for p in planned_last_14 if p.get("status") == "completed"]
    completion_rate = (len(done_last_14) / len(planned_last_14)) if planned_last_14 else 0.8

    return TrainingState(
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        completion_rate=completion_rate,
        discipline_loads=dict(discipline_loads),
        recent_types=recent_types,
    )


def choose_workout_type(state: TrainingState, target_date: date, discipline: str) -> str:
    week_in_block = ((target_date.isocalendar().week - 1) % 4) + 1
    hard_types = {"vo2", "threshold", "tempo"}
    hard_count = sum(1 for t in state.recent_types if t in hard_types)

    if state.tsb < -20:
        return "recovery"
    if week_in_block == 4:
        return "recovery"
    if hard_count >= 3:
        return "endurance"

    dow = target_date.weekday()
    if dow in {1, 3}:  # Tue/Thu hard days
        if state.completion_rate > 0.75 and state.tsb > -10:
            return "threshold" if discipline != "triathlon" else "brick"
        return "tempo"
    if dow == 5:
        return "endurance"
    if dow == 6:
        return "recovery"
    return "endurance"


def target_tss_for_day(state: TrainingState, discipline: str, workout_type: str, target_date: date) -> int:
    discipline_base = {
        "cycling": 65,
        "running": 55,
        "triathlon": 72,
    }.get(discipline, 60)

    phase = ((target_date.isocalendar().week - 1) % 4) + 1
    phase_mod = {1: 0, 2: 6, 3: 10, 4: -18}[phase]

    type_mod = {
        "recovery": -30,
        "endurance": -8,
        "tempo": 5,
        "threshold": 14,
        "vo2": 18,
        "brick": 10,
    }.get(workout_type, 0)

    compliance_mod = 8 * (state.completion_rate - 0.7)
    freshness_mod = 0
    if state.tsb > 10:
        freshness_mod += 8
    elif state.tsb < -15:
        freshness_mod -= 12

    chronic_load_mod = min(12, state.ctl * 0.15)

    target = discipline_base + phase_mod + type_mod + compliance_mod + freshness_mod + chronic_load_mod
    return max(18, int(round(target)))


def select_next_workout(
    discipline: str,
    target_day: date,
    library: list[dict],
    completed: list[dict],
    planned: list[dict],
):
    state = compute_training_state(completed, planned)
    workout_type = choose_workout_type(state, target_day, discipline)
    target_tss = target_tss_for_day(state, discipline, workout_type, target_day)

    candidates = [w for w in library if w["discipline"] == discipline]
    if workout_type == "brick":
        primary = [w for w in candidates if w["workout_type"] in {"brick", "threshold", "tempo"}]
    elif workout_type == "threshold":
        primary = [w for w in candidates if w["workout_type"] in {"threshold", "tempo", "vo2"}]
    elif workout_type == "recovery":
        primary = [w for w in candidates if w["workout_type"] in {"recovery", "endurance"}]
    else:
        primary = [w for w in candidates if w["workout_type"] == workout_type] or candidates

    recent_names = [c.get("name") for c in completed[-6:]]

    def score(workout: dict) -> float:
        tss_cost = abs(workout["tss"] - target_tss)
        variety_penalty = 8 if workout["name"] in recent_names else 0
        intensity_penalty = 10 * abs(float(workout["intensity"]) - _expected_intensity(workout_type))
        return tss_cost + variety_penalty + intensity_penalty

    selected = min(primary or candidates, key=score)

    rationale = {
        "state": {
            "ctl": round(state.ctl, 1),
            "atl": round(state.atl, 1),
            "tsb": round(state.tsb, 1),
            "completion_rate": round(state.completion_rate, 2),
        },
        "target_tss": target_tss,
        "target_type": workout_type,
    }
    return selected, rationale


def _expected_intensity(workout_type: str) -> float:
    return {
        "recovery": 0.52,
        "endurance": 0.67,
        "tempo": 0.8,
        "threshold": 0.88,
        "vo2": 0.94,
        "brick": 0.82,
    }.get(workout_type, 0.72)


def generate_week_structure(disciplines: list[str]) -> dict[int, str]:
    ordered = disciplines[:] if disciplines else ["cycling"]
    while len(ordered) < 3:
        ordered.append(ordered[-1])

    # 0 Mon ... 6 Sun
    return {
        0: ordered[0],
        1: ordered[1],
        2: ordered[2],
        3: ordered[0],
        4: ordered[1],
        5: ordered[2],
        6: ordered[0],
    }
