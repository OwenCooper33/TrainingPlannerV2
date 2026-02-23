import os
import unittest

from server import build_workout_profile, provider_config_status


class ServerConfigTests(unittest.TestCase):
    def test_provider_config_status_shape(self):
        keys = [
            "STRAVA_CLIENT_ID",
            "STRAVA_CLIENT_SECRET",
            "ZWIFT_CLIENT_ID",
            "ZWIFT_CLIENT_SECRET",
            "ZWIFT_AUTH_URL",
            "ZWIFT_TOKEN_URL",
        ]
        old = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            cfg = provider_config_status()
            self.assertIn("strava", cfg)
            self.assertIn("zwift", cfg)
            self.assertFalse(cfg["strava"]["ready"])
            self.assertFalse(cfg["zwift"]["ready"])
            self.assertGreaterEqual(len(cfg["strava"]["missing"]), 1)
            self.assertGreaterEqual(len(cfg["zwift"]["missing"]), 1)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_build_workout_profile_has_ordered_blocks(self):
        profile = build_workout_profile({"duration_minutes": 60, "workout_type": "threshold"})
        self.assertGreaterEqual(len(profile), 3)
        self.assertEqual(profile[0]["start_minute"], 0)
        self.assertEqual(profile[-1]["end_minute"], 60)
        for i in range(1, len(profile)):
            self.assertGreaterEqual(profile[i]["start_minute"], profile[i - 1]["end_minute"])


if __name__ == "__main__":
    unittest.main()
