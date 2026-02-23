from datetime import date, datetime, timedelta
import unittest

from app.planner import compute_training_state, select_next_workout


class PlannerTests(unittest.TestCase):
    def test_training_state_ctl_and_completion(self):
        today = datetime.utcnow().date()
        completed = []
        for i in range(10):
            completed.append(
                {
                    "started_at": f"{(today - timedelta(days=i)).isoformat()}T08:00:00",
                    "tss": 70,
                    "discipline": "cycling",
                    "workout_type": "endurance",
                }
            )
        planned = [{"date": (today - timedelta(days=i)).isoformat(), "status": "completed"} for i in range(7)]
        state = compute_training_state(completed, planned)
        self.assertGreater(state.ctl, 5)
        self.assertGreater(state.completion_rate, 0.9)

    def test_select_next_workout_returns_recovery_under_high_fatigue(self):
        today = date.today()
        completed = []
        for i in range(4):
            completed.append(
                {
                    "started_at": f"{(today - timedelta(days=i)).isoformat()}T07:00:00",
                    "tss": 180,
                    "discipline": "cycling",
                    "workout_type": "vo2",
                    "name": f"Hard {i}",
                }
            )
        library = [
            {"id": 1, "discipline": "cycling", "name": "Recovery", "tss": 20, "intensity": 0.5, "workout_type": "recovery"},
            {"id": 2, "discipline": "cycling", "name": "Threshold", "tss": 90, "intensity": 0.88, "workout_type": "threshold"},
        ]
        workout, rationale = select_next_workout("cycling", today, library, completed, planned=[])
        self.assertEqual(workout["workout_type"], "recovery")
        self.assertEqual(rationale["target_type"], "recovery")


if __name__ == "__main__":
    unittest.main()
