"""Gemini side of Orra: the five jobs from the spec.

1. intake       free text -> Profile (structured output)
2. plan         Gemini decides which satellite tools to call; every step streams as a trace event
3. read images  Gemini describes the radar and optical chips (vision)
4. judge        profile + facts -> Report; then code drops any evidence whose tool never ran
5. chat         follow-ups answered from the evidence, calling tools again when needed
+  translate    a language switch translates what was already written, in place
+  claim        an insurer's flood claim date checked against the nearest radar pass, without planning

`python agent.py` checks the evidence guardrail offline.
`python agent.py warm` runs the full agent on every test pin in pins.json to fill cache/.
"""
import base64
import functools
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

import eo
import places

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
CACHE = Path(__file__).parent / "cache"
log = logging.getLogger("uvicorn.error")  # uvicorn's logger, so failures reach the server log
MAX_CALLS = 10  # six base tools, up to two flood-image passes, a wider fire search, one spare
LOW = types.ThinkingConfig(thinking_level="LOW", include_thoughts=True)

Weight = Literal["low", "medium", "high"]
Level = Literal["Low", "Medium", "High", "Not enough evidence"]
ToolName = Literal["water_history", "flood_day_images", "water_on_date", "heat", "fire", "air", "change", "nearby", "find_places"]
Lang = Literal["en", "th"]


class Profile(BaseModel):
    who: str
    flood: Weight
    heat: Weight
    smoke: Weight
    change: Weight
    nearby: Weight
    why: str
    floor: int | None
    budget_thb: int | None


class Evidence(BaseModel):
    claim: str
    tool: ToolName
    source: str
    value: str
    date: str


class Chapter(BaseModel):
    key: Literal["water", "heat", "smoke", "change", "nearby"]
    level: Level
    headline: str
    detail: str
    evidence: list[Evidence]


class Report(BaseModel):  # chapters first: Gemini writes in this order, so the call follows from them
    chapters: list[Chapter]
    verdict: list[str]
    overall: Literal["Low", "Medium", "High"]
    checks: list[str]
    questions: list[str]
    lender_note: str


class Compare(BaseModel):
    pick: Literal["A", "B", "Either"]
    reason: str
    tradeoffs: list[str]


class Texts(BaseModel):
    texts: list[str]


INTAKE = """Turn what this person wrote into a risk profile for choosing a home.
Weights flood / heat / smoke / change / nearby are high, medium or low, from who lives there and how:
ground floor, car, baby or children, elderly or health issues, work from home, budget. nearby is how much
they need shops, a hospital, a school or transit close by (no car, elderly, children, or they said so).
`who` is one short third-person line. `why` is one sentence on the highest weights.
floor and budget_thb only if stated (monthly rent or price in Thai baht), else null.
If it is a pasted property listing, infer from it."""

PLAN = """You are the investigator for Orra. You gather satellite evidence about the
ground at one map pin for one person. You do not write the report.
- Cover all five chapters: water (water_history), heat (heat), smoke (fire and air), change (change),
  nearby (nearby).
- Start with what this person's profile weights highest. Independent tools can run in parallel.
- Before each tool call write one short plain sentence saying why; the person sees it.
- If water_history finds flood events, call flood_day_images for the worst event and, if it is a
  different one, the latest event, passing their exact `time` values.
- Call fire with radius_km 20. If that shows many dry-season fire-days (over ~50) or smoke matters a
  lot to this person, call fire again with radius_km 50.
- If the person names a place or brand they need (a Big C, their office, a particular school), also call
  find_places for it.
- When you have the evidence, say in one sentence that you are done. Never state findings."""

JUDGE = """Write the Orra report for this person, using only the evidence JSON below.
- Every evidence item comes from the JSON: `tool` is the tool that produced it, `source` is that
  result's source (shortened is fine), `value` is the number or fact with units, `date` is the date or
  period it covers. Never claim anything without an evidence item. Put the most telling item first:
  the person sees only that one until they open the chapter.
- A chapter whose tool failed, was not run, or reported missing data has level "Not enough evidence",
  and its detail says what is missing.
- Levels are Low / Medium / High with plain reasons, weighed by the profile (flood matters more for a
  ground floor or a car, smoke for a baby, heat for elderly people). No probabilities.
- water: flood events, the worst and latest (share of nearby ground flooded, whether water reached the
  pin), HAND. Mention that radar can miss water between buildings when it matters.
- heat: judge by delta_c, the pin against the 2 km average. Surface temperature is not air temperature
  and runs high everywhere in the hot season, so a high value alone is no reason for concern or for
  bigger power bills. smoke: dry-season fire-days within 5 km and the radius, peak months, direction
  and whether hotspots are upwind; air is PM2.5 for the last 30 days only, so call it recent air and
  never let a clean month outweigh years of fire-days. change: land-cover change and what the images show. nearby:
  straight-line distance to the shops, hospital, school or transit this person needs; it is not travel time.
- headline: one plain sentence of at most 12 words (in Thai about 50 characters) saying whether this
  is a problem for this person and why. Round numbers, no date ranges.
- detail: 2-4 sentences with the key numbers and dates.
- verdict: exactly 2 lines. 1) The call for this person in at most 8 words: an action or plain judgement
  they can act on without reading any rating, e.g. "Sign only if it comes with air conditioning",
  "A bad spot for your needs", "A good fit: go ahead". It follows from the chapters you rated highest
  and gives no reason that no chapter supports. If they asked a question, this answers it.
  2) Why, in 1-2 plain sentences naming the biggest risks with their numbers and dates.
- checks: 1-3 things to check or negotiate before signing, most important first, each an instruction
  of at most 10 words. What this person cares about but the evidence cannot measure (travel time,
  noise, building quality) goes here, never into a level.
- questions: 3 short follow-up questions this person would likely ask next about these findings,
  in their own voice.
- lender_note: 2-3 neutral sentences for a bank or insurer on collateral risk here, with dates.
- Plain words, no jargon."""

COMPARE = """Two sites, A and B, were checked for the same person. Using only their reports, pick
the better site for this person, or "Either" if there is no clear difference. Give the main reason in
1-2 sentences and list the trade-offs, one line each, naming the chapter. Weigh by the profile."""

CHAT = """You answer follow-up questions about one location's Orra report. The profile, report
and evidence are below. Answer in at most 2 short sentences (under 40 words): the direct answer first,
then the one number or date behind it. Call a tool if you need more evidence. If satellites cannot
answer (flood depth, building quality, noise, neighbours), say so in one sentence and name what would
answer it (site visit, flood marks on walls, asking neighbours). For a question about one date, such as an
insurance claim, call water_on_date. For a named place, brand or kind of place (Big C, 7-Eleven, a hospital
with an emergency room), call find_places and give the nearest match's name and straight-line km; it is not
travel time."""

TRANSLATE = """Translate each string in this JSON list, taken from one location's Orra report. Return exactly as
many strings, in the same order. Translate the meaning in plain words and add nothing. Keep numbers, units,
dates, place names and dataset names."""

FLOOD_LOOK = """Two radar images of the same 3 km area. In 2 sentences, say where the cyan flood water
is relative to the white ring that marks the pin (direction, rough distance, along a river or road?) and
whether it touches the pin. Do not guess anything you cannot see."""

CHANGE_LOOK = """'before' and 'after' true-colour satellite images of the same 2 km area. In 2-3
sentences, name the clearest visible changes (new buildings, roads, cleared or planted land, water)
with direction and rough distance from the white ring that marks the pin. If nothing clearly changed, say so. Use
"likely" for building types and do not guess beyond what is visible."""

WRITE = {"en": "Write in English.",  # appended to every prompt whose text the person reads
         "th": "Write in Thai (ภาษาไทย), in plain everyday words. Keep numbers, units, dataset names and "
               "Gregorian years (e.g. 2024, not 2567) as they are."}


_client_lock = threading.Lock()


@functools.cache
def _client():
    return genai.Client(enterprise=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=4)))


def client():
    with _client_lock:  # without it, two first calls build two clients and the uncached one closes when collected
        return _client()


def ask(contents, schema=None, thinking=LOW, system=None):
    config = types.GenerateContentConfig(system_instruction=system, thinking_config=thinking, response_schema=schema,
                                         response_mime_type="application/json" if schema else None)
    resp = client().models.generate_content(model=MODEL, contents=contents, config=config)
    return resp.parsed if schema else resp.text.strip()


class Heard(BaseModel):
    text: str


HEAR = """Transcribe this recording for Orra: someone saying who will live in a home and how.
Write what they said in the language they spoke, leaving out filler words (um, เอ่อ) and false starts.
text is only that, or empty if nobody speaks."""


def transcribe(audio, mime):
    """Speech for the profile box. Gemini hears it, so it works in browsers without speech recognition (Brave, Firefox)."""
    return ask([types.Content(role="user", parts=[types.Part.from_bytes(data=audio, mime_type=mime)])], Heard,
               system=HEAR).text.strip()


def png(b64):
    return types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/png")


def strip_images(x):
    """Facts only. One PNG is >100k tokens, so nothing with images goes into a prompt."""
    if isinstance(x, dict):
        return {k: strip_images(v) for k, v in x.items() if k != "images"}
    if isinstance(x, list):
        return [strip_images(v) for v in x]
    return x


def cached(fn):
    """Tool results (facts + chips + vision text) on disk: a site run before is instant and survives EE hiccups."""
    @functools.wraps(fn)
    def wrap(*args):
        path = CACHE / (fn.__name__ + "-" + re.sub(r"[^\w.-]", "_", "_".join(map(str, args))) + ".json")
        if path.exists():
            return json.loads(path.read_text())
        out = fn(*args)
        CACHE.mkdir(exist_ok=True)
        path.write_text(json.dumps(out))
        return out
    return wrap


def see(images, notes, look, lang):
    """Job 3: Gemini reads the chips."""
    parts = []
    for name, b64 in images.items():
        parts += [types.Part(text=f"{name}:"), png(b64)]
    return ask(parts + [types.Part(text=f"{notes}\n{look}\n{WRITE[lang]}")])


@cached
def _water_history(lat, lon):
    return eo.water_history(lat, lon)


@cached
def _flood_day_images(lat, lon, event_time):
    return eo.flood_day_images(lat, lon, event_time)


@cached
def _flood_look(lat, lon, event_time, lang):  # cached apart from the chips: a new language re-reads them, not EE
    r = _flood_day_images(lat, lon, event_time)
    return see(r["images"], r["image_notes"], FLOOD_LOOK, lang)


@cached
def _water_on_date(lat, lon, day):
    return eo.flood_day_images(lat, lon, day, 6)  # S1 revisit is 6-12 days, so a pass is within 6 days either side


@cached
def _water_on_date_look(lat, lon, day, lang):
    r = _water_on_date(lat, lon, day)
    return see(r["images"], r["image_notes"], FLOOD_LOOK, lang)


@cached
def _heat(lat, lon):
    return eo.heat(lat, lon)


@cached
def _fire(lat, lon, radius_km):
    return eo.fire(lat, lon, radius_km)


@cached
def _change(lat, lon):
    return eo.change(lat, lon)


@cached
def _change_look(lat, lon, lang):
    r = _change(lat, lon)
    return see(r["images"], r["image_notes"], CHANGE_LOOK, lang)


def make_tools(lat, lon, lang):
    """Tools bound to the pin, so the model never types coordinates."""
    lat, lon = round(lat, 4), round(lon, 4)  # ~11 m: clicks that close share one cache entry

    def water_history() -> dict:
        """Radar flood history since 2018 for flood-prone ground within 1 km of the pin, plus height above
        nearest drainage (HAND). Returns flood events with their `time`."""
        return _water_history(lat, lon)

    def flood_day_images(event_time: str) -> dict:
        """Radar images for one flood event: the usual view vs that pass with flood water in blue and the
        pin marked, plus whether water reached the pin. event_time must be a `time` from water_history."""
        w = _water_history(lat, lon)
        t = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        match = [e for e in [w.get("worst"), w.get("latest"), *(w.get("events") or [])]
                 if e and abs(datetime.fromisoformat(e["time"]) - t) <= timedelta(days=1)]
        if not match:
            return {"error": "event_time must be one of the event `time` values from water_history"}
        r = _flood_day_images(lat, lon, match[0]["time"])
        return {**r, "visual_description": _flood_look(lat, lon, match[0]["time"], lang)} if r.get("images") else r

    def water_on_date(date: str) -> dict:
        """Radar check of one date, such as an insurance claim's: the radar pass nearest that date (within 6
        days either side), whether water reached the pin, and the images. date: YYYY-MM-DD."""
        day = datetime.fromisoformat(date[:10]).date().isoformat()  # validated, so it is also a safe cache key
        r = _water_on_date(lat, lon, day)
        return {**r, "visual_description": _water_on_date_look(lat, lon, day, lang)} if r.get("images") else r

    def heat() -> dict:
        """Hot-season (Mar-May) land surface temperature at the pin vs the 2 km average, from Landsat."""
        return _heat(lat, lon)

    def fire(radius_km: int) -> dict:
        """Dry-season fire hotspots near the pin (NASA FIRMS) by year and month, the main hotspot
        direction and the dry-season wind. radius_km: 20 normally, 50 to look wider."""
        return _fire(lat, lon, 50 if radius_km > 20 else 20)

    def air() -> dict:
        """PM2.5 at the pin over the last 30 days (Google Air Quality API): the mean, the worst day and days over
        Thailand's 24-hour standard. 30 days is all the API keeps, so it says nothing about other seasons."""
        return places.air(lat, lon)

    def change() -> dict:
        """Land-cover change within 1 km since 2017/18 (Dynamic World), with before/after Sentinel-2
        images described by Gemini."""
        return {**_change(lat, lon), "visual_description": _change_look(lat, lon, lang)}

    def nearby() -> dict:
        """The nearest shops, hospital, school and transit stops within 10 km (Google Maps Places), with
        straight-line distances from the pin."""
        return places.nearby(lat, lon, lang)

    def find_places(query: str) -> dict:
        """Google Maps search around the pin for a named place, brand or kind of place ("Big C", "international
        school", "hospital with an emergency room"): up to 5 matches, nearest first, with straight-line km."""
        return places.find(lat, lon, query[:100], lang)

    return {f.__name__: f for f in (water_history, flood_day_images, water_on_date, heat, fire, air, change, nearby, find_places)}


def run_tools(system, contents, tools, evidence):
    """Job 2: Gemini plans and calls tools. Yields trace events; returns the final text."""
    config = types.GenerateContentConfig(
        system_instruction=system, tools=list(tools.values()), thinking_config=LOW,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    calls, text = 0, ""
    while True:
        content = client().models.generate_content(model=MODEL, contents=contents, config=config).candidates[0].content
        contents.append(content)  # as returned: keeps Gemini's thought signatures
        fcs = []
        for part in content.parts or []:
            if part.function_call:
                fcs.append(part.function_call)
            elif part.text and part.thought:
                yield {"type": "thought", "text": part.text.strip()}
            elif part.text:
                text = part.text.strip()
                yield {"type": "say", "text": text}
        if not fcs:
            return text
        for fc in fcs:
            yield {"type": "call", "name": fc.name, "args": dict(fc.args or {})}

        def run(fc):
            try:
                return tools[fc.name](**(fc.args or {}))
            except Exception as e:  # the agent should say "not enough evidence", not crash
                log.warning("tool %s failed", fc.name, exc_info=True)
                return {"error": f"{type(e).__name__}: {e}"}

        with ThreadPoolExecutor() as pool:
            results = list(pool.map(run, fcs))
        parts = []
        for fc, r in zip(fcs, results):
            evidence.append({"tool": fc.name, "args": dict(fc.args or {}), "result": r})
            yield {"type": "result", "name": fc.name, "result": strip_images(r)}  # UI gets images once, in the report
            parts.append(types.Part(function_response=types.FunctionResponse(
                id=fc.id, name=fc.name, response=strip_images(r))))
        contents.append(types.Content(role="user", parts=parts))
        calls += len(fcs)
        if calls >= MAX_CALLS:  # budget enforced in code, not just in the prompt
            config.tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE"))


def enforce_evidence(rep, ran):
    """Evidence rule, in code: keep only items whose tool actually ran; an empty chapter has no evidence."""
    dropped = []
    for ch in rep.chapters:
        dropped += [e.claim for e in ch.evidence if e.tool not in ran]
        ch.evidence = [e for e in ch.evidence if e.tool in ran]
        if not ch.evidence:
            ch.level = "Not enough evidence"
    return rep, dropped


def user(text):
    return types.Content(role="user", parts=[types.Part(text=text)])


def report(lat, lon, text, lang):
    yield {"type": "step", "phase": "intake", "text": "Reading what you told us"}
    profile = ask([user(text)], Profile, system=f"{INTAKE}\n{WRITE[lang]}")
    yield {"type": "profile", "profile": profile.model_dump()}

    evidence = []
    contents = [user(f"Pin: {lat:.5f}, {lon:.5f}\nProfile: {profile.model_dump_json()}\nInvestigate this pin.")]
    yield from run_tools(f"{PLAN}\n{WRITE[lang]}", contents, make_tools(lat, lon, lang), evidence)

    yield {"type": "step", "phase": "judge", "text": "Weighing the evidence for you"}
    facts = json.dumps(strip_images(evidence))
    rep = ask([user(f"Person wrote: {text}\nProfile: {profile.model_dump_json()}\nEvidence: {facts}")], Report,
              thinking=types.ThinkingConfig(include_thoughts=False), system=f"{JUDGE}\n{WRITE[lang]}")
    rep, dropped = enforce_evidence(rep, {e["tool"] for e in evidence if "error" not in e["result"]})
    if dropped:
        yield {"type": "guardrail", "dropped": dropped}
    yield {"type": "report", "state": {"lat": lat, "lon": lon, "text": text, "lang": lang, "profile": profile.model_dump(),
                                       "report": rep.model_dump(), "evidence": evidence}}


def compare(profile, a, b, lang):
    yield {"type": "step", "phase": "compare", "text": "Comparing the two places for you"}
    body = json.dumps({"profile": profile, "A": strip_images(a), "B": strip_images(b)})
    yield {"type": "compare", "compare": {**ask([user(body)], Compare, system=f"{COMPARE}\n{WRITE[lang]}").model_dump(),
                                          "lang": lang}}


def chat(state, messages, question, lang):
    evidence = []
    system = f"{CHAT}\n{WRITE[lang]}\n\n" + json.dumps(strip_images(
        {k: state[k] for k in ("profile", "report", "evidence")}))
    contents = [types.Content(role=m["role"], parts=[types.Part(text=m["text"])]) for m in messages]
    contents.append(user(question))
    answer = yield from run_tools(system, contents, make_tools(state["lat"], state["lon"], lang), evidence)
    yield {"type": "answer", "text": answer, "evidence": evidence}


def claim(lat, lon, day, lang):
    """Flood claim check for an insurer: one tool and no planning, so a date always gets the same answer."""
    yield {"type": "call", "name": "water_on_date", "args": {"date": day}}
    yield {"type": "claim", "result": make_tools(lat, lon, lang)["water_on_date"](day)}


def translate(texts, lang):
    """A language switch: what Gemini already wrote, translated, so levels, numbers and sources stay as they were."""
    out = ask([user(json.dumps(texts, ensure_ascii=False))], Texts, system=f"{TRANSLATE}\n{WRITE[lang]}").texts
    if len(out) != len(texts):  # each string goes back to the field it came from, so a dropped line would shift them all
        raise ValueError(f"translation returned {len(out)} of {len(texts)} strings")
    yield {"type": "translated", "texts": out}


if __name__ == "__main__":
    if sys.argv[1:] == ["warm"]:
        for p in json.loads((Path(__file__).parent / "pins.json").read_text()):
            for lang, text in (("en", p["text"]), ("th", p["th"]["text"])):  # th too: image readings are cached per language
                for ev in report(p["lat"], p["lon"], text, lang):
                    print(p["id"], lang, ev["type"], json.dumps(strip_images(ev), ensure_ascii=False)[:160])
    else:
        r = Report(verdict=["a", "b"], overall="Low", checks=["c"], questions=["q"], lender_note="n", chapters=[
            Chapter(key="water", level="High", headline="h", detail="d",
                    evidence=[Evidence(claim="planted", tool="heat", source="s", value="v", date="d")]),
            Chapter(key="smoke", level="Low", headline="h", detail="d",
                    evidence=[Evidence(claim="real", tool="fire", source="s", value="v", date="d")])])
        r, dropped = enforce_evidence(r, {"fire", "water_history"})
        assert dropped == ["planted"] and r.chapters[0].level == "Not enough evidence", (dropped, r)
        assert r.chapters[1].level == "Low" and len(r.chapters[1].evidence) == 1
        assert strip_images({"a": [{"images": {"x": "y"}, "b": 1}]}) == {"a": [{"b": 1}]}
        print("agent self-check passed")
