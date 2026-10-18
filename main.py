"""Orra API: the streaming routes. web/server.ts sits in front: sign-in, pages, admin."""
import base64
import datetime as dt
import json
import logging
from typing import Annotated, Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import agent
import places

app = FastAPI(title="Orra")
log = logging.getLogger("uvicorn.error")


class ReportReq(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    text: str = Field(min_length=1, max_length=2000)
    lang: agent.Lang = "en"


class CompareReq(BaseModel):
    profile: dict
    a: dict
    b: dict
    lang: agent.Lang = "en"


class Message(BaseModel):
    role: Literal["user", "model"]
    text: str = Field(max_length=4000)


class ChatReq(BaseModel):
    state: dict
    messages: list[Message] = Field(max_length=20)
    question: str = Field(min_length=1, max_length=1000)
    lang: agent.Lang = "en"


class ClaimReq(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    date: dt.date = Field(ge=dt.date.fromisoformat(agent.eo.S1_START))  # no usable radar before this
    lang: agent.Lang = "en"


class PlacesReq(BaseModel):
    q: str = Field(min_length=2, max_length=200)
    lang: agent.Lang = "en"


class SpeechReq(BaseModel):
    audio: str = Field(max_length=1_000_000)  # base64; the front door also caps bodies at 1 MB
    mime: str = Field(pattern=r"^audio/[\w.+-]+$")


class TranslateReq(BaseModel):
    texts: list[Annotated[str, Field(max_length=4000)]] = Field(min_length=1, max_length=200)
    lang: agent.Lang


def ndjson(events):
    try:
        for ev in events:
            yield json.dumps(ev) + "\n"
    except Exception as e:  # headers are already sent: tell the UI instead of leaving it spinning
        log.exception("stream failed")
        yield json.dumps({"type": "error", "text": f"{type(e).__name__}: {e}"}) + "\n"


def stream(events):
    return StreamingResponse(ndjson(events), media_type="application/x-ndjson")


@app.post("/api/report")
def report(req: ReportReq):
    return stream(agent.report(req.lat, req.lon, req.text, req.lang))


@app.post("/api/compare")
def compare(req: CompareReq):
    return stream(agent.compare(req.profile, req.a, req.b, req.lang))


@app.post("/api/chat")
def chat(req: ChatReq):
    return stream(agent.chat(req.state, [m.model_dump() for m in req.messages], req.question, req.lang))


@app.post("/api/claim")
def claim(req: ClaimReq):
    return stream(agent.claim(req.lat, req.lon, req.date.isoformat(), req.lang))


def found(q, lang):  # a generator, so a Places failure reaches the UI as an error event
    yield {"type": "places", "places": places.search(q, lang)}


@app.post("/api/places")
def place_search(req: PlacesReq):
    return stream(found(req.q, req.lang))


def heard(audio, mime):  # a generator, so a Gemini failure reaches the UI as an error event
    yield {"type": "transcript", "text": agent.transcribe(base64.b64decode(audio), mime)}


@app.post("/api/transcribe")
def transcribe(req: SpeechReq):
    return stream(heard(req.audio, req.mime))


@app.post("/api/translate")
def translate(req: TranslateReq):
    return stream(agent.translate(req.texts, req.lang))

