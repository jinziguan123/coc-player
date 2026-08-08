"""模组经历归档与角色头像。

这两件事共同回答一个问题：一张角色卡跑完一个本之后，它身上应该留下什么。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import character_chronicle


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _LLM:
    """按脚本返回小传；返回 Exception 实例表示这一次抛异常。"""

    def __init__(self, *scripted):
        self.scripted, self.calls, self.seen = list(scripted or ["一段小传。"]), 0, []

    async def complete(self, messages, temperature=0.7, **kw):
        self.calls += 1
        self.seen.append(messages)
        out = self.scripted[min(self.calls - 1, len(self.scripted) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


def _seed(db, *, n_players=1, status="active", with_summary=True):
    module = Module(title="渡口来信", rule_system="coc")
    db.add(module)
    db.flush()
    chars = [
        Character(name=f"调查员{i}", rule_system="coc", is_player=True, status=status,
                  module_id=module.id, system_data={"occupation": "报馆校对"})
        for i in range(n_players)
    ]
    db.add_all(chars)
    db.flush()
    ws = {"ending_reached": {"id": "e1", "name": "真相大白"}}
    if with_summary:
        ws["story_chapters"] = [{
            "text": "调查员在当铺见了老板，随后赶往渡口接头。", "from_seq": 1, "to_seq": 40,
        }]
    gs = GameSession(module_id=module.id, player_character_id=chars[0].id,
                     status="ended", world_state=ws)
    db.add(gs)
    db.commit()
    return gs, module, chars


def _patch_party(monkeypatch, chars):
    monkeypatch.setattr(
        character_chronicle.session_service, "get_party_members",
        lambda db, sid, **kw: chars,
    )


def _patch_llm(monkeypatch, llm):
    monkeypatch.setattr(character_chronicle, "get_llm", lambda: llm)


# ── 归档主流程 ──────────────────────────────────────────────────────────


def test_归档写入小传与元数据(db, monkeypatch):
    gs, module, chars = _seed(db)
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM("他在渡口截下了那封信，却没能救回当铺老板。"))

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 1
    exp = (db.get(Character, chars[0].id).experiences or [])
    assert len(exp) == 1
    e = exp[0]
    assert "渡口" in e["story"]                      # 叙事正文
    assert e["module_title"] == "渡口来信"           # 元数据供档案卡显示
    assert e["ending_name"] == "真相大白"
    assert e["session_id"] == gs.id and e["survived"] is True


def test_每人各写各的(db, monkeypatch):
    """同一局里有人活有人死、有人查到真相有人没有，共用一段总结会把差别抹平。"""
    gs, module, chars = _seed(db, n_players=3)
    _patch_party(monkeypatch, chars)
    llm = _LLM("甲的小传", "乙的小传", "丙的小传")
    _patch_llm(monkeypatch, llm)

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 3
    assert llm.calls == 3
    stories = {db.get(Character, c.id).experiences[0]["story"] for c in chars}
    assert stories == {"甲的小传", "乙的小传", "丙的小传"}


def test_死亡状态写进小传素材(db, monkeypatch):
    gs, module, chars = _seed(db, status="dead")
    _patch_party(monkeypatch, chars)
    llm = _LLM()
    _patch_llm(monkeypatch, llm)

    asyncio.run(character_chronicle.archive_session(db, gs.id))
    prompt = str(llm.seen[0])
    assert "死亡" in prompt                                    # 提示词知道他死了
    assert db.get(Character, chars[0].id).experiences[0]["survived"] is False


def test_同一局不重复归档(db, monkeypatch):
    """结束流程可能被重入，不能记两笔。"""
    gs, module, chars = _seed(db)
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM())

    asyncio.run(character_chronicle.archive_session(db, gs.id))
    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 0
    assert len(db.get(Character, chars[0].id).experiences) == 1


def test_多局累积成档案(db, monkeypatch):
    gs, module, chars = _seed(db)
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM())
    asyncio.run(character_chronicle.archive_session(db, gs.id))

    gs2 = GameSession(module_id=module.id, player_character_id=chars[0].id, status="ended",
                      world_state={"story_chapters": [{"text": "第二个故事。",
                                                       "from_seq": 1, "to_seq": 9}]})
    db.add(gs2)
    db.commit()
    asyncio.run(character_chronicle.archive_session(db, gs2.id))
    assert len(db.get(Character, chars[0].id).experiences) == 2


# ── 兜底与容错 ──────────────────────────────────────────────────────────


def test_无滚动摘要时用事件正文兜底(db, monkeypatch):
    """短局跑完就结束，可能一次摘要都没触发过——那也得能写出小传。"""
    from app.models.event_log import EventLog
    gs, module, chars = _seed(db, with_summary=False)
    for i in range(1, 6):
        db.add(EventLog(session_id=gs.id, sequence_num=i, event_type="narration",
                        content=f"第{i}段：雨夜里的渡口，木桩泡得发黑。", actor_name="KP"))
    db.commit()
    _patch_party(monkeypatch, chars)
    llm = _LLM()
    _patch_llm(monkeypatch, llm)

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 1
    assert "渡口" in str(llm.seen[0])


def test_完全没素材时不归档(db, monkeypatch):
    """无米下炊时宁可不写，也不让模型凭空编一段经历。"""
    gs, module, chars = _seed(db, with_summary=False)
    _patch_party(monkeypatch, chars)
    llm = _LLM()
    _patch_llm(monkeypatch, llm)

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 0
    assert llm.calls == 0


def test_单个角色生成失败不拖累其他人(db, monkeypatch):
    gs, module, chars = _seed(db, n_players=3)
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM(RuntimeError("boom"), "乙的小传", "丙的小传"))

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 2
    assert not db.get(Character, chars[0].id).experiences
    assert db.get(Character, chars[1].id).experiences


def test_空产出不落库(db, monkeypatch):
    gs, module, chars = _seed(db)
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM("   "))

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 0
    assert not db.get(Character, chars[0].id).experiences


def test_归档整体失败不抛(db, monkeypatch):
    """已经结束的会话不该因为归档出错而受影响。"""
    gs, module, chars = _seed(db)
    def _boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(character_chronicle.session_service, "get_party_members", _boom)
    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 0


def test_ai_队友不归档(db, monkeypatch):
    """经历是给真人玩家的卡攒的；AI 队友的卡是会话资产。"""
    gs, module, chars = _seed(db)
    ai = Character(name="AI 队友", rule_system="coc", is_player=False, module_id=module.id)
    db.add(ai)
    db.commit()
    _patch_party(monkeypatch, [*chars, ai])
    _patch_llm(monkeypatch, _LLM())

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 1
    assert not db.get(Character, ai.id).experiences


# ── 参战副本回写原件 ────────────────────────────────────────────────────


def test_副本经历回写原件(db, monkeypatch):
    """客人的卡在房主机器上只是副本；不回写的话他回自己库里看到的还是白纸卡。"""
    gs, module, chars = _seed(db)
    origin = Character(name="原件", rule_system="coc", is_player=True)
    db.add(origin)
    db.flush()
    chars[0].origin_character_id = origin.id
    db.commit()
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM("他在渡口截下了那封信。"))

    asyncio.run(character_chronicle.archive_session(db, gs.id))
    assert db.get(Character, origin.id).experiences[0]["story"] == "他在渡口截下了那封信。"


def test_原件不在本库时静默跳过(db, monkeypatch):
    """origin_character_id 是跨库标识，查不到是正常情况，不是错误。"""
    gs, module, chars = _seed(db)
    chars[0].origin_character_id = "不存在的-id"
    db.commit()
    _patch_party(monkeypatch, chars)
    _patch_llm(monkeypatch, _LLM())

    assert asyncio.run(character_chronicle.archive_session(db, gs.id)) == 1


def test_追加是整体赋值_能被_ORM_感知(db):
    """JSON 列就地 append 不会被视为脏——提交后看着成功、实际什么都没写。"""
    _gs, _m, chars = _seed(db)
    character_chronicle.append_experience(db, chars[0], {"session_id": "s1", "story": "x"})
    character_chronicle.append_experience(db, chars[0], {"session_id": "s2", "story": "y"})
    db.expire_all()
    assert len(db.get(Character, chars[0].id).experiences) == 2


# ── 头像 ────────────────────────────────────────────────────────────────


class _ImgLLM:
    def __init__(self, supported=True, b64="ZmFrZQ=="):
        self.supported, self.b64 = supported, b64

    def supports_image_gen(self):
        return self.supported

    async def generate_image(self, prompt):
        self.prompt = prompt
        return self.b64


def test_头像生成写回_url(db, monkeypatch):
    from app.services import character_avatar
    _gs, _m, chars = _seed(db)
    monkeypatch.setattr(character_avatar, "get_fast_llm", lambda: _LLM("a portrait of a man"))
    monkeypatch.setattr(character_avatar, "get_image_llm", lambda: _ImgLLM())
    monkeypatch.setattr(character_avatar.image_store, "save_image_b64", lambda b: "/api/images/x.jpg")

    url = asyncio.run(character_avatar.generate_avatar(db, chars[0]))
    assert url == "/api/images/x.jpg"
    assert db.get(Character, chars[0].id).avatar_url == "/api/images/x.jpg"


def test_未配生图模型时返回_None(db, monkeypatch):
    """没配生图的人不该被报错糊脸——上传那条路照样能给头像。"""
    from app.services import character_avatar
    _gs, _m, chars = _seed(db)
    monkeypatch.setattr(character_avatar, "get_image_llm", lambda: _ImgLLM(supported=False))
    assert asyncio.run(character_avatar.generate_avatar(db, chars[0])) is None


def test_生成失败不写坏数据(db, monkeypatch):
    from app.services import character_avatar
    _gs, _m, chars = _seed(db)
    monkeypatch.setattr(character_avatar, "get_fast_llm", lambda: _LLM(RuntimeError("boom")))
    monkeypatch.setattr(character_avatar, "get_image_llm", lambda: _ImgLLM())
    assert asyncio.run(character_avatar.generate_avatar(db, chars[0])) is None
    assert db.get(Character, chars[0].id).avatar_url is None


def test_提示词带上外貌与职业(db, monkeypatch):
    """头像要像这个人，靠的就是这些素材。"""
    from app.services import character_avatar
    _gs, _m, chars = _seed(db)
    chars[0].system_data = {"occupation": "报馆校对", "personalDescription": "瘦高，左眉有疤"}
    db.commit()
    llm = _LLM("prompt")
    monkeypatch.setattr(character_avatar, "get_fast_llm", lambda: llm)
    monkeypatch.setattr(character_avatar, "get_image_llm", lambda: _ImgLLM())
    monkeypatch.setattr(character_avatar.image_store, "save_image_b64", lambda b: "/api/images/x.jpg")

    asyncio.run(character_avatar.generate_avatar(db, chars[0]))
    prompt = str(llm.seen[0])
    assert "报馆校对" in prompt and "左眉有疤" in prompt


def test_摘掉头像回到首字纹章(db):
    from app.services import character_avatar
    _gs, _m, chars = _seed(db)
    character_avatar.set_avatar(db, chars[0], "/api/images/x.jpg")
    character_avatar.set_avatar(db, chars[0], None)
    assert db.get(Character, chars[0].id).avatar_url is None
