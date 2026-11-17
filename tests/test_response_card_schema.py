import hashlib
import json
"""Authoring validation and recipient-context boundaries for Response Cards."""
import copy
import unittest

from response_cards.schema import SchemaError, personalize, render_text, validate_schema


def card(*fields, **values):
    return {"title": "Confirmation", "fields": list(fields), **values}


def field(identity="answer", kind="short_text", **values):
    return {"id": identity, "type": kind, "label": "Your response", **values}


class SchemaTests(unittest.TestCase):
    def test_normalizes_without_mutating_and_retains_historical_independence(self):
        source = card(field(kind="text", verification={"expected": " Name ", "mode": "normalized-text"}))
        before = copy.deepcopy(source)
        frozen = validate_schema(source)
        self.assertEqual(source, before)
        self.assertEqual(frozen["fields"][0]["type"], "short_text")
        self.assertEqual(frozen["fields"][0]["verification"], {
            "expected": " Name ", "mode": "normalized", "on_mismatch": "ALLOW_AND_FLAG",
        })
        frozen["fields"][0]["verification"]["expected"] = "Different"
        self.assertEqual(source, before)
        self.assertFalse(frozen["fields"][0]["required"])

    def test_typed_conditional_logic_only_references_earlier_responses(self):
        valid = card(
            field("attend", "boolean"),
            field("reason", show_if={"field": "attend", "equals": False}),
            field("session", "multi_select", options=["Monday", "Tuesday"]),
            field("notes", show_if={"field": "session", "equals": ["Monday"]}),
            field("count", "integer"),
            field("explanation", show_if={"field": "count", "equals": 3}),
        )
        self.assertEqual(validate_schema(valid)["fields"][1]["show_if"]["equals"], False)
        for position, condition in (
            (1, {"field": "attend", "equals": "false"}),
            (1, {"field": "session", "equals": "Monday"}),
            (1, {"field": "reason", "equals": "value"}),
            (3, {"field": "session", "equals": "Monday"}),
            (3, {"field": "session", "equals": ["Wednesday"]}),
            (3, {"field": "session", "equals": ["Monday", "Monday"]}),
            (5, {"field": "count", "equals": True}),
            (5, {"field": "count", "equals": 2.5}),
        ):
            with self.subTest(condition=condition):
                invalid = copy.deepcopy(valid)
                invalid["fields"][position]["show_if"] = condition
                with self.assertRaises(SchemaError):
                    validate_schema(invalid)

    def test_choice_and_static_constraints(self):
        invalid_fields = [
            field(kind="single_select"),
            field(kind="radio", options=[]),
            field(kind="multi_select", options=["A", "A"]),
            field(kind="single_select", options=[{"label": "A"}]),
            field(options=["A"]),
            field(kind="static", required=True),
            field(kind="static", verification={"expected": "hello"}),
            field(required="true"),
        ]
        for invalid in invalid_fields:
            with self.subTest(invalid=invalid), self.assertRaises(SchemaError):
                validate_schema(card(invalid))
        with self.assertRaises(SchemaError):
            validate_schema(card(field("info", "static"), field(show_if={"field": "info", "equals": "yes"})))

    def test_rejects_executable_extensions_unknown_keys_and_unbounded_inputs(self):
        for invalid in (
            card(field(verification={"expected": "x", "mode": "regex"})),
            card(field(verification={"expected": "x", "mode": "custom"})),
            card(field(verification={"expected": "x", "on_mismatch": "RUN_ACTION"})),
            card(field(verification={"expected": {"anything": "nested"}})),
            card(field(verification={"expected": [["nested"]]})),
            card(field(verification={"expected": float("inf")})),
            card(field(verification={"expected": 2 ** 70})),
            card(field(script="alert(1)")),
            card(field(), on_submit="roster.sync"),
            card(field(identity="constructor")),
            card(field(identity="__proto__")),
            card(field(identity="a.b")),
            card(field(), field()),
            card(*[field(f"field{i}") for i in range(51)]),
            card(field(), title="x" * 201),
            card(*[field(f"field{i}", "static", label="x" * 10000) for i in range(20)]),
        ):
            with self.subTest(invalid=str(invalid)[:150]), self.assertRaises(SchemaError):
                validate_schema(invalid)

    def test_verification_policy_shapes_and_aliases(self):
        for mode, expected in (("numeric", " 3.2 "), ("numeric", 3), ("one_of", ["A", "B"]), ("case_insensitive", "YES")):
            self.assertIn(validate_schema(card(field(verification={"expected": expected, "mode": mode})))["fields"][0]["verification"]["mode"],
                          {"numeric", "one-of", "case-insensitive"})
        for mode, expected in (("numeric", True), ("numeric", "nan"), ("numeric", "seat A3"), ("numeric", []), ("one-of", "A"), ("normalized", ["A"])):
            with self.subTest(mode=mode, expected=expected), self.assertRaises(SchemaError):
                validate_schema(card(field(verification={"expected": expected, "mode": mode})))

    def test_date_and_time_condition_values_are_real_wall_clock_values(self):
        for kind, value in (("date", "2026-09-14"), ("time", "09:30"), ("time", "23:59:00")):
            self.assertEqual(validate_schema(card(field("first", kind), field(show_if={"field": "first", "equals": value})))["fields"][1]["show_if"]["equals"], value)
        for kind, value in (("date", "2026-02-30"), ("date", "September 14"), ("time", "25:30"), ("time", "09:30Z")):
            with self.subTest(kind=kind, value=value), self.assertRaises(SchemaError):
                validate_schema(card(field("first", kind), field(show_if={"field": "first", "equals": value})))


class PersonalizationTests(unittest.TestCase):
    def test_same_safe_vocabulary_in_every_text_property(self):
        source = card(
            field("seat", "single_select", label="Choose {{seat.number}}", options=["{{seat.number}}", "Other"],
                  verification={"expected": "{{seat.number}}"}),
            field("explain", label="Hello {{student.first_name}}", show_if={"field": "seat", "equals": "{{seat.number}}"}),
            title="{{course.code}} confirmation", description="{{student.full_name}}",
            confirmation_message="Thanks {{student.first_name}}",
        )
        before = copy.deepcopy(source)
        actual = personalize(source, {"course.code": "MATH 140", "seat.number": "C17", "student.first_name": "Jane", "student.full_name": "Jane Smith"})
        self.assertEqual(actual["title"], "MATH 140 confirmation")
        self.assertEqual(actual["fields"][0]["options"], ["C17", "Other"])
        self.assertEqual(actual["fields"][0]["verification"]["expected"], "C17")
        self.assertEqual(actual["fields"][1]["show_if"]["equals"], "C17")
        self.assertEqual(actual["confirmation_message"], "Thanks Jane")
        self.assertEqual(source, before)

    def test_uin_is_never_available_without_explicit_last_four_filter(self):
        self.assertEqual(render_text("{{ student.uin | last4 }}", {"student.uin": "001230045"}), "0045")
        self.assertEqual(render_text("{{student.uin|last4}}", {"student.uin | last4": "0045"}), "0045")
        for text in ("{{student.uin}}", "{{student.uin | upper}}", "{{student.email | last4}}"):
            with self.subTest(text=text), self.assertRaises(SchemaError):
                validate_schema(card(field(label=text)))
        for value in (1230045, "1230045", "00123x045"):
            with self.subTest(value=value), self.assertRaises(SchemaError):
                render_text("{{student.uin | last4}}", {"student.uin": value})

    def test_rejects_unknown_and_malformed_variables_before_issuance(self):
        for text in (
            "{{student.password}}", "{{student.__class__}}", "{{ student.name.upper() }}",
            "{{student.name", "student.name}}", "{{{student.name}}}", "{{1+1}}",
        ):
            with self.subTest(text=text), self.assertRaises(SchemaError):
                validate_schema(card(field(label=text)))

    def test_requires_nonempty_context_without_nested_lookup_or_recursive_expansion(self):
        source = card(field(label="Hello {{student.name}}"))
        for context in ({}, {"student": {"name": "Jane"}}, {"student.name": None}, {"student.name": " "},
                        {"student.name": "{{student.email}}", "student.email": "secret@example.com"}, {"student.name": ["Jane"]}):
            with self.subTest(context=context), self.assertRaises(SchemaError):
                personalize(source, context)
        self.assertEqual(personalize(source, {"student.name": "<b>Jane</b>"})["fields"][0]["label"], "Hello <b>Jane</b>")

    def test_expansion_revalidates_size_and_choice_collisions(self):
        with self.assertRaises(SchemaError):
            personalize(card(field(), title="{{student.name}}"), {"student.name": "A" * 201})
        with self.assertRaises(SchemaError):
            personalize(card(field(kind="radio", options=["{{student.name}}", "Jane"])), {"student.name": "Jane"})
        with self.assertRaises(SchemaError):
            render_text("{{student.name}}" * 100, {"student.name": "A" * 10000})
        with self.assertRaises(SchemaError):
            personalize(card(field(verification={"expected": "{{seat.number}}", "mode": "numeric"})), {"seat.number": "C17"})


class ExamConfirmationModeTests(unittest.TestCase):
    def test_compact_mode_and_salted_uin_digest(self):
        from response_cards.schema import personalize, uin_digest, validate_schema, SchemaError
        schema = validate_schema({"title": "Seat", "fields": [
            {"id": "room", "type": "short_text", "label": "Room", "required": True, "verification": {"expected": "{{exam.room}}", "mode": "compact", "on_mismatch": "BLOCK_SUBMISSION"}},
            {"id": "when", "type": "static", "label": "{{exam.date}} at {{exam.time}}"},
            {"id": "uin", "type": "short_text", "label": "UIN", "required": True, "verification": {"expected": "{{student.uin | uin_digest}}", "mode": "uin-digest", "on_mismatch": "BLOCK_SUBMISSION"}}]})
        card = personalize(schema, {"exam.room": "HECC 105", "exam.date": "2026-09-25", "exam.time": "7:00 PM", "student.uin": "001234567"})
        self.assertEqual(card["fields"][1]["label"], "2026-09-25 at 7:00 PM")
        salt, digest = card["fields"][2]["verification"]["expected"].split(":")
        self.assertEqual(digest, hashlib.sha256((salt + "001234567").encode()).hexdigest())
        self.assertNotIn("001234567", json.dumps(card))
        self.assertEqual(uin_digest("001234567", "0123456789abcdef"), "0123456789abcdef:16a0df6add80ce8e71b02453534f0255a2641787d4ffa8a774964f9ac9523e4b")
        for bad in ({"expected": "001234567", "mode": "uin-digest"}, {"expected": ["a"], "mode": "compact"}, {"expected": "{{student.uin}}", "mode": "uin-digest"}):
            with self.subTest(bad=bad), self.assertRaises(SchemaError):
                validate_schema({"title": "Bad", "fields": [{"id": "x", "type": "short_text", "label": "x", "verification": bad}]})
        with self.assertRaises(SchemaError):
            personalize(schema, {"exam.room": "HECC 105", "exam.date": "2026-09-25", "exam.time": "7:00 PM", "student.uin": "12345"})


if __name__ == "__main__":
    unittest.main()
