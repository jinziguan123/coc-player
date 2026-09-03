"""旁白事件的 metadata["model"]：记下生成它的模型名，供按模型分组统计文风。

反 tic 反馈环（context.py）与密度阈值（turn_validator.py）都靠本地库里的历史旁白来定，
此前 metadata 只有 scene_id / group / kp_manual，分不清「否定对比少了」是纠偏的功劳还是换了模型。
"""

from types import SimpleNamespace

from app.ai.provider import model_meta, model_name
from app.services import combat_service, session_service, turn_orchestrator
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from tests.test_chat_service import _seed_session


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _Provider:
    def __init__(self, model):
        self.model = model


class _Agent:
    """包着 Provider 的 agent（BaseAgent.llm 的形状）。"""
    def __init__(self, model):
        self.llm = _Provider(model)


def _narrations(db, session_id):
    return [e for e in session_service.get_session_events(db, session_id) if e.event_type == "narration"]


def test_model_name_接受_provider_或包着它的_agent():
    assert model_name(_Provider("gpt-x")) == "gpt-x"
    assert model_name(_Agent("claude-y")) == "claude-y"
    assert model_name(_Provider("  qwen  ")) == "qwen"


def test_model_name_拿不到就是_None_不写键():
    """测试里的假 llm、None、空模型名都走这条路：metadata 不写 model 键，旧读取方照常。"""
    assert model_name(None) is None
    assert model_name(SimpleNamespace()) is None
    assert model_name(_Provider("")) is None
    assert model_name(_Provider(None)) is None
    assert model_meta(None) == {}
    assert model_meta(_Provider("m")) == {"model": "m"}


def test_persist_narration_把模型名打进每条旁白(db_factory):
    db = db_factory()
    sid = _seed_session(db)
    # 两段旁白中间夹一句对话：切开落库的每一段都要带戳
    result = ["前半段旁白。\n\n后半段旁白。", "", [("守夜人", "谁在那儿？")], [(7, "守夜人", "谁在那儿？")], []]
    turn_orchestrator._persist_narration(db, sid, result, llm=_Agent("kp-model"))
    narrs = _narrations(db_factory(), sid)
    assert len(narrs) >= 2
    assert all((e.metadata_ or {}).get("model") == "kp-model" for e in narrs)


def test_没有模型名时不写键_既有键不受影响(db_factory):
    db = db_factory()
    sid = _seed_session(db)
    result = ["[GROUP: scene=档案馆]亨利翻查旧卷宗。", "", [], [], []]
    turn_orchestrator._persist_narration(db, sid, result)          # 旧调用形态：不传 llm
    narrs = _narrations(db_factory(), sid)
    assert narrs and all("model" not in (e.metadata_ or {}) for e in narrs)


def test_战斗旁白也带模型戳(db_factory):
    db = db_factory()
    sid = _seed_session(db)
    combat_service._combat_narration(db, sid, "拳风掠过。", _Agent("combat-model"))
    combat_service._combat_narration(db, sid, "血光四溅。")        # 没有 agent：不写键
    narrs = _narrations(db_factory(), sid)
    tags = [(e.metadata_ or {}).get("model") for e in narrs]
    assert tags == ["combat-model", None]


def test_真人_KP_手写的旁白只有_kp_manual_没有_model(db_factory):
    """真人 KP 的旁白不是模型生成的，靠既有的 kp_manual 标记区分，不塞一个空 model 键。"""
    db = db_factory()
    sid = _seed_session(db)
    ev = session_service.add_event(db, sid, "narration", "灯灭了。", actor_name="KP",
                                   metadata={"kp_manual": True})
    meta = ev.metadata_ or {}
    assert meta.get("kp_manual") is True and "model" not in meta
