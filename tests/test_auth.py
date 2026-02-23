import unittest

from app.auth import hash_password, verify_password


class AuthTests(unittest.TestCase):
    def test_hash_and_verify(self):
        salt, digest = hash_password("supersecret")
        self.assertTrue(verify_password("supersecret", salt, digest))
        self.assertFalse(verify_password("wrong", salt, digest))


if __name__ == "__main__":
    unittest.main()
