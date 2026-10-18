"""Offline tests for agent.py: the evidence guardrail, the tool cache, tool binding, the tool loop and the streaming flows.

Gemini, Earth Engine and Google Maps are always faked; a call that would reach them fails the test instead.
Run all Python tests from the repo root with `.venv/bin/python -m unittest`.
"""
import base64
import json
import os
import tempfile
import typing
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from google.genai import types
from pydantic import ValidationError

import agent

ROOT = Path(__file__).parent.parent


def drive(gen):
    """Run a generator to the end: (events it yielded, value it returned)."""
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


def ev(tool, claim="a claim"):
    return agent.Evidence(claim=claim, tool=tool, source="src", value="v", date="2024")


def chapter(key, level, *evidence):
    return agent.Chapter(key=key, level=level, headline="h", detail="d", evidence=list(evidence))


def make_report(*chapters):
    return agent.Report(chapters=list(chapters), verdict=["the call", "the why"], overall="Medium", checks=["c"],
                        questions=["q"], lender_note="n")


def make_profile():
    return agent.Profile(who="A family", flood="high", heat="low", smoke="medium", change="low", nearby="low",
                         why="Because.", floor=1, budget_thb=12000)


class StripImagesTest(unittest.TestCase):
    def test_images_are_removed_at_every_depth(self):
        x = {"a": [{"images": {"x": "y"}, "b": 1}], "images": "top", "c": {"d": {"images": 1, "e": [1, {"images": 2}]}}}
        self.assertEqual(agent.strip_images(x), {"a": [{"b": 1}], "c": {"d": {"e": [1, {}]}}})

    def test_other_values_pass_through(self):
        for x in ("text", 3, 1.5, None, True):
            self.assertEqual(agent.strip_images(x), x)
        self.assertEqual(agent.strip_images({"image": "kept", "imagery": 1}), {"image": "kept", "imagery": 1})

    def test_the_input_is_not_changed(self):
        x = {"images": {"a": "b"}, "k": [{"images": 1}]}
        agent.strip_images(x)
        self.assertEqual(x, {"images": {"a": "b"}, "k": [{"images": 1}]})


class EnforceEvidenceTest(unittest.TestCase):
    def test_evidence_from_tools_that_ran_is_kept_untouched(self):
        rep = make_report(chapter("water", "High", ev("water_history")), chapter("heat", "Low", ev("heat")))
        rep, dropped = agent.enforce_evidence(rep, {"water_history", "heat"})
        self.assertEqual(dropped, [])
        self.assertEqual([c.level for c in rep.chapters], ["High", "Low"])
        self.assertEqual([len(c.evidence) for c in rep.chapters], [1, 1])

    def test_claims_whose_tool_never_ran_are_dropped_and_named(self):
        rep = make_report(chapter("water", "High", ev("water_history", "kept"), ev("fire", "invented 1")),
                          chapter("smoke", "Medium", ev("nearby", "invented 2")))
        rep, dropped = agent.enforce_evidence(rep, {"water_history"})
        self.assertEqual(dropped, ["invented 1", "invented 2"])
        self.assertEqual([e.claim for e in rep.chapters[0].evidence], ["kept"])
        self.assertEqual(rep.chapters[0].level, "High")

    def test_a_chapter_left_without_evidence_becomes_not_enough_evidence(self):
        rep = make_report(chapter("water", "High", ev("fire")))
        rep, _ = agent.enforce_evidence(rep, set())
        self.assertEqual(rep.chapters[0].level, "Not enough evidence")
        self.assertEqual(rep.chapters[0].evidence, [])

    def test_a_chapter_that_never_had_evidence_is_not_a_finding_either(self):
        rep = make_report(chapter("heat", "High"))
        rep, dropped = agent.enforce_evidence(rep, {"heat"})
        self.assertEqual((dropped, rep.chapters[0].level), ([], "Not enough evidence"))

    def test_the_report_is_changed_in_place(self):
        rep = make_report(chapter("water", "High", ev("fire")))
        same, _ = agent.enforce_evidence(rep, set())
        self.assertIs(same, rep)


class CachedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "cache"  # not made yet: the first write has to
        patcher = mock.patch.object(agent, "CACHE", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_call_runs_and_stores_then_later_calls_read_the_file(self):
        calls = []

        @agent.cached
        def tool(lat, lon):
            calls.append((lat, lon))
            return {"lat": lat, "images": {"x": "y"}}

        self.assertEqual(tool(1.5, 2.5), {"lat": 1.5, "images": {"x": "y"}})
        self.assertEqual(tool(1.5, 2.5), {"lat": 1.5, "images": {"x": "y"}})
        self.assertEqual(calls, [(1.5, 2.5)])
        self.assertEqual(json.loads((self.dir / "tool-1.5_2.5.json").read_text()), {"lat": 1.5, "images": {"x": "y"}})

    def test_different_arguments_are_different_entries(self):
        @agent.cached
        def tool(x):
            return x

        self.assertEqual((tool(1), tool(2)), (1, 2))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["tool-1.json", "tool-2.json"])

    def test_file_name_is_the_function_and_its_arguments_made_safe(self):
        def _flood_day_images(lat, lon, when):  # same name and arguments as a committed entry in cache/
            return {}

        agent.cached(_flood_day_images)(20.3692, 99.9957, "2024-09-15T23:16+00:00")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["_flood_day_images-20.3692_99.9957_2024-09-15T23_16_00_00.json"])

    def test_unsafe_characters_cannot_leave_the_cache_directory(self):
        @agent.cached
        def tool(x):
            return 1

        tool("../../etc/passwd x")
        (path,) = self.dir.iterdir()
        self.assertEqual(path.parent, self.dir)
        self.assertEqual(path.name, "tool-.._.._etc_passwd_x.json")

    def test_wrapper_keeps_the_function_name(self):
        @agent.cached
        def tool():
            """doc"""

        self.assertEqual((tool.__name__, tool.__doc__), ("tool", "doc"))

    def test_a_failing_tool_stores_nothing(self):
        @agent.cached
        def tool():
            raise RuntimeError("Earth Engine hiccup")

        with self.assertRaises(RuntimeError):
            tool()
        self.assertFalse(self.dir.exists() and list(self.dir.iterdir()))


class CommittedCacheTest(unittest.TestCase):
    """The demo pins in cache/ answer without Earth Engine or Gemini. A changed key scheme would orphan those files and
    cost real runs, so every call that could reach a service here fails the test instead."""

    def setUp(self):
        def reached(*a, **k):
            raise AssertionError("not served from cache/")

        for patcher in (mock.patch.multiple(agent.eo, water_history=reached, flood_day_images=reached, heat=reached, fire=reached,
                                            change=reached),
                        mock.patch.object(agent, "ask", reached), mock.patch.object(agent, "client", reached)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_demo_pin_is_served_from_cache_in_both_languages(self):
        for pin in json.loads((ROOT / "pins.json").read_text()):
            for lang in ("en", "th"):
                tools = agent.make_tools(pin["lat"], pin["lon"], lang)
                with self.subTest(pin=pin["id"], lang=lang):
                    self.assertTrue(tools["water_history"]()["source"])
                    self.assertTrue(tools["heat"]()["source"])
                    self.assertEqual([tools["fire"](r)["radius_km"] for r in (20, 50)], [20, 50])
                    self.assertTrue(tools["change"]()["visual_description"])

    def test_the_mae_sai_flood_images_and_their_reading_come_from_cache(self):
        pin = next(p for p in json.loads((ROOT / "pins.json").read_text()) if p["id"] == "maesai")
        for lang in ("en", "th"):
            tools = agent.make_tools(pin["lat"], pin["lon"], lang)
            worst = tools["water_history"]()["worst"]["time"]
            out = tools["flood_day_images"](worst)
            with self.subTest(lang=lang):
                self.assertTrue(out["water_at_pin"])
                self.assertTrue(out["visual_description"])
                self.assertEqual(set(out["images"]), {"before", "after"})


class PatchedTools:
    """Mixin: the cached tool functions and the Maps calls under make_tools are replaced, so each test sees exactly what is passed."""

    def setUp(self):
        for name in ("_water_history", "_flood_day_images", "_flood_look", "_water_on_date", "_water_on_date_look", "_heat", "_fire",
                     "_change", "_change_look"):
            patcher = mock.patch.object(agent, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        for name in ("air", "nearby", "find"):
            patcher = mock.patch.object(agent.places, name)
            setattr(self, "places_" + name, patcher.start())
            self.addCleanup(patcher.stop)

    def tools(self, lang="en", lat=13.75, lon=100.5):
        return agent.make_tools(lat, lon, lang)


class MakeToolsTest(PatchedTools, unittest.TestCase):
    def test_there_is_a_tool_for_every_name_the_report_schema_allows(self):
        self.assertEqual(set(self.tools()), set(typing.get_args(agent.ToolName)))

    def test_every_tool_can_be_declared_to_gemini_without_coordinates(self):
        params = {"flood_day_images": ["event_time"], "water_on_date": ["date"], "fire": ["radius_km"], "find_places": ["query"]}
        for name, fn in self.tools().items():
            with self.subTest(tool=name):
                decl = types.FunctionDeclaration.from_callable_with_api_option(callable=fn, api_option="VERTEX_AI")
                self.assertEqual(decl.name, name)
                self.assertTrue(decl.description)
                self.assertEqual(list(decl.parameters.properties) if decl.parameters else [], params.get(name, []))

    def test_the_pin_is_snapped_to_about_eleven_metres_so_close_clicks_share_a_cache_entry(self):
        self.tools(lat=13.123456, lon=100.987654)["heat"]()
        self._heat.assert_called_once_with(13.1235, 100.9877)

    def test_plain_tools_pass_the_pin_through(self):
        t = self.tools("th")
        self._water_history.return_value = {"w": 1}
        self._heat.return_value = {"h": 1}
        self.places_air.return_value = {"a": 1}
        self.places_nearby.return_value = {"n": 1}
        self.assertEqual((t["water_history"](), t["heat"](), t["air"](), t["nearby"]()), ({"w": 1}, {"h": 1}, {"a": 1}, {"n": 1}))
        self._water_history.assert_called_once_with(13.75, 100.5)
        self.places_air.assert_called_once_with(13.75, 100.5)
        self.places_nearby.assert_called_once_with(13.75, 100.5, "th")  # the nearby names come back in the report language

    def test_fire_radius_is_20_or_50_km_whatever_the_model_asks(self):
        t = self.tools()
        for asked, used in ((5, 20), (20, 20), (21, 50), (50, 50), (500, 50)):
            self._fire.reset_mock()
            t["fire"](asked)
            self._fire.assert_called_once_with(13.75, 100.5, used)

    def test_find_places_query_is_cut_to_100_characters(self):
        self.tools("th")["find_places"]("x" * 150)
        self.places_find.assert_called_once_with(13.75, 100.5, "x" * 100, "th")

    def test_change_adds_gemini_s_reading_of_the_images(self):
        self._change.return_value = {"change_points_1km": {"built": 4.0}}
        self._change_look.return_value = "new roofs to the east"
        self.assertEqual(self.tools("th")["change"](), {"change_points_1km": {"built": 4.0}, "visual_description": "new roofs to the east"})
        self._change_look.assert_called_once_with(13.75, 100.5, "th")


class FloodDayImagesToolTest(PatchedTools, unittest.TestCase):
    EVENTS = {"worst": {"time": "2024-09-15T23:16+00:00"}, "latest": {"time": "2026-07-26T23:14+00:00"},
              "events": [{"time": "2026-07-26T23:14+00:00"}, {"time": "2024-09-15T23:16+00:00"}, {"time": "2021-05-01T11:00+00:00"}]}

    def setUp(self):
        super().setUp()
        self._water_history.return_value = self.EVENTS
        self._flood_day_images.return_value = {"images": {"before": "b", "after": "a"}, "water_at_pin": True}
        self._flood_look.return_value = "water east of the ring"

    def test_the_models_time_is_matched_to_a_known_event_and_that_events_own_time_is_used(self):
        out = self.tools()["flood_day_images"]("2024-09-15T23:16:00Z")
        self._flood_day_images.assert_called_once_with(13.75, 100.5, "2024-09-15T23:16+00:00")  # the cache key never drifts
        self._flood_look.assert_called_once_with(13.75, 100.5, "2024-09-15T23:16+00:00", "en")
        self.assertEqual(out, {"images": {"before": "b", "after": "a"}, "water_at_pin": True, "visual_description": "water east of the ring"})

    def test_a_time_within_a_day_of_an_event_still_matches(self):
        t = self.tools()["flood_day_images"]
        self.assertNotIn("error", t("2024-09-16T10:00:00Z"))  # 10h44 after
        self.assertNotIn("error", t("2024-09-14T23:16:00+00:00"))  # exactly one day before
        self.assertNotIn("error", t("2021-05-01T11:00:00"))  # no zone means UTC

    def test_a_time_that_is_no_known_event_is_refused_without_a_lookup(self):
        for when in ("2024-09-18T00:00:00Z", "2024-09-17T12:00:00Z", "2024-09-17T00:17:00Z", "2024-09-14T23:15:00Z", "1999-01-01"):
            with self.subTest(when):  # the middle three are within two days but a minute or more past the one-day tolerance
                self.assertEqual(self.tools()["flood_day_images"](when),
                                 {"error": "event_time must be one of the event `time` values from water_history"})
        self._flood_day_images.assert_not_called()

    def test_no_flood_history_at_all_is_just_a_refusal(self):
        self._water_history.return_value = {"worst": None, "latest": None, "events": []}
        self.assertIn("error", self.tools()["flood_day_images"]("2024-09-15T23:16:00Z"))
        self._water_history.return_value = {}
        self.assertIn("error", self.tools()["flood_day_images"]("2024-09-15T23:16:00Z"))

    def test_a_pass_without_images_gets_no_reading(self):
        self._flood_day_images.return_value = {"note": "No radar pass"}
        self.assertEqual(self.tools()["flood_day_images"]("2024-09-15T23:16:00Z"), {"note": "No radar pass"})
        self._flood_look.assert_not_called()

    def test_the_report_language_reaches_the_image_reading(self):
        self.tools("th")["flood_day_images"]("2024-09-15T23:16:00Z")
        self.assertEqual(self._flood_look.call_args.args[-1], "th")


class WaterOnDateToolTest(PatchedTools, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._water_on_date.return_value = {"images": {"before": "b", "after": "a"}, "water_at_pin": False}
        self._water_on_date_look.return_value = "dry ground"

    def test_a_date_is_checked_and_read_by_gemini(self):
        out = self.tools("th")["water_on_date"]("2024-09-12")
        self._water_on_date.assert_called_once_with(13.75, 100.5, "2024-09-12")
        self._water_on_date_look.assert_called_once_with(13.75, 100.5, "2024-09-12", "th")
        self.assertEqual(out["visual_description"], "dry ground")

    def test_anything_after_the_date_is_ignored_so_the_cache_key_is_a_plain_day(self):
        self.tools()["water_on_date"]("2024-09-12T10:30:00Z")
        self._water_on_date.assert_called_once_with(13.75, 100.5, "2024-09-12")

    def test_something_that_is_not_a_date_never_becomes_a_cache_key(self):
        for bad in ("not-a-date", "2024-13-45", "", "../../x"):
            with self.subTest(date=bad), self.assertRaises(ValueError):
                self.tools()["water_on_date"](bad)
        self._water_on_date.assert_not_called()

    def test_no_pass_means_no_reading(self):
        self._water_on_date.return_value = {"note": "No radar pass within 6 day(s) of 2024-09-12."}
        self.assertEqual(self.tools()["water_on_date"]("2024-09-12"), {"note": "No radar pass within 6 day(s) of 2024-09-12."})
        self._water_on_date_look.assert_not_called()


class CachedToolsCallEarthEngineTest(unittest.TestCase):
    """The cached functions forward to eo with the right arguments (and nothing else)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (mock.patch.object(agent, "CACHE", Path(tmp.name)), mock.patch.object(agent, "see", return_value="reading")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.eo = {}
        for name in ("water_history", "flood_day_images", "heat", "fire", "change"):
            patcher = mock.patch.object(agent.eo, name, return_value={"images": {"i": "b64"}, "image_notes": "notes", "from": name})
            self.eo[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_each_cached_tool_calls_its_earth_engine_function(self):
        agent._water_history(1.0, 2.0)
        agent._flood_day_images(1.0, 2.0, "2024-09-15T23:16+00:00")
        agent._heat(1.0, 2.0)
        agent._fire(1.0, 2.0, 50)
        agent._change(1.0, 2.0)
        self.eo["water_history"].assert_called_once_with(1.0, 2.0)
        self.eo["flood_day_images"].assert_called_once_with(1.0, 2.0, "2024-09-15T23:16+00:00")
        self.eo["heat"].assert_called_once_with(1.0, 2.0)
        self.eo["fire"].assert_called_once_with(1.0, 2.0, 50)
        self.eo["change"].assert_called_once_with(1.0, 2.0)

    def test_a_claim_date_looks_six_days_either_side_for_a_radar_pass(self):
        agent._water_on_date(1.0, 2.0, "2024-09-12")
        self.eo["flood_day_images"].assert_called_once_with(1.0, 2.0, "2024-09-12", 6)

    def test_image_readings_use_the_chips_the_notes_the_right_prompt_and_language(self):
        agent._flood_look(1.0, 2.0, "2024-09-15T23:16+00:00", "th")
        agent._water_on_date_look(1.0, 2.0, "2024-09-12", "en")
        agent._change_look(1.0, 2.0, "en")
        self.assertEqual([c.args for c in agent.see.call_args_list], [
            ({"i": "b64"}, "notes", agent.FLOOD_LOOK, "th"), ({"i": "b64"}, "notes", agent.FLOOD_LOOK, "en"),
            ({"i": "b64"}, "notes", agent.CHANGE_LOOK, "en")])


class GeminiCallsTest(unittest.TestCase):
    def fake_client(self, **resp):
        generate = mock.Mock(return_value=SimpleNamespace(**resp))
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate)), generate

    def test_ask_with_a_schema_returns_the_parsed_object_as_json(self):
        client, generate = self.fake_client(parsed="PARSED", text="ignored")
        with mock.patch.object(agent, "client", return_value=client):
            self.assertEqual(agent.ask(["hi"], agent.Heard, system="sys"), "PARSED")
        kwargs = generate.call_args.kwargs
        self.assertEqual((kwargs["model"], kwargs["contents"]), (agent.MODEL, ["hi"]))
        self.assertEqual(kwargs["config"].response_mime_type, "application/json")
        self.assertIs(kwargs["config"].response_schema, agent.Heard)
        self.assertEqual(kwargs["config"].system_instruction, "sys")
        self.assertIs(kwargs["config"].thinking_config, agent.LOW)

    def test_ask_without_a_schema_returns_trimmed_text(self):
        client, generate = self.fake_client(parsed="ignored", text="  a reading \n")
        with mock.patch.object(agent, "client", return_value=client):
            self.assertEqual(agent.ask(["hi"]), "a reading")
        config = generate.call_args.kwargs["config"]
        self.assertIsNone(config.response_mime_type)
        self.assertIsNone(config.response_schema)

    def test_ask_passes_a_custom_thinking_config(self):
        client, generate = self.fake_client(text="x")
        thinking = types.ThinkingConfig(include_thoughts=False)
        with mock.patch.object(agent, "client", return_value=client):
            agent.ask(["hi"], thinking=thinking)
        self.assertIs(generate.call_args.kwargs["config"].thinking_config, thinking)

    def test_transcribe_sends_the_audio_and_returns_the_trimmed_words(self):
        with mock.patch.object(agent, "ask", return_value=agent.Heard(text="  สวัสดี  ")) as ask:
            self.assertEqual(agent.transcribe(b"AUDIO", "audio/webm"), "สวัสดี")
        contents, schema = ask.call_args.args
        part = contents[0].parts[0]
        self.assertEqual((part.inline_data.data, part.inline_data.mime_type), (b"AUDIO", "audio/webm"))
        self.assertIs(schema, agent.Heard)
        self.assertEqual(ask.call_args.kwargs, {"system": agent.HEAR})

    def test_png_decodes_the_base64_chip(self):
        part = agent.png(base64.b64encode(b"\x89PNG-bytes").decode())
        self.assertEqual((part.inline_data.data, part.inline_data.mime_type), (b"\x89PNG-bytes", "image/png"))

    def test_see_labels_each_chip_then_asks_the_question_in_the_report_language(self):
        images = {"before": base64.b64encode(b"B").decode(), "after": base64.b64encode(b"A").decode()}
        with mock.patch.object(agent, "ask", return_value="a reading") as ask:
            self.assertEqual(agent.see(images, "the notes", "look here", "th"), "a reading")
        parts = ask.call_args.args[0]
        self.assertEqual([p.text for p in parts[0::2]], ["before:", "after:", f"the notes\nlook here\n{agent.WRITE['th']}"])
        self.assertEqual([p.inline_data.data for p in parts[1:4:2]], [b"B", b"A"])

    def test_see_with_no_chips_still_asks(self):
        with mock.patch.object(agent, "ask", return_value="r") as ask:
            agent.see({}, "n", "l", "en")
        self.assertEqual([p.text for p in ask.call_args.args[0]], [f"n\nl\n{agent.WRITE['en']}"])


class ClientTest(unittest.TestCase):
    def setUp(self):
        agent._client.cache_clear()
        self.addCleanup(agent._client.cache_clear)

    def build(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")}
        with mock.patch.dict(os.environ, {**base, **env}, clear=True), mock.patch.object(agent.genai, "Client") as client:
            return agent.client(), client

    def test_client_is_built_for_the_project_with_retries(self):
        _, ctor = self.build(GOOGLE_CLOUD_PROJECT="proj")
        kwargs = ctor.call_args.kwargs
        self.assertEqual((kwargs["enterprise"], kwargs["project"], kwargs["location"]), (True, "proj", "global"))
        self.assertEqual(kwargs["http_options"].retry_options.attempts, 4)

    def test_location_can_be_set_by_the_environment(self):
        _, ctor = self.build(GOOGLE_CLOUD_PROJECT="proj", GOOGLE_CLOUD_LOCATION="asia-southeast1")
        self.assertEqual(ctor.call_args.kwargs["location"], "asia-southeast1")

    def test_one_client_is_shared(self):
        first, ctor = self.build(GOOGLE_CLOUD_PROJECT="proj")
        with mock.patch.object(agent.genai, "Client") as again:
            self.assertIs(agent.client(), first)
        ctor.assert_called_once()
        again.assert_not_called()

    def test_no_project_is_a_clear_error(self):
        with self.assertRaises(KeyError):
            self.build()


def reply(*parts):
    return SimpleNamespace(candidates=[SimpleNamespace(content=types.Content(role="model", parts=list(parts) if parts else None))])


def say(text):
    return types.Part(text=text)


def think(text):
    return types.Part(text=text, thought=True)


def call(name, args=None, id=None):
    return types.Part(function_call=types.FunctionCall(name=name, args=args, id=id))


class FakeGemini:
    """Stands in for agent.client(): hands out scripted replies and remembers what each request looked like."""

    def __init__(self, *replies):
        self.replies, self.tool_configs, self.models = list(replies), [], self

    def generate_content(self, *, model, contents, config):
        self.tool_configs.append(config.tool_config)
        return self.replies.pop(0)


class RunToolsTest(unittest.TestCase):
    def run_loop(self, replies, tools=None, contents=None):
        gemini, evidence = FakeGemini(*replies), []
        contents = [agent.user("go")] if contents is None else contents
        with mock.patch.object(agent, "client", return_value=gemini):
            events, text = drive(agent.run_tools("system", contents, tools or {}, evidence))
        return events, text, evidence, contents, gemini

    def test_a_plain_answer_ends_the_loop_and_is_returned(self):
        events, text, evidence, contents, _ = self.run_loop([reply(say("  All done.  "))])
        self.assertEqual(events, [{"type": "say", "text": "All done."}])
        self.assertEqual((text, evidence), ("All done.", []))
        self.assertEqual([c.role for c in contents], ["user", "model"])  # Gemini's own reply is kept, thought signatures and all

    def test_thoughts_are_streamed_apart_from_the_plan(self):
        events, text, *_ = self.run_loop([reply(think(" weighing flood first "), say("Checking water."))])
        self.assertEqual(events, [{"type": "thought", "text": "weighing flood first"}, {"type": "say", "text": "Checking water."}])
        self.assertEqual(text, "Checking water.")

    def test_parts_with_nothing_to_say_are_skipped(self):
        events, text, *_ = self.run_loop([reply(types.Part(text=""), types.Part(inline_data=types.Blob(data=b"x", mime_type="image/png")), say("ok"))])
        self.assertEqual((events, text), ([{"type": "say", "text": "ok"}], "ok"))

    def test_a_reply_with_no_parts_ends_with_no_text(self):
        events, text, *_ = self.run_loop([reply()])
        self.assertEqual((events, text), ([], ""))

    def test_a_tool_call_is_run_streamed_recorded_and_answered_without_images(self):
        def fire(radius_km):
            return {"days": radius_km, "images": {"x": "HUGE"}}

        events, text, evidence, contents, _ = self.run_loop(
            [reply(say("Looking at smoke."), call("fire", {"radius_km": 20}, id="c1")), reply(say("Done."))], {"fire": fire})
        self.assertEqual(events, [
            {"type": "say", "text": "Looking at smoke."}, {"type": "call", "name": "fire", "args": {"radius_km": 20}},
            {"type": "result", "name": "fire", "result": {"days": 20}},  # the UI gets images once, in the report
            {"type": "say", "text": "Done."}])
        self.assertEqual(text, "Done.")
        self.assertEqual(evidence, [{"tool": "fire", "args": {"radius_km": 20}, "result": {"days": 20, "images": {"x": "HUGE"}}}])
        answer = contents[2]  # user, model with the call, then the tool's answer
        self.assertEqual(answer.role, "user")
        fr = answer.parts[0].function_response
        self.assertEqual((fr.id, fr.name, fr.response), ("c1", "fire", {"days": 20}))  # no PNG goes back into the prompt

    def test_calls_in_one_reply_all_run_and_keep_their_order(self):
        tools = {"a": lambda: {"n": "a"}, "b": lambda: {"n": "b"}}
        events, _, evidence, contents, _ = self.run_loop([reply(call("a", id="1"), call("b", id="2")), reply(say("ok"))], tools)
        self.assertEqual([(e["type"], e["name"]) for e in events[:4]], [("call", "a"), ("call", "b"), ("result", "a"), ("result", "b")])
        self.assertEqual([e["tool"] for e in evidence], ["a", "b"])
        self.assertEqual([p.function_response.id for p in contents[2].parts], ["1", "2"])

    def test_a_call_without_arguments_gets_an_empty_dict(self):
        events, _, evidence, *_ = self.run_loop([reply(call("heat", None)), reply(say("ok"))], {"heat": lambda: {"c": 1}})
        self.assertEqual(events[0], {"type": "call", "name": "heat", "args": {}})
        self.assertEqual(evidence[0]["args"], {})

    def test_a_tool_that_raises_becomes_an_error_result_the_agent_can_report(self):
        def boom():
            raise ValueError("EE down")

        with self.assertLogs(agent.log, "WARNING") as logs:
            events, text, evidence, *_ = self.run_loop([reply(call("boom")), reply(say("Not enough evidence."))], {"boom": boom})
        self.assertEqual(events[1], {"type": "result", "name": "boom", "result": {"error": "ValueError: EE down"}})
        self.assertEqual(evidence[0]["result"], {"error": "ValueError: EE down"})
        self.assertEqual(text, "Not enough evidence.")
        self.assertIn("tool boom failed", logs.output[0])

    def test_a_tool_the_model_made_up_is_an_error_result_not_a_crash(self):
        with self.assertLogs(agent.log, "WARNING"):
            events, _, evidence, *_ = self.run_loop([reply(call("teleport")), reply(say("ok"))], {"heat": lambda: {}})
        self.assertEqual(evidence[0]["result"], {"error": "KeyError: 'teleport'"})

    def test_after_the_call_budget_is_spent_tools_are_switched_off_in_code(self):
        tools = {"t": lambda: {"ok": 1}}
        with mock.patch.object(agent, "MAX_CALLS", 3):
            *_, gemini = self.run_loop([reply(call("t", id="1"), call("t", id="2")), reply(call("t", id="3")), reply(say("Done."))], tools)
        first, second, third = gemini.tool_configs
        self.assertIsNone(first)  # nothing spent yet
        self.assertIsNone(second)  # 2 of 3 spent
        self.assertEqual(third.function_calling_config.mode, types.FunctionCallingConfigMode.NONE)  # 3 of 3

    def test_the_default_budget_is_ten_calls(self):
        self.assertEqual(agent.MAX_CALLS, 10)
        tools = {"t": lambda: {}}
        nine = [call("t", id=str(i)) for i in range(9)]
        *_, gemini = self.run_loop([reply(*nine), reply(call("t", id="9")), reply(say("Done."))], tools)
        self.assertIsNone(gemini.tool_configs[1])
        self.assertIsNotNone(gemini.tool_configs[2])


class FlowsTest(unittest.TestCase):
    """report, compare, chat, claim and translate with the model calls and the tool loop replaced."""

    def setUp(self):
        self.asked = []

    def fake_ask(self, answers):
        def ask(contents, schema=None, thinking=agent.LOW, system=None):
            self.asked.append(SimpleNamespace(text=contents[0].parts[0].text, schema=schema, thinking=thinking, system=system))
            return answers[schema]
        return ask

    def test_report_streams_intake_tools_judging_and_the_final_state(self):
        evidence_seen = {}

        def run_tools(system, contents, tools, evidence):
            evidence_seen.update(system=system, prompt=contents[0].parts[0].text, tools=tools)
            evidence.append({"tool": "heat", "args": {}, "result": {"delta_c": 2.0, "images": {"heat": "HUGEPNG"}}})
            yield {"type": "call", "name": "heat", "args": {}}

        rep = make_report(chapter("heat", "Medium", ev("heat", "ran")))
        profile = make_profile()
        with mock.patch.object(agent, "ask", self.fake_ask({agent.Profile: profile, agent.Report: rep})), \
                mock.patch.object(agent, "run_tools", run_tools):
            events = list(agent.report(20.3692, 99.9957, "I have a baby", "th"))

        self.assertEqual([e["type"] for e in events], ["step", "profile", "call", "step", "report"])
        self.assertEqual(events[0]["phase"], "intake")
        self.assertEqual(events[1]["profile"], profile.model_dump())
        self.assertEqual(events[3]["phase"], "judge")
        state = events[4]["state"]
        self.assertEqual({k: state[k] for k in ("lat", "lon", "text", "lang")}, {"lat": 20.3692, "lon": 99.9957, "text": "I have a baby", "lang": "th"})
        self.assertEqual(state["profile"], profile.model_dump())
        self.assertEqual(state["report"], rep.model_dump())
        self.assertEqual(state["evidence"][0]["result"]["images"], {"heat": "HUGEPNG"})  # the stored report keeps its chips

        intake, judge = self.asked
        self.assertEqual((intake.schema, intake.text, intake.system), (agent.Profile, "I have a baby", f"{agent.INTAKE}\n{agent.WRITE['th']}"))
        self.assertIs(intake.thinking, agent.LOW)
        self.assertEqual((judge.schema, judge.system), (agent.Report, f"{agent.JUDGE}\n{agent.WRITE['th']}"))
        self.assertFalse(judge.thinking.include_thoughts)
        self.assertIn("Person wrote: I have a baby", judge.text)
        self.assertNotIn("HUGEPNG", judge.text)  # a PNG is over 100k tokens
        self.assertIn('"delta_c": 2.0', judge.text)
        self.assertEqual(evidence_seen["system"], f"{agent.PLAN}\n{agent.WRITE['th']}")
        self.assertTrue(evidence_seen["prompt"].startswith("Pin: 20.36920, 99.99570\nProfile: "))
        self.assertEqual(set(evidence_seen["tools"]), set(typing.get_args(agent.ToolName)))

    def test_claims_with_no_successful_tool_behind_them_are_removed_and_announced(self):
        def run_tools(system, contents, tools, evidence):
            evidence.append({"tool": "heat", "args": {}, "result": {"delta_c": 1.0}})
            evidence.append({"tool": "fire", "args": {"radius_km": 20}, "result": {"error": "EEException: quota"}})
            return
            yield  # a generator, like the real loop

        rep = make_report(chapter("heat", "Low", ev("heat", "real")), chapter("smoke", "High", ev("fire", "from a failed call")),
                          chapter("nearby", "High", ev("nearby", "never called")))
        with mock.patch.object(agent, "ask", self.fake_ask({agent.Profile: make_profile(), agent.Report: rep})), \
                mock.patch.object(agent, "run_tools", run_tools):
            events = list(agent.report(1.0, 2.0, "x", "en"))
        self.assertEqual([e["type"] for e in events], ["step", "profile", "step", "guardrail", "report"])
        self.assertEqual(events[3]["dropped"], ["from a failed call", "never called"])
        chapters = {c["key"]: c for c in events[4]["state"]["report"]["chapters"]}
        self.assertEqual((chapters["heat"]["level"], len(chapters["heat"]["evidence"])), ("Low", 1))
        self.assertEqual((chapters["smoke"]["level"], chapters["smoke"]["evidence"]), ("Not enough evidence", []))
        self.assertEqual(chapters["nearby"]["level"], "Not enough evidence")

    def test_no_guardrail_event_when_nothing_was_dropped(self):
        def run_tools(system, contents, tools, evidence):
            evidence.append({"tool": "heat", "args": {}, "result": {}})
            return
            yield

        rep = make_report(chapter("heat", "Low", ev("heat")))
        with mock.patch.object(agent, "ask", self.fake_ask({agent.Profile: make_profile(), agent.Report: rep})), \
                mock.patch.object(agent, "run_tools", run_tools):
            events = list(agent.report(1.0, 2.0, "x", "en"))
        self.assertNotIn("guardrail", [e["type"] for e in events])

    def test_compare_sends_both_reports_without_images_and_returns_the_pick_with_its_language(self):
        cmp = agent.Compare(pick="B", reason="Higher ground.", tradeoffs=["Water: B is safer"])
        with mock.patch.object(agent, "ask", self.fake_ask({agent.Compare: cmp})):
            events = list(agent.compare({"who": "x"}, {"k": 1, "images": {"a": "PNG"}}, {"k": 2}, "th"))
        self.assertEqual(events[0], {"type": "step", "phase": "compare", "text": "Comparing the two places for you"})
        self.assertEqual(events[1], {"type": "compare", "compare": {"pick": "B", "reason": "Higher ground.", "tradeoffs": ["Water: B is safer"],
                                                                    "lang": "th"}})
        (asked,) = self.asked
        self.assertEqual(json.loads(asked.text), {"profile": {"who": "x"}, "A": {"k": 1}, "B": {"k": 2}})
        self.assertEqual(asked.system, f"{agent.COMPARE}\n{agent.WRITE['th']}")

    def test_chat_answers_from_the_stored_report_and_returns_only_new_evidence(self):
        state = {"lat": 13.75, "lon": 100.5, "text": "secret profile text", "profile": {"who": "x"}, "report": {"verdict": ["v"]},
                 "evidence": [{"tool": "heat", "args": {}, "result": {"delta_c": 1, "images": {"h": "PNG"}}}]}
        seen = {}

        def run_tools(system, contents, tools, evidence):
            seen.update(system=system, contents=contents, tools=tools)
            evidence.append({"tool": "fire", "args": {}, "result": {"n": 1}})
            yield {"type": "say", "text": "Looking."}
            return "It is mostly March."

        messages = [{"role": "user", "text": "first?"}, {"role": "model", "text": "first answer"}]
        with mock.patch.object(agent, "run_tools", run_tools):
            events = list(agent.chat(state, messages, "Is it only March?", "en"))
        self.assertEqual(events, [{"type": "say", "text": "Looking."}, {"type": "answer", "text": "It is mostly March.",
                                                                          "evidence": [{"tool": "fire", "args": {}, "result": {"n": 1}}]}])
        self.assertEqual([(c.role, c.parts[0].text) for c in seen["contents"]],
                         [("user", "first?"), ("model", "first answer"), ("user", "Is it only March?")])
        self.assertTrue(seen["system"].startswith(f"{agent.CHAT}\n{agent.WRITE['en']}\n\n"))
        grounding = json.loads(seen["system"].rsplit("\n\n", 1)[1])
        self.assertEqual(grounding, {"profile": {"who": "x"}, "report": {"verdict": ["v"]},
                                     "evidence": [{"tool": "heat", "args": {}, "result": {"delta_c": 1}}]})
        self.assertEqual(set(seen["tools"]), set(typing.get_args(agent.ToolName)))

    def test_claim_runs_the_one_radar_check_without_planning(self):
        check = mock.Mock(return_value={"pass_time": "2024-09-12T11:00+00:00"})
        with mock.patch.object(agent, "make_tools", return_value={"water_on_date": check}) as make:
            events = list(agent.claim(13.75, 100.5, "2024-09-12", "th"))
        self.assertEqual(events, [{"type": "call", "name": "water_on_date", "args": {"date": "2024-09-12"}},
                                  {"type": "claim", "result": {"pass_time": "2024-09-12T11:00+00:00"}}])
        make.assert_called_once_with(13.75, 100.5, "th")
        check.assert_called_once_with("2024-09-12")

    def test_translate_returns_the_strings_in_order(self):
        with mock.patch.object(agent, "ask", self.fake_ask({agent.Texts: agent.Texts(texts=["น้ำ", "ความร้อน"])})):
            events = list(agent.translate(["Water", "Heat"], "th"))
        self.assertEqual(events, [{"type": "translated", "texts": ["น้ำ", "ความร้อน"]}])
        (asked,) = self.asked
        self.assertEqual(json.loads(asked.text), ["Water", "Heat"])
        self.assertEqual(asked.system, f"{agent.TRANSLATE}\n{agent.WRITE['th']}")

    def test_thai_is_sent_as_thai_not_as_escapes(self):
        with mock.patch.object(agent, "ask", self.fake_ask({agent.Texts: agent.Texts(texts=["Water"])})):
            list(agent.translate(["น้ำ"], "en"))
        self.assertIn("น้ำ", self.asked[0].text)

    def test_a_translation_with_a_missing_or_extra_line_is_refused_rather_than_shifted(self):
        for texts in (["only one"], ["a", "b", "c"]):
            with self.subTest(returned=len(texts)), mock.patch.object(agent, "ask", self.fake_ask({agent.Texts: agent.Texts(texts=texts)})):
                with self.assertRaisesRegex(ValueError, rf"translation returned {len(texts)} of 2 strings"):
                    list(agent.translate(["x", "y"], "th"))


class SchemaTest(unittest.TestCase):
    def test_evidence_can_only_name_a_real_tool(self):
        for tool in typing.get_args(agent.ToolName):
            ev(tool)
        with self.assertRaises(ValidationError):
            ev("telepathy")

    def test_not_enough_evidence_is_a_chapter_level_but_not_an_overall_level(self):
        chapter("water", "Not enough evidence")
        with self.assertRaises(ValidationError):
            agent.Report(chapters=[], verdict=[], overall="Not enough evidence", checks=[], questions=[], lender_note="")

    def test_profile_weights_are_low_medium_or_high(self):
        with self.assertRaises(ValidationError):
            agent.Profile(who="x", flood="extreme", heat="low", smoke="low", change="low", nearby="low", why="y", floor=None, budget_thb=None)

    def test_chapters_are_the_five_the_app_shows(self):
        self.assertEqual(typing.get_args(agent.Chapter.model_fields["key"].annotation), ("water", "heat", "smoke", "change", "nearby"))

    def test_every_report_language_has_a_writing_instruction(self):
        self.assertEqual(set(agent.WRITE), set(typing.get_args(agent.Lang)))
        self.assertIn("Gregorian", agent.WRITE["th"])  # the UI prints Gregorian years, so Gemini must cite them too


if __name__ == "__main__":
    unittest.main()
