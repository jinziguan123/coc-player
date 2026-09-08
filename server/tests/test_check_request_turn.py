"""玩家申请检定之后的两类故障（鬼屋一局实测）：

1. 旁白里被提及的词被抽成台词，引号前的半句话成了说话人（「方才」「她讲」「她把」「话里始终没有」）。
2. 申请检定轮 KP 该只回一行指令，小模型却先写一段揣测性旁白，骰子未定先演结果。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import chat_service, narration_protocol, session_service
from app.services.turn_orchestrator import _check_request_narration_keep
from tests.test_chat_service import _patch_runtime


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


NPCS = [{"name": "加布里埃尔·马卡里奥"}, {"name": "玛莎"}]


def _dialogues(text):
    async def gen():
        yield text

    async def run():
        r = ["", "", [], [], []]
        async for _ in narration_protocol.filter_narration_stream(gen(), r, npcs=NPCS, party_names={"陈守一"}):
            pass
        return r
    return asyncio.run(run())[2]


@pytest.mark.parametrize("text", [
    "她方才说“教堂的事，我说不上更多”的时候，指尖在碎布包边沿顿了一拍。",
    "她讲“那本书”“不许我碰”的时候，语调里那点极力压住的僵，不像编的。",
    "她把“他”的事说得很慢，很平。",
    "话里始终没有“孩子”这两个字。",
])
def test_被提及的引号不抽成台词(text):
    assert _dialogues(text) == []


@pytest.mark.parametrize("text, speaker", [
    ("玛莎说道：“别过去。”", "玛莎"),
    ("老板娘：“今天不卖。”", "老板娘"),
    ("一个护工低声说：“他又闹了。”", "护工"),
    ("护士长：“探视时间到了。”", "护士长"),
])
def test_真正的说话前缀仍然抽得出(text, speaker):
    assert _dialogues(text) == [(speaker, text.split("“")[1].rstrip("”"))]


def test_申请检定轮旁白去留判定():
    directive = "[DICE_CHECK: skill=侦查, difficulty=normal, char=陈守一]"
    long = "陈守一的目光落在加布里埃尔脸上。她仍低着头缝那件针线，针脚一针一针，匀称、细密，手上的动作没有半分迟疑。" * 2
    assert _check_request_narration_keep(["", directive, [], [], []]) is True            # 只回指令
    assert _check_request_narration_keep(["这无需检定，你一眼便看清了。", "", [], [], []]) is True   # 明说免检
    assert _check_request_narration_keep(["她的针脚没有停。", directive, [], [], []]) is True     # 一句引子
    assert _check_request_narration_keep([long, directive, [], [], []]) is False        # 长旁白 + 指令
    assert _check_request_narration_keep([long, "", [], [], []]) is False               # 长旁白无指令


def _seed(db):
    module = Module(title="鬼屋", rule_system="coc", npcs=[{"name": "加布里埃尔·马卡里奥"}],
                    scenes=[{"id": "ward", "name": "病房"}])
    pc = Character(name="陈守一", rule_system="coc", is_player=True, skills={"侦查": 55})
    db.add_all([module, pc]); db.flush()
    session = GameSession(module_id=module.id, player_character_id=pc.id, status="active",
                          current_scene_id="ward", world_state={})
    db.add(session); db.commit()
    return module, pc, session


def _run_check_turn(db_factory, monkeypatch, narration, commands):
    _patch_runtime(monkeypatch, db_factory)
    sent = []
    from app.services.room_hub import room_hub
    monkeypatch.setattr(room_hub, "broadcast", lambda room, chunk: sent.append(chunk))

    async def fake_stream(kp, messages, result, npcs=None, **kwargs):
        result[0] = narration
        result[1] = commands
        yield chat_service._make_chunk("narration", narration, actor_name="KP")

    monkeypatch.setattr(chat_service, "_stream_narration_filtered", fake_stream)
    db = db_factory()
    module, pc, session = _seed(db)
    session_service.add_event(db, session.id, "action", "（申请「侦查」检定：看看针线包）", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(chat_service._run_kp_turn(
        db, session.id, session, module, pc, [],
        "玩家「陈守一」申请用「侦查」检定，请裁定",
        requested_check=(pc, "侦查"), focus_member=pc.name, split_aware=True,
    ))
    evs = session_service.get_session_events(db_factory(), session.id)
    return sent, evs


def test_申请检定轮_长揣测旁白被丢弃_检定仍补挂(db_factory, monkeypatch):
    long = "陈守一的目光落在加布里埃尔脸上。她仍低着头缝那件针线，针脚一针一针，匀称、细密，手上的动作没有半分迟疑。心里有个影子若隐若现地晃着，还没成形。"
    sent, evs = _run_check_turn(db_factory, monkeypatch, long, "")
    assert not [e for e in evs if e.event_type == "narration"]                 # 旁白没落库
    assert not [c for c in sent if '"type": "narration"' in str(c) or "narration" == getattr(c, "type", None)]
    assert any(e.event_type == "system" and "侦查" in (e.content or "") for e in evs)  # 检定确定性补挂


def test_申请检定轮_明说免检的旁白保留(db_factory, monkeypatch):
    sent, evs = _run_check_turn(db_factory, monkeypatch, "这无需检定，针线包就摆在那儿，你一眼便看清里面只有针和线。", "")
    narr = [e for e in evs if e.event_type == "narration"]
    assert len(narr) == 1 and "无需检定" in narr[0].content
    assert not any(e.event_type == "system" and "侦查" in (e.content or "") for e in evs)  # 尊重免检裁定
