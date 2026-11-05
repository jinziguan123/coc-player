"""疯狂台词改写：状态判断、模型改写与回退。"""

import asyncio
from types import SimpleNamespace

from app.services import madness_voice


def _char(status: str = "temporary_insanity", label: str = "偏执妄想"):
    return SimpleNamespace(
        id="c1", status=status,
        system_data={"madness": {"label": label, "manifest": "坚信所有人都在密谋害他"}},
    )


def test_non_insane_passes_through():
    assert asyncio.run(madness_voice.rewrite_dialogue("把钥匙给我", _char("active"))) == "把钥匙给我"


def test_insane_rewrites_through_model(monkeypatch):
    class LLM:
        async def complete(self, messages, **kwargs):
            assert "偏执妄想" in messages[0]["content"]
            return "把钥匙给我……不对，你们拿钥匙想干什么？"

    monkeypatch.setattr(madness_voice, "get_fast_llm", lambda: LLM())
    out = asyncio.run(madness_voice.rewrite_dialogue("把钥匙给我", _char()))
    assert "钥匙" in out and out != "把钥匙给我"


def test_model_adding_quotes_falls_back(monkeypatch):
    class LLM:
        async def complete(self, messages, **kwargs):
            return "「把钥匙给我」"

    monkeypatch.setattr(madness_voice, "get_fast_llm", lambda: LLM())
    assert asyncio.run(madness_voice.rewrite_dialogue("把钥匙给我", _char())) == "把钥匙给我"


def test_model_error_falls_back(monkeypatch):
    class LLM:
        async def complete(self, messages, **kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr(madness_voice, "get_fast_llm", lambda: LLM())
    assert asyncio.run(madness_voice.rewrite_dialogue("把钥匙给我", _char())) == "把钥匙给我"
