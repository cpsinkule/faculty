"""The request ID every idempotent write carries: one shape and one refusal for every service that checks it."""
import unittest

from request_ids import REQUEST, valid_request_id


class RequestIdTests(unittest.TestCase):
    def test_an_id_of_one_to_a_hundred_letters_digits_and_dots_colons_dashes_underscores_passes(self):
        for value in ("a", "A1", "9", "x" * 100, "a.b_c:d-e", "550e8400-e29b-41d4-a716-446655440000", "_", ".", ":", "-"):
            self.assertIs(valid_request_id(value), value)

    def test_anything_else_is_refused_with_the_same_text(self):
        for value in ("", "x" * 101, "a b", "a\n", "\na", "a/b", "café", "١٢", "a\u0000", None, 12345, b"abc", ["a"], {"a": 1}, True):
            with self.assertRaisesRegex(ValueError, "^Send a request ID.$"):
                valid_request_id(value)

    def test_the_pattern_is_the_one_the_services_used_to_declare(self):
        old = __import__("re").compile(r"[A-Za-z0-9._:-]{1,100}")
        for value in ("", "a", "a" * 100, "a" * 101, "a-b", "a b", "a\n", "é", "١"):
            self.assertEqual(bool(REQUEST.fullmatch(value)), bool(old.fullmatch(value)))


if __name__ == "__main__":
    unittest.main()
