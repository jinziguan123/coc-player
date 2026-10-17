import asyncio

from app.services.narration_protocol import filter_narration_stream


async def _tokens(text):
    for ch in text:
        yield ch


class _Guard:
    def __init__(self):
        self.calls = []

    def named_id(self, speaker):
        return "butler" if speaker == "管家" else None

    async def rewrite(self, speaker, trigger):
        self.calls.append((speaker, trigger))
        return "管家", "欢迎光临。"


def test_named_say_is_rewritten_and_marks_updated():
    result = ["", "", [], [], []]
    guard = _Guard()

    async def run():
        return [
            c async for c in filter_narration_stream(
                _tokens("他抬起头。[SAY: who=管家]欢迎。[/SAY]"),
                result, npcs=[{"id": "butler", "name": "管家"}], npc_guard=guard,
            )
        ]

    chunks = asyncio.run(run())
    dialogue = [c for c in chunks if c.type == "npc_dialogue"]
    assert len(dialogue) == 1
    assert dialogue[0].content == "欢迎光临。"
    assert dialogue[0].actor_name == "管家"
    assert dialogue[0].metadata is None
    assert not guard.calls[0][0] == ""
    assert guard.calls[0][1] == "欢迎。"
    assert result[3] == [(5, "管家", "欢迎光临。")]
    assert result[2] == [("管家", "欢迎光临。")]


def test_unnamed_say_stays_and_private_speaker_is_stripped():
    result = ["", "", [], [], []]

    async def run():
        return [
            c async for c in filter_narration_stream(
                _tokens("[SAY: who=路人]借过。[/SAY]"),
                result, npcs=[{"id": "butler", "name": "管家"}], npc_guard=_Guard(),
            )
        ]

    chunks = asyncio.run(run())
    dialogue = [c for c in chunks if c.type == "npc_dialogue"]
    assert len(dialogue) == 1
    assert dialogue[0].content == "借过。"
    assert dialogue[0].metadata is None
    assert result[3] == [(0, "路人", "借过。")]


def test_no_guard_still_strips_private_speaker():
    result = ["", "", [], [], []]

    async def run():
        return [
            c async for c in filter_narration_stream(
                _tokens("[SAY: who=管家]欢迎。[/SAY]"), result,
            )
        ]

    chunks = asyncio.run(run())
    dialogue = [c for c in chunks if c.type == "npc_dialogue"]
    assert dialogue and dialogue[0].metadata is None
