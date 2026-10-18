"""Offline tests for main.py: what each route accepts, the NDJSON stream and its error line.

agent and places are replaced, so no route can reach Gemini, Earth Engine or Google Maps.
Run all Python tests from the repo root with `.venv/bin/python -m unittest`.
"""
import base64
import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import main

REPORT = {"lat": 20.3692, "lon": 99.9957, "text": "Buying this house for my parents."}


def lines(response):
    return [json.loads(line) for line in response.text.splitlines()]


def recorder(*events, then=None):
    """A stand-in for an agent function: a generator that notes its arguments, yields `events`, then raises `then` if given."""
    calls = []

    def fn(*args):
        calls.append(args)
        yield from events
        if then:
            raise then
    return fn, calls


class NdjsonTest(unittest.TestCase):
    def test_one_json_object_per_line(self):
        self.assertEqual(list(main.ndjson([{"type": "a"}, {"type": "b", "n": 1}])), ['{"type": "a"}\n', '{"type": "b", "n": 1}\n'])

    def test_a_line_never_contains_a_raw_newline_whatever_the_text(self):
        (line,) = main.ndjson([{"type": "say", "text": "first\nsecond\r\nthird"}])
        self.assertEqual(line.count("\n"), 1)
        self.assertEqual(json.loads(line)["text"], "first\nsecond\r\nthird")

    def test_type_comes_first_with_json_dumps_spacing_because_web_server_ts_saves_reports_by_that_prefix(self):
        (line,) = main.ndjson([{"type": "report", "state": {"lat": 1}}])
        self.assertTrue(line.startswith('{"type": "report"'))

    def test_a_failure_part_way_is_told_to_the_ui_after_what_was_already_sent(self):
        def events():
            yield {"type": "step"}
            raise ValueError("EE quota")

        with self.assertLogs(main.log, "ERROR"):
            out = list(main.ndjson(events()))
        self.assertEqual([json.loads(x) for x in out], [{"type": "step"}, {"type": "error", "text": "ValueError: EE quota"}])

    def test_a_failure_before_the_first_event_is_still_an_error_line(self):
        def events():
            raise KeyError("GOOGLE_CLOUD_PROJECT")
            yield

        with self.assertLogs(main.log, "ERROR"):
            (out,) = main.ndjson(events())
        self.assertEqual(json.loads(out), {"type": "error", "text": "KeyError: 'GOOGLE_CLOUD_PROJECT'"})

    def test_an_empty_stream_is_empty(self):
        self.assertEqual(list(main.ndjson([])), [])


class RouteCase(unittest.TestCase):
    client = TestClient(main.app)

    def post(self, path, body):
        return self.client.post(path, json=body)

    def assert_rejected(self, path, body, msg=None):
        r = self.post(path, body)
        self.assertEqual(r.status_code, 422, msg or body)
        return r

    def routed(self, target, name, *events, then=None):
        fn, calls = recorder(*events, then=then)
        patcher = mock.patch.object(target, name, fn)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class ReportRouteTest(RouteCase):
    def test_streams_the_agents_events_as_ndjson(self):
        calls = self.routed(main.agent, "report", {"type": "step"}, {"type": "report", "state": {"x": 1}})
        r = self.post("/api/report", REPORT)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/x-ndjson"))
        self.assertEqual(lines(r), [{"type": "step"}, {"type": "report", "state": {"x": 1}}])
        self.assertEqual(calls, [(20.3692, 99.9957, "Buying this house for my parents.", "en")])

    def test_the_language_is_passed_on(self):
        calls = self.routed(main.agent, "report")
        self.post("/api/report", {**REPORT, "lang": "th"})
        self.assertEqual(calls[0][3], "th")

    def test_the_edges_of_every_limit_are_accepted(self):
        self.routed(main.agent, "report", {"type": "ok"})
        for body in ({**REPORT, "lat": 90, "lon": 180}, {**REPORT, "lat": -90, "lon": -180}, {**REPORT, "text": "x"},
                     {**REPORT, "text": "x" * 2000}, {**REPORT, "lat": "13.5", "lon": "100.5"}):
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.post("/api/report", body).status_code, 200)

    def test_anything_outside_the_limits_is_refused_before_any_agent_work(self):
        calls = self.routed(main.agent, "report")
        for body in ({**REPORT, "lat": 90.01}, {**REPORT, "lat": -91}, {**REPORT, "lon": 180.5}, {**REPORT, "lon": -181},
                     {**REPORT, "lat": "north"}, {**REPORT, "text": ""}, {**REPORT, "text": "x" * 2001}, {**REPORT, "lang": "fr"},
                     {"lat": 1, "lon": 2}, {**REPORT, "text": None}, {}):
            self.assert_rejected("/api/report", body)
        self.assertEqual(calls, [])

    def test_only_post_is_allowed(self):
        self.assertEqual(self.client.get("/api/report").status_code, 405)


class CompareRouteTest(RouteCase):
    def test_passes_the_profile_and_both_sites(self):
        calls = self.routed(main.agent, "compare", {"type": "compare", "compare": {"pick": "A"}})
        r = self.post("/api/compare", {"profile": {"who": "x"}, "a": {"k": 1}, "b": {"k": 2}, "lang": "th"})
        self.assertEqual(lines(r), [{"type": "compare", "compare": {"pick": "A"}}])
        self.assertEqual(calls, [({"who": "x"}, {"k": 1}, {"k": 2}, "th")])

    def test_both_sites_and_the_profile_are_required_objects(self):
        for body in ({"profile": {}, "a": {}}, {"profile": {}, "b": {}}, {"a": {}, "b": {}}, {"profile": [], "a": {}, "b": {}},
                     {"profile": {}, "a": "A", "b": {}}):
            self.assert_rejected("/api/compare", body)


class ChatRouteTest(RouteCase):
    BODY = {"state": {"lat": 1}, "messages": [{"role": "user", "text": "hi"}, {"role": "model", "text": "hello"}], "question": "Why?"}

    def test_passes_the_state_the_history_as_plain_dicts_and_the_question(self):
        calls = self.routed(main.agent, "chat", {"type": "answer", "text": "Because."})
        r = self.post("/api/chat", self.BODY)
        self.assertEqual(lines(r), [{"type": "answer", "text": "Because."}])
        self.assertEqual(calls, [({"lat": 1}, [{"role": "user", "text": "hi"}, {"role": "model", "text": "hello"}], "Why?", "en")])

    def test_limits_on_history_and_question(self):
        self.routed(main.agent, "chat")
        turn = {"role": "user", "text": "x"}
        for body in ({**self.BODY, "messages": []}, {**self.BODY, "messages": [turn] * 20}, {**self.BODY, "question": "?"},
                     {**self.BODY, "question": "q" * 1000}, {**self.BODY, "messages": [{"role": "user", "text": "t" * 4000}]}):
            with self.subTest(body=str(body)[:50]):
                self.assertEqual(self.post("/api/chat", body).status_code, 200)
        for body in ({**self.BODY, "messages": [turn] * 21}, {**self.BODY, "question": ""}, {**self.BODY, "question": "q" * 1001},
                     {**self.BODY, "messages": [{"role": "user", "text": "t" * 4001}]}, {**self.BODY, "messages": [{"role": "system", "text": "x"}]},
                     {**self.BODY, "messages": [{"role": "user"}]}, {**self.BODY, "state": "not a dict"}, {"messages": [], "question": "x"}):
            with self.subTest(refused=str(body)[:50]):
                self.assert_rejected("/api/chat", body)


class ClaimRouteTest(RouteCase):
    def test_passes_the_pin_and_the_date_as_text(self):
        calls = self.routed(main.agent, "claim", {"type": "claim", "result": {"water_at_pin": True}})
        r = self.post("/api/claim", {"lat": 20.3692, "lon": 99.9957, "date": "2024-09-12", "lang": "th"})
        self.assertEqual(lines(r), [{"type": "claim", "result": {"water_at_pin": True}}])
        self.assertEqual(calls, [(20.3692, 99.9957, "2024-09-12", "th")])

    def test_radar_has_nothing_usable_before_12_january_2018(self):
        self.routed(main.agent, "claim")
        base = {"lat": 20.0, "lon": 99.0}
        self.assertEqual(self.post("/api/claim", {**base, "date": "2018-01-12"}).status_code, 200)
        self.assert_rejected("/api/claim", {**base, "date": "2018-01-11"})
        self.assert_rejected("/api/claim", {**base, "date": "1999-12-31"})

    def test_a_date_has_to_be_a_real_date(self):
        self.routed(main.agent, "claim")
        for date in ("2024-13-01", "2024-02-30", "yesterday", "", None, 20240912.5):
            self.assert_rejected("/api/claim", {"lat": 20.0, "lon": 99.0, "date": date}, date)

    def test_the_pin_is_checked_like_everywhere_else(self):
        self.routed(main.agent, "claim")
        self.assert_rejected("/api/claim", {"lat": 91, "lon": 99.0, "date": "2024-09-12"})
        self.assert_rejected("/api/claim", {"lat": 20, "lon": 181, "date": "2024-09-12"})


class PlacesRouteTest(RouteCase):
    def test_searches_by_text_in_the_chosen_language(self):
        found = [{"name": "Condo", "address": "1 Rd", "lat": 1.0, "lon": 2.0}]
        with mock.patch.object(main.places, "search", return_value=found) as search:
            r = self.post("/api/places", {"q": "condo", "lang": "th"})
        self.assertEqual(lines(r), [{"type": "places", "places": found}])
        search.assert_called_once_with("condo", "th")

    def test_a_maps_failure_reaches_the_ui_as_an_error_event_not_a_dead_stream(self):
        patcher = mock.patch.object(main.places, "search", side_effect=RuntimeError("403 Forbidden"))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertLogs(main.log, "ERROR"):
            r = self.post("/api/places", {"q": "condo"})
        self.assertEqual((r.status_code, lines(r)), (200, [{"type": "error", "text": "RuntimeError: 403 Forbidden"}]))

    def test_the_query_is_2_to_200_characters(self):
        patcher = mock.patch.object(main.places, "search", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        for q in ("ab", "q" * 200):
            self.assertEqual(self.post("/api/places", {"q": q}).status_code, 200)
        for q in ("a", "", "q" * 201):
            self.assert_rejected("/api/places", {"q": q})
        self.assert_rejected("/api/places", {"lang": "en"})


class TranscribeRouteTest(RouteCase):
    def test_decodes_the_recording_and_returns_the_transcript(self):
        patcher = mock.patch.object(main.agent, "transcribe", return_value="ซื้อบ้านให้พ่อแม่")
        transcribe = patcher.start()
        self.addCleanup(patcher.stop)
        r = self.post("/api/transcribe", {"audio": base64.b64encode(b"RIFF-audio").decode(), "mime": "audio/webm"})
        self.assertEqual(lines(r), [{"type": "transcript", "text": "ซื้อบ้านให้พ่อแม่"}])
        transcribe.assert_called_once_with(b"RIFF-audio", "audio/webm")

    def test_only_audio_types_without_parameters_are_accepted(self):
        patcher = mock.patch.object(main.agent, "transcribe", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)
        for mime in ("audio/webm", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/wav", "audio/3gpp2"):
            self.assertEqual(self.post("/api/transcribe", {"audio": "QQ==", "mime": mime}).status_code, 200, mime)
        for mime in ("video/mp4", "audio/webm;codecs=opus", "audio/", "text/plain", "audio/we bm", ""):
            self.assert_rejected("/api/transcribe", {"audio": "QQ==", "mime": mime}, mime)

    def test_the_recording_is_capped_at_a_million_characters(self):
        patcher = mock.patch.object(main.agent, "transcribe", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(self.post("/api/transcribe", {"audio": "A" * 1_000_000, "mime": "audio/webm"}).status_code, 200)
        self.assert_rejected("/api/transcribe", {"audio": "A" * 1_000_001, "mime": "audio/webm"})

    def test_a_broken_recording_or_a_gemini_failure_is_an_error_event(self):
        with self.assertLogs(main.log, "ERROR"):
            r = self.post("/api/transcribe", {"audio": "abc", "mime": "audio/webm"})  # not valid base64 (bad padding)
        (event,) = lines(r)
        self.assertEqual(event["type"], "error")
        self.assertIn("padding", event["text"])
        patcher = mock.patch.object(main.agent, "transcribe", side_effect=RuntimeError("Gemini busy"))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertLogs(main.log, "ERROR"):
            r = self.post("/api/transcribe", {"audio": "QQ==", "mime": "audio/webm"})
        self.assertEqual(lines(r), [{"type": "error", "text": "RuntimeError: Gemini busy"}])


class TranslateRouteTest(RouteCase):
    def test_passes_the_strings_and_the_target_language(self):
        calls = self.routed(main.agent, "translate", {"type": "translated", "texts": ["น้ำ"]})
        r = self.post("/api/translate", {"texts": ["Water"], "lang": "th"})
        self.assertEqual(lines(r), [{"type": "translated", "texts": ["น้ำ"]}])
        self.assertEqual(calls, [(["Water"], "th")])

    def test_limits_on_how_many_strings_and_how_long(self):
        self.routed(main.agent, "translate")
        self.assertEqual(self.post("/api/translate", {"texts": ["x"] * 200, "lang": "en"}).status_code, 200)
        self.assertEqual(self.post("/api/translate", {"texts": ["x" * 4000], "lang": "en"}).status_code, 200)
        for body in ({"texts": [], "lang": "en"}, {"texts": ["x"] * 201, "lang": "en"}, {"texts": ["x" * 4001], "lang": "en"},
                     {"texts": ["x"]}, {"texts": ["x"], "lang": "fr"}, {"texts": "x", "lang": "en"}, {"texts": [1], "lang": "en"}, {"lang": "en"}):
            with self.subTest(refused=str(body)[:50]):
                self.assert_rejected("/api/translate", body)

    def test_a_translation_that_comes_back_the_wrong_length_reaches_the_ui_as_an_error(self):
        patcher = mock.patch.object(main.agent, "ask", return_value=main.agent.Texts(texts=["only one"]))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertLogs(main.log, "ERROR"):
            r = self.post("/api/translate", {"texts": ["a", "b"], "lang": "th"})
        self.assertEqual(lines(r), [{"type": "error", "text": "ValueError: translation returned 1 of 2 strings"}])


class StreamFailureTest(RouteCase):
    def test_status_stays_200_and_the_stream_ends_with_an_error_event(self):
        self.routed(main.agent, "report", {"type": "step", "phase": "intake"}, then=RuntimeError("Earth Engine timed out"))
        with self.assertLogs(main.log, "ERROR"):
            r = self.post("/api/report", REPORT)
        self.assertEqual(r.status_code, 200)  # the headers went out with the first event
        self.assertEqual(lines(r), [{"type": "step", "phase": "intake"}, {"type": "error", "text": "RuntimeError: Earth Engine timed out"}])


class AppTest(RouteCase):
    def test_unknown_routes_are_404(self):
        self.assertEqual(self.client.post("/api/nothing", json={}).status_code, 404)

    def test_the_streaming_routes_are_exactly_the_ones_web_server_ts_forwards(self):
        posts = {r.path for r in main.app.routes if "POST" in getattr(r, "methods", ())}
        self.assertEqual(posts, {"/api/report", "/api/compare", "/api/chat", "/api/claim", "/api/places", "/api/transcribe", "/api/translate"})


if __name__ == "__main__":
    unittest.main()
