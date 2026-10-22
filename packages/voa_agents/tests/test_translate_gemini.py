"""GeminiTranslator with a fake genai client: one entry per turn in order,
bounded retries, chunking, never a partial or made-up result."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from voa_agents import translate as gt
from voa_core.translate import TranslationFailed, TurnTranslation


class FakeModels:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def generate_content(self, *, model, contents, config):
        self.requests.append((model, contents, config))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(contents)
        return SimpleNamespace(parsed=None, text=json.dumps(r))


def _client(replies):
    models = FakeModels(replies)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), models


def _echo(contents):
    """Translate every turn to "EN:<text>" with lang es; English stays."""
    data = json.loads(contents.split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0])
    return {
        "turns": [
            {"i": d["i"], "lang": "es", "english": False, "text_en": "EN:" + d["text"]}
            for d in data
        ]
    }


def _t(client, **kw):
    return gt.GeminiTranslator(model="gemini-2.5-flash", client=client, backoff_s=0, **kw)


def test_one_entry_per_turn_in_order():
    reply = {
        "turns": [
            {"i": 0, "lang": "es", "english": False, "text_en": "I don't know."},
            {"i": 1, "lang": "en", "english": True, "text_en": ""},
        ]
    }
    client, models = _client([reply])
    out = asyncio.run(_t(client).translate_turns(["No sé.", "OK"]))
    assert out == [TurnTranslation("es", "I don't know."), TurnTranslation("en", None)]
    model, contents, config = models.requests[0]
    assert model == "gemini-2.5-flash" and '"No sé."' in contents
    assert config.thinking_config.thinking_budget == 0 and config.temperature == 0
    assert "never summarise" in config.system_instruction.lower()


def test_a_wrong_count_is_retried_then_fails():
    bad = {"turns": [{"i": 0, "lang": "es", "english": False, "text_en": "Yes"}]}
    client, models = _client([bad, bad, bad])
    with pytest.raises(TranslationFailed):
        asyncio.run(_t(client).translate_turns(["Sí", "No"]))
    assert len(models.requests) == 3  # bounded


def test_a_missing_translation_is_not_accepted():
    empty = {"turns": [{"i": 0, "lang": "es", "english": False, "text_en": ""}]}
    good = {"turns": [{"i": 0, "lang": "es", "english": False, "text_en": "Yes"}]}
    client, models = _client([empty, good])
    assert asyncio.run(_t(client).translate_turns(["Sí"])) == [TurnTranslation("es", "Yes")]
    assert len(models.requests) == 2


def test_out_of_order_indexes_are_rejected():
    swapped = {
        "turns": [
            {"i": 1, "lang": "es", "english": False, "text_en": "No"},
            {"i": 0, "lang": "es", "english": False, "text_en": "Yes"},
        ]
    }
    client, _ = _client([swapped])
    with pytest.raises(TranslationFailed):
        asyncio.run(_t(client, attempts=1).translate_turns(["Sí", "No"]))


def test_errors_are_retried():
    client, models = _client([RuntimeError("429"), _echo])
    out = asyncio.run(_t(client).translate_turns(["Sí"]))
    assert out == [TurnTranslation("es", "EN:Sí")] and len(models.requests) == 2


def test_long_transcripts_are_chunked_and_reassembled():
    turns = [f"frase número {i}" for i in range(7)]
    client, models = _client([_echo, _echo, _echo])
    out = asyncio.run(_t(client, max_turns=3).translate_turns(turns))
    assert [o.text_en for o in out] == [f"EN:{t}" for t in turns]
    assert len(models.requests) == 3


def test_chunks():
    assert gt.chunks(["a", "b", "c"], 100, 2) == [(0, ["a", "b"]), (2, ["c"])]
    assert gt.chunks(["x" * 10, "y" * 10, "z"], 15, 50) == [
        (0, ["x" * 10]),
        (10 // 10, ["y" * 10, "z"]),
    ]
    assert gt.chunks([], 10, 10) == []


def test_from_config():
    from voa_core.config import load_config

    cfg = load_config(environ={"VOA_ENV": "test", "VOA__GCP__PROJECT_ID": "test-project"})
    t = gt.GeminiTranslator.from_config(cfg)
    assert t.model == cfg.models.translate == "gemini-2.5-flash"
    assert t._kwargs == {"enterprise": True, "project": "test-project", "location": "global"}
    assert t._thinking == 0
