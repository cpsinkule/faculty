"""ACCESS / PERFORMANCE: shared wire limits preserve endpoint exceptions and default refusals."""
import unittest

from flask import Flask, request
from werkzeug.exceptions import HTTPException
from werkzeug.routing import Rule

from request_limits import json_object, protect_body


class RequestBodyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(MAX_CONTENT_LENGTH=26 * 1024 * 1024, MAX_FORM_PARTS=24)

    def check(self, endpoint, content_type, size=0):
        with self.app.test_request_context("/", method="POST", content_type=content_type,
                                           environ_overrides={"CONTENT_LENGTH": str(size)}):
            request.url_rule = Rule("/", endpoint=endpoint)
            result = protect_body()
            return (request.max_content_length, request.max_form_parts,
                    None if result is None else (result[1], result[0].get_json()))

    def test_endpoint_uploads_override_their_blueprint_json_defaults(self):
        self.assertEqual(self.check("frq_grading.upload_rubric", "multipart/form-data"), (10 * 1024**2 + 65536, 4, None))
        self.assertEqual(self.check("frq_grading.upload_rubric", "application/json")[2],
                         (415, {"error": "Upload rubric sources using multipart/form-data."}))
        self.assertEqual(self.check("frq_grading.other", "application/json"), (2 * 1024**2, 24, None))

    def test_canvas_and_mail_attachments_keep_their_separate_budgets(self):
        self.assertEqual(self.check("canvas.message_send", "multipart/form-data"), (26 * 1024**2, 12, None))
        self.assertEqual(self.check("gmail_mail.send", "multipart/form-data"), (26 * 1024**2, 12, None))  # 25 MB of files since 2026-11-12
        self.assertEqual(self.check("canvas.message_send", "application/json"), (256 * 1024, 24, None))
        self.assertEqual(self.check("gmail_mail.send", "application/json"), (32 * 1024, 24, None))

    def test_capture_accepts_json_or_one_image_with_metadata(self):
        self.assertEqual(self.check("assistant.capture_upload", "application/json"), (512 * 1024, 2, None))
        self.assertEqual(self.check("assistant.capture_upload", "multipart/form-data"), (5 * 1024**2 + 512 * 1024, 2, None))
        self.assertEqual(self.check("assistant.capture_upload", "text/plain")[2],
                         (415, {"error": "Supply selected content or one selected image and its metadata."}))

    def test_default_limit_precedes_content_type_and_vendor_json_is_accepted(self):
        self.assertEqual(self.check("unknown", "text/plain", 32769)[2], (413, {"error": "The request is too large."}))
        self.assertEqual(self.check("unknown", "text/plain", 32768)[2],
                         (415, {"error": "Send JSON with Content-Type: application/json."}))
        self.assertEqual(self.check("unknown", "application/problem+json", 32768), (32768, 24, None))

    def test_inbox_protocol_keeps_its_own_body_handling(self):
        self.assertEqual(self.check("inbox.submit", "message/rfc822", 40000), (26 * 1024**2, 24, None))


class JsonObjectTests(unittest.TestCase):
    """Each route keeps its own `silent` flag and refusal text; the helper only names them in one place."""

    def read(self, data, content_type="application/json", **options):
        app = Flask(__name__)
        with app.test_request_context("/", method="POST", data=data, content_type=content_type):
            return json_object(**options)

    def test_an_object_is_returned_as_sent(self):
        self.assertEqual(self.read('{"a": [1]}'), {"a": [1]})
        self.assertEqual(self.read('{"a": 1}', silent=True), {"a": 1})

    def test_anything_else_is_refused_with_the_routes_own_message(self):
        for data in ('[1]', 'null', '"x"', '3'):
            with self.assertRaisesRegex(ValueError, "^Supply a JSON object.$"):
                self.read(data)
            with self.assertRaisesRegex(ValueError, "^The request must be a JSON object.$"):
                self.read(data, message="The request must be a JSON object.")

    def test_silent_turns_an_unreadable_body_into_the_same_refusal(self):
        for data, content_type in (("{broken", "application/json"), ("x", "text/plain"), ("", "application/json")):
            with self.assertRaisesRegex(ValueError, "^Send a JSON object.$"):
                self.read(data, content_type, message="Send a JSON object.", silent=True)
            with self.assertRaises(HTTPException) as caught:
                self.read(data, content_type)
            self.assertIn(caught.exception.code, (400, 415))


if __name__ == "__main__":
    unittest.main()
