import unittest
from urllib.parse import parse_qs, urlparse

from app.integrations import build_generic_authorize_url, build_strava_authorize_url, generate_oauth_state


class OAuthHelperTests(unittest.TestCase):
    def test_generate_oauth_state_random_shape(self):
        a = generate_oauth_state()
        b = generate_oauth_state()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 20)

    def test_build_strava_authorize_url_has_required_params(self):
        url = build_strava_authorize_url("123", "http://localhost:8080/callback", "state123")
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["client_id"][0], "123")
        self.assertEqual(q["redirect_uri"][0], "http://localhost:8080/callback")
        self.assertEqual(q["response_type"][0], "code")
        self.assertEqual(q["state"][0], "state123")

    def test_build_generic_authorize_url(self):
        url = build_generic_authorize_url(
            "https://example.com/oauth/authorize",
            "abc",
            "http://localhost/cb",
            "state-1",
            "profile workouts",
        )
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["client_id"][0], "abc")
        self.assertEqual(q["scope"][0], "profile workouts")


if __name__ == "__main__":
    unittest.main()
