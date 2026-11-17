"""canonical_json: the pinned bytes of each flavour, and that every module keeps exporting its own name for them."""

import hashlib
import unittest
from datetime import date
from decimal import Decimal

import canonical_json
from absences import department_makeups
from assistant import evidence, service as assistant_service
from canvas import announcements, content as canvas_content, messages as canvas_messages
from communications import record
from exam_day import service as exam_day
from exam_workflow import requests as exam_requests, service as exam_service
from gmail_mail import message as gmail_message
from google_calendar import service as google_calendar
from gradebook import canvas_export, common as gradebook_common
from grading import models as grading_models, regrade_requests
from jev import service as jev_service
from question_bank import service as question_bank
from response_cards import service as response_cards
from scheduling import service as scheduling
from student_support import service as student_support
from teach_inbox import service as inbox
import seating_mail_merge

SAMPLE = {"nom": "José", "b": [2, 1, {"y": 1, "x": "日本"}], "a": 1.5}
TEXT = '{"a":1.5,"b":[2,1,{"x":"日本","y":1}],"nom":"José"}'
DIGEST = "008909aba9ab40620c0db9d1b0d24a695806c8d82c2ad4ee5e920a055ea493e3"
ASCII_DIGEST = "b9fe84b4666c0d763d8905d75e58c98fa3efbdf0c14c55ed11452c57fcc29eea"  # the same SAMPLE, non-ASCII text \u-escaped
JOSE = {"name": "Jos\u00e9"}
JOSE_ASCII = "782f7fb6e7349477ad0878467428033420f78fc728c94d07ebb1d49d7cbae82e"
JOSE_UTF8 = "0264c9b67e4687fb6eb775c9517fa029a75fa2b3e041da81bd8703885d13647b"


class FlavourTests(unittest.TestCase):
    def test_the_text_is_sorted_compact_utf8(self):
        self.assertEqual(canonical_json.encoded(SAMPLE), TEXT)
        self.assertEqual(canonical_json.encoded_strict(SAMPLE), TEXT)

    def test_the_digest_is_the_sha256_of_that_text(self):
        self.assertEqual(canonical_json.digest(SAMPLE), DIGEST)
        self.assertEqual(canonical_json.digest(SAMPLE), hashlib.sha256(TEXT.encode()).hexdigest())
        self.assertEqual(canonical_json.digest_strict(SAMPLE), DIGEST)

    def test_the_flavours_differ_on_nan_and_infinity_and_nowhere_else(self):
        self.assertEqual(canonical_json.encoded({"n": float("nan")}), '{"n":NaN}')
        self.assertEqual(canonical_json.encoded([float("inf")]), "[Infinity]")
        for value in ({"n": float("nan")}, [float("inf")], {"x": -float("inf")}):
            with self.assertRaises(ValueError):
                canonical_json.encoded_strict(value)
            with self.assertRaises(ValueError):
                canonical_json.digest_strict(value)

    def test_only_the_strict_digest_takes_bytes_and_hashes_them_as_they_are(self):
        self.assertEqual(canonical_json.digest_strict(b"x"), hashlib.sha256(b"x").hexdigest())
        with self.assertRaises(TypeError):
            canonical_json.digest(b"x")

    def test_values_json_cannot_write_are_refused(self):
        for value in ({"d": Decimal("1.5")}, {"d": date(2026, 11, 6)}, {"s": {1}}):
            for function in (canonical_json.encoded, canonical_json.encoded_strict, canonical_json.digest, canonical_json.digest_strict):
                with self.assertRaises(TypeError):
                    function(value)


class ModuleNameTests(unittest.TestCase):
    """Each module keeps the name its importers use, with the bytes it has always produced."""

    def test_utf8_text_and_digests(self):
        for function in (record.encoded, exam_requests.encoded, exam_service.encoded, jev_service.encoded, question_bank._json,
                         scheduling.encoded, student_support.encoded, exam_day._encoded, gradebook_common.encoded,
                         grading_models.encoded, response_cards.encoded, inbox.encoded):
            self.assertEqual(function(SAMPLE), TEXT, function)
        for function in (exam_requests.digest, exam_service.digest, jev_service.fingerprint, question_bank._sha, scheduling.request_digest,
                         student_support._digest, canvas_content.digest, seating_mail_merge._digest, gmail_message.fingerprint,
                         gradebook_common.digest, grading_models.digest, response_cards.digest):
            self.assertEqual(function(SAMPLE), DIGEST, function)

    def test_the_strict_modules_refuse_nan_and_the_others_write_it(self):
        value = {"n": float("nan")}
        for function in (gradebook_common.encoded, grading_models.encoded, response_cards.encoded, inbox.encoded):
            with self.assertRaises(ValueError):
                function(value)
        for function in (record.encoded, exam_requests.encoded, exam_service.encoded, jev_service.encoded, question_bank._json,
                         scheduling.encoded, student_support.encoded, exam_day._encoded):
            self.assertEqual(function(value), '{"n":NaN}')

    def test_the_gradebook_and_grading_digests_pass_bytes_through(self):
        for function in (gradebook_common.digest, grading_models.digest):
            self.assertEqual(function(b"x"), hashlib.sha256(b"x").hexdigest())

    def test_the_response_card_digest_still_refuses_bytes(self):
        with self.assertRaises(TypeError):
            response_cards.digest(b"x")


class AsciiDigestTests(unittest.TestCase):
    """The ASCII-escaped flavour: stored fingerprints of non-ASCII names must keep escaping."""

    def test_non_ascii_text_is_escaped_before_it_is_hashed(self):
        self.assertEqual(canonical_json.ascii_digest(SAMPLE), ASCII_DIGEST)
        self.assertEqual(canonical_json.ascii_digest(JOSE), JOSE_ASCII)
        self.assertEqual(canonical_json.ascii_digest(JOSE), hashlib.sha256(b'{"name":"Jos\\u00e9"}').hexdigest())
        self.assertEqual(canonical_json.digest(JOSE), JOSE_UTF8)
        self.assertNotEqual(JOSE_ASCII, JOSE_UTF8)

    def test_plain_ascii_hashes_like_the_utf8_flavour(self):
        self.assertEqual(canonical_json.ascii_digest({"b": [1, "x"], "a": None}), canonical_json.digest({"b": [1, "x"], "a": None}))

    def test_a_default_lets_dates_decimals_and_paths_through_and_none_refuses_them(self):
        value = {"d": date(2026, 11, 6), "p": Decimal("1.50")}
        self.assertEqual(canonical_json.ascii_digest(value, default=str),
                         hashlib.sha256(b'{"d":"2026-11-06","p":"1.50"}').hexdigest())
        with self.assertRaises(TypeError):
            canonical_json.ascii_digest(value)
        self.assertEqual(canonical_json.ascii_digest(SAMPLE, default=None), ASCII_DIGEST)

    def test_every_module_keeps_its_name_with_the_flavour_it_always_had(self):
        for function in (department_makeups._digest, evidence.digest, assistant_service._digest, regrade_requests._digest):
            self.assertEqual(function(SAMPLE), ASCII_DIGEST, function)
            self.assertEqual(function({"d": date(2026, 11, 6)}), canonical_json.ascii_digest({"d": date(2026, 11, 6)}, default=str))
        for function in (canvas_export.digest, google_calendar.content_hash):
            self.assertEqual(function(SAMPLE), ASCII_DIGEST, function)
            with self.assertRaises(TypeError):
                function({"d": date(2026, 11, 6)})

    def test_the_canvas_fingerprints_hash_the_escaped_value(self):
        title = {"title": "T\u00e9st", "text": "Body", "courses": [1, 2]}
        self.assertEqual(announcements.fingerprint("T\u00e9st", "Body", [{"course_id": 2}, {"course_id": 1}]),
                         canonical_json.ascii_digest(title))
        value = {"subject": "S\u00fcbject", "body": "B", "courses": [[1, "individual", [3, 9]]]}
        self.assertEqual(canvas_messages.fingerprint("S\u00fcbject", "B", [{"course_id": 1, "kind": "individual",
                                                                          "recipients": [{"user_id": 9}, {"user_id": 3}]}]),
                         canonical_json.ascii_digest(value))


if __name__ == "__main__":
    unittest.main()
