"""A role's reasoning choice comes from the catalogue's facts about its model; without facts nothing changes."""
import json
import unittest
from pathlib import Path

from grading import config
from grading.jobs import thinking_factor
from grading.model_catalog import normalize
from grading.providers.cloud import HostedEndpoint
from test_grading_model_catalog import model

CASES = json.loads((Path(__file__).parent / "fixtures" / "grading" / "reasoning_cases.json").read_text())
REQUEST = {"prompt": "transcript_grade", "payload": {"rubric": {"items": []}, "transcript": {"lines": []}}, "images": []}
MUST = {"supported": True, "mandatory": True, "efforts": ["low", "high", "max"], "default_effort": "max"}


def connection(name="glm", **extra):
    return {**config.ENDPOINT, "name": name, "base_url": config.AGGREGATOR, "key_env": "OPENROUTER_API_KEY", "model": f"lab/{name}", "lineage": name, "aggregator": True, **extra}


class ReasoningChoices(unittest.TestCase):
    def test_choices_and_defaults_match_the_cases_the_settings_screen_is_held_to(self):
        for case in CASES:
            endpoint = connection(reasoning=case["reasoning"], reasoning_options=case["options"])
            with self.subTest(case["name"]):
                self.assertEqual((config.reasoning_choices(endpoint), config.reasoning_default(endpoint)), (case["choices"], case["default"]))

    def test_facts_are_validated_and_a_shipped_connection_saved_before_them_takes_its_shipped_facts(self):
        for bad in ({"supported": True}, {**MUST, "efforts": ["low", "low"]}, {**MUST, "efforts": ["ultra"]}, {**MUST, "supported": False}, {**MUST, "default_effort": "medium"}, "high"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.endpoint_entry(connection(reasoning_options=bad))
        self.assertEqual(config.endpoint_entry(connection(reasoning_options={**MUST, "efforts": ["max", "low", "high"]}))["reasoning_options"]["efforts"], ["low", "high", "max"])
        # Settings saved yesterday: the same connections, without the new key. Nothing is fetched to complete them.
        saved = [{k: v for k, v in e.items() if k != "reasoning_options"} for e in config.ENDPOINTS]
        loaded = {e["name"]: e["reasoning_options"] for e in config.validate({"endpoints": saved})["endpoints"]}
        self.assertEqual(loaded, {e["name"]: e["reasoning_options"] for e in config.ENDPOINTS})
        self.assertEqual((loaded["qwen3-vl-32b"]["supported"], loaded["gpt-oss-120b"]["mandatory"], loaded["kimi-k2.5"]["efforts"]), (False, True, []))
        self.assertIsNone(config.endpoint_entry(connection())["reasoning_options"])  # A model nobody described stays unknown.

    def test_a_roles_choice_is_kept_when_its_model_offers_it_and_settings_always_load(self):
        base = config.validate({})
        endpoints = [*base["endpoints"], connection("glm", reasoning_options=MUST)]
        tiers = {**base["tiers"], "cloud_standard": {**base["tiers"]["cloud_standard"], "verifier": "glm", "flag_reviewer": "glm"}}
        chosen = {"cloud_standard": {"verifier": "high", "flag_reviewer": "low", "second": "low", "reader": "high", "primary": "on"}, "cloud_high_assurance": {"verifier": "on"}}
        clean = config.validate({**base, "endpoints": endpoints, "tiers": tiers, "role_reasoning": chosen})
        # The reader's model never thinks and the primary's offers no "on": those choices fall back instead of refusing every setting.
        self.assertEqual(clean["role_reasoning"], {"cloud_standard": {"verifier": "high", "flag_reviewer": "low", "second": "low"}})
        roles = config.roles(clean, "cloud_standard")
        self.assertEqual({role: e["reasoning_choice"] for role, e in roles.items()}, {"reader": "off", "primary": "off", "second": "low", "verifier": "high", "flag_reviewer": "low"})
        for bad in ({"local": {}}, {"cloud_standard": {"grader": "low"}}, {"cloud_standard": "high"}, ["high"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.validate({**base, "role_reasoning": bad})

    def test_the_rubric_reviewer_has_its_own_choice_because_it_is_no_team_role(self):
        """Seen 2026-09-21: the reviewer was resolved outside the roles, so a model that must think got its least effort and one
        that may think was told not to, for the one request that reads a whole exam's rubrics at once."""
        base = config.validate({})
        endpoints = [*base["endpoints"], connection("glm", reasoning_options=MUST)]
        chosen = config.validate({**base, "endpoints": endpoints, "rubric_reviewer": "glm", "rubric_reviewer_reasoning": "high"})
        self.assertEqual((chosen["rubric_reviewer_reasoning"], config.rubric_reviewer(chosen)["reasoning_choice"]), ("high", "high"))
        # No choice made: the model's own default, as for a role. A choice the model does not offer falls back; settings always load.
        self.assertEqual(config.rubric_reviewer(config.validate({**base, "endpoints": endpoints, "rubric_reviewer": "glm"}))["reasoning_choice"], "low")
        stale = config.validate({**base, "endpoints": endpoints, "rubric_reviewer": "glm", "rubric_reviewer_reasoning": "off"})
        self.assertEqual((stale["rubric_reviewer_reasoning"], config.rubric_reviewer(stale)["reasoning_choice"]), ("", "low"))
        # An unset reviewer is Standard's primary grader, with the reviewer's own choice where that model offers one.
        self.assertEqual(config.rubric_reviewer(base)["name"], base["tiers"]["cloud_standard"]["primary"])
        self.assertEqual(config.validate({**base, "rubric_reviewer_reasoning": "high"})["rubric_reviewer_reasoning"], "")  # That model never thinks.
        self.assertEqual(HostedEndpoint(config.rubric_reviewer(chosen)).body({**REQUEST, "prompt": "rubric_quality", "payload": {"questions": []}})[0]["reasoning"], {"effort": "high", "exclude": False})

    def test_a_batch_frozen_before_these_facts_existed_behaves_as_it_did(self):
        frozen = config.validate({})
        frozen.pop("role_reasoning")
        frozen["endpoints"] = [{k: v for k, v in e.items() if k != "reasoning_options"} for e in frozen["endpoints"]]
        roles = config.roles(frozen, "cloud_standard")
        self.assertTrue(all("reasoning_choice" not in e for e in roles.values() if e))
        body, metadata = HostedEndpoint(roles["second"]).body(REQUEST)
        self.assertEqual((body["reasoning"], body["max_tokens"], "reasoning" in metadata), ({"max_tokens": 8192, "exclude": False}, 16384, False))
        self.assertEqual(HostedEndpoint(roles["reader"]).body({**REQUEST, "prompt": "reconcile"})[0]["reasoning"], {"enabled": False})
        self.assertEqual((thinking_factor(roles["second"]), thinking_factor(roles["reader"])), (3, 1))


class ReasoningOnTheWire(unittest.TestCase):
    def sent(self, endpoint):
        body, metadata = HostedEndpoint(endpoint).body(REQUEST)
        return body.get("reasoning"), body["max_tokens"], metadata.get("reasoning")

    def test_each_choice_is_sent_as_what_it_is_and_the_answer_keeps_its_room(self):
        glm = connection(reasoning_options=MUST, max_tokens=4000, reasoning_tokens=6000)
        self.assertEqual(self.sent(glm), ({"effort": "low", "exclude": False}, 10000, "low"))  # It must think and nobody chose: the least that works.
        self.assertEqual(self.sent({**glm, "reasoning_choice": "max"}), ({"effort": "max", "exclude": False}, 10000, "max"))
        toggled = connection(reasoning_options={"supported": True, "mandatory": False, "efforts": [], "default_effort": ""}, max_tokens=4000, reasoning_tokens=6000)
        self.assertEqual(self.sent(toggled), ({"enabled": False}, 4000, "off"))
        self.assertEqual(self.sent({**toggled, "reasoning_choice": "on"}), ({"max_tokens": 6000, "exclude": False}, 10000, "on"))
        # A model that never thinks is sent no field about thinking: that field alone once left it without an eligible host.
        never = connection(reasoning_options=config.NEVER_THINKS, max_tokens=4000)
        self.assertEqual(self.sent(never), (None, 4000, "off"))
        self.assertFalse(HostedEndpoint(never).capabilities()["separated_reasoning"])
        self.assertTrue(HostedEndpoint(glm).capabilities()["separated_reasoning"])

    def test_each_connection_keeps_its_own_caps_and_the_workspace_toggle_can_omit_them(self):
        settings = config.validate({})
        settings["endpoints"] = [{**e, "max_tokens": 4000, "reasoning_tokens": 2000} if e["name"] == "gpt-oss-120b" else e
                                 for e in settings["endpoints"]]
        second = config.roles(settings, "cloud_standard")["second"]
        self.assertEqual((second["token_limits"], second["max_tokens"], second["reasoning_tokens"]), (True, 4000, 2000))
        self.assertEqual(config.roles(settings, "cloud_standard")["reader"]["max_tokens"], 8192)
        body = HostedEndpoint(second).body(REQUEST)[0]
        self.assertEqual((body["max_tokens"], body["reasoning"]), (6000, {"effort": "medium", "exclude": False}))
        off = config.validate({**settings, "token_limits": False, "endpoints": settings["endpoints"]})
        reader = HostedEndpoint(config.roles(off, "cloud_standard")["reader"]).body({**REQUEST, "prompt": "reconcile"})[0]
        self.assertNotIn("max_tokens", reader)
        self.assertNotIn("reasoning", reader)
        thinker = HostedEndpoint(config.roles(off, "cloud_standard")["second"]).body(REQUEST)[0]
        self.assertNotIn("max_tokens", thinker)
        self.assertEqual(thinker["reasoning"], {"effort": "medium", "exclude": False})
        frozen = {k: v for k, v in settings.items() if k != "token_limits"}
        legacy = config.roles(frozen, "cloud_standard")["second"]
        self.assertEqual((legacy["token_limits"], legacy["max_tokens"]), (True, 4000))
        self.assertIn("max_tokens", HostedEndpoint(legacy).body(REQUEST)[0])

    def test_the_estimate_counts_thinking_by_how_much_was_asked_for(self):
        glm = connection(reasoning_options=MUST)
        factors = [thinking_factor({**glm, "reasoning_choice": choice}) for choice in ("off", "low", "high", "max")]
        self.assertEqual(factors, sorted(factors))
        self.assertEqual((factors[0], thinking_factor(connection(reasoning=True)), thinking_factor(connection())), (1, 3, 1))


class ReasoningFromTheCatalogue(unittest.TestCase):
    def test_the_hosts_statement_becomes_facts_and_a_model_that_must_think_gets_room_for_its_answer(self):
        rows = [model("lab/plain"),
                model("lab/optional", supported_parameters=["structured_outputs", "reasoning"], reasoning={"mandatory": False, "default_enabled": True, "supported_efforts": ["max", "high", "low", "none", "ultra"], "default_effort": "high"}),
                model("lab/must", supported_parameters=["structured_outputs", "reasoning"], reasoning={"mandatory": True, "supported_efforts": ["high", "medium", "low"], "default_effort": "medium"}),
                model("lab/toggle", supported_parameters=["structured_outputs", "reasoning"])]
        found = {m["id"]: m["endpoint"] for m in normalize({"data": rows})}
        self.assertEqual(found["lab/plain"]["reasoning_options"], config.NEVER_THINKS)
        # "none" is the same request as Off, and an effort this application does not know is left out rather than guessed at.
        self.assertEqual(found["lab/optional"]["reasoning_options"], {"supported": True, "mandatory": False, "efforts": ["low", "high", "max"], "default_effort": "high"})
        self.assertEqual(found["lab/toggle"]["reasoning_options"], {"supported": True, "mandatory": False, "efforts": [], "default_effort": ""})
        must = found["lab/must"]
        self.assertEqual((must["reasoning_options"]["mandatory"], must["reasoning"], config.reasoning_default(must)), (True, False, "low"))
        self.assertLessEqual(must["max_tokens"] + must["reasoning_tokens"], 8192 * 2)
        for endpoint in found.values():
            self.assertEqual(config.endpoint_entry({**endpoint, "name": "saved", "lineage": endpoint["lineage"] or "lab"})["reasoning_options"], endpoint["reasoning_options"])  # What the catalogue gives can be saved.


if __name__ == "__main__":
    unittest.main()
