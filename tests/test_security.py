import unittest

from face_attendance.security import hash_password, validate_password, verify_password


class SecurityTests(unittest.TestCase):
    def test_password_round_trip(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertNotIn("correct horse", encoded)

    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_password("1234567")

    def test_invalid_hash_is_rejected(self):
        self.assertFalse(verify_password("anything", "not-a-valid-hash"))


if __name__ == "__main__":
    unittest.main()
