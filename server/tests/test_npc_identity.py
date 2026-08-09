"""NPC 对外称呼：玩家还没认出来的东西，机制界面不能替 KP 把名字说出来。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module
from app.services import npc_identity as ni
from app.services import session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ident.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


MONSTER = "田间潜随者（莎布·尼古拉丝化身）"
STUDENT = "香澄澪"


def _seed(db):
    module = Module(
        title="闇暗山",
        rule_system="coc",
        scenes=[{"id": "s1", "name": "土路"}],
        npcs=[
            {"id": "npc_student", "name": STUDENT, "description": "学生服青年"},
            {"id": "npc_field", "name": MONSTER, "description": "山顶枯树上现身的神"},
            {"id": "npc_yobuko", "name": "呼子（蠕虫行者）", "unknown_as": "林中的声音"},
        ],
        clues=[],
        world_setting={},
    )
    hero = Character(name="沃什·帕杉德", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    session = GameSession(
        module_id=module.id, player_character_id=hero.id,
        status="active", current_scene_id="s1", world_state={},
    )
    db.add(session)
    db.commit()
    return module, hero, session


def _narrate(db, sid, text, **kw):
    return session_service.add_event(db, sid, "narration", text, actor_name="KP", **kw)


def test_没在叙事里出现过的名字一律遮住(db_factory):
    """截图里的那一幕：叙事只写「一团比夜色更浓的黑」，
    对抗检定卡却印出「田间潜随者（莎布·尼古拉丝化身）」，玩家一眼就知道对面是谁。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(db, session.id, "树影边缘，一团比夜色更浓的黑正从土路中央隆起。没有脸，没有眼。")

    mask = ni.build_masker(db, session.id, module)
    assert mask(MONSTER) == ni.UNKNOWN_LABEL      # 看着不像人
    assert mask(STUDENT) == "陌生人"               # 看着是人，但描述没写性别


def test_模组可以给更贴切的中性称呼(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask("呼子（蠕虫行者）") == "林中的声音"


def test_叙事里写过外号就显示外号_但神话身份仍然遮着(db_factory):
    """KP 说破「田间潜随者」这个称呼时玩家就认识它了；
    但「莎布·尼古拉丝化身」是另一层——那是知道自己在跟哪尊旧日支配者打交道。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(db, session.id, "山民管这种东西叫田间潜随者。")

    mask = ni.build_masker(db, session.id, module)
    assert mask(MONSTER) == "田间潜随者"
    assert "莎布" not in mask(MONSTER)


def test_神话身份出现后给全名(db_factory):
    """知道了它是谁的化身，再遮外号没有意义。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(db, session.id, "书页上写着：那是莎布·尼古拉丝化身，千仔之母的一枝。")

    mask = ni.build_masker(db, session.id, module)
    assert mask(MONSTER) == MONSTER


def test_NPC自报家门当场改口(db_factory):
    """否则气泡会是「不明存在：我叫香澄澪」——同一条消息里自己打自己的脸。"""
    db = db_factory()
    module, _hero, session = _seed(db)

    assert ni.build_masker(db, session.id, module)(STUDENT) == "陌生人"
    mask = ni.build_masker(db, session.id, module)
    assert mask(STUDENT, extra_prose="我叫香澄澪，是这附近的学生。") == STUDENT


def test_对白正文也算认识途径(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    session_service.add_event(
        db, session.id, "dialogue", "我叫香澄澪。", actor_name="某人",
    )
    assert ni.build_masker(db, session.id, module)(STUDENT) == STUDENT


def test_仅KP可见的事件不算玩家知道(db_factory):
    """幕后推演里 KP 自己盘算过的东西，玩家没看见。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(
        db, session.id, "（幕后）田间潜随者已经盯上了调查员。",
        visibility=[session_service.KP_ONLY_SENTINEL],
    )
    assert ni.build_masker(db, session.id, module)(MONSTER) == ni.UNKNOWN_LABEL


def test_机制事件不算认识途径(db_factory):
    """否则会自我实现：检定卡印一次名字，就永远算「玩家已知」，遮罩当场失效。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    session_service.add_event(
        db, session.id, "dice", f"对抗骰　沃什·帕杉德 vs {MONSTER}", actor_name="系统",
    )
    assert ni.build_masker(db, session.id, module)(MONSTER) == ni.UNKNOWN_LABEL


def test_不是本模组NPC的名字原样通过(db_factory):
    """玩家角色、临场 NPC 不该被误伤。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask("沃什·帕杉德") == "沃什·帕杉德"
    assert mask("卖报的老头") == "卖报的老头"
    assert mask("") == ""
    assert mask(None) == ""


def test_没有模组时不遮任何东西(db_factory):
    db = db_factory()
    _module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, None)
    assert mask(MONSTER) == MONSTER


def test_reveals_报告是否照实显示(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask.reveals("沃什·帕杉德") is True
    assert mask.reveals(MONSTER) is False


# ── 玩家还不知道名字时怎么称呼 ───────────────────────────────────────────


def _label(**npc):
    return ni.unknown_label(npc)


def test_模组明写的字段优先于一切推断():
    assert _label(unknown_as="林中的声音", description="中年男子") == "林中的声音"
    assert _label(looks_human=True, gender="female", description="一团黑影") == "陌生女性"
    assert _label(looks_human=False, description="穿西装的中年男子") == ni.UNKNOWN_LABEL


@pytest.mark.parametrize("desc,expected", [
    ("一位焦虑的中年男子，房屋的新主人。", "陌生男性"),
    ("波士顿环球报社编辑，一位守旧的中年女性。", "陌生女性"),
    ("30岁男性电车乘务员，身穿制服。", "陌生男性"),
    ("六岁，张金贵的小女儿，活泼好动。", "陌生女性"),
    ("维托里奥的妻子，疗养院病人，意识清醒但虚弱。", "陌生女性"),
])
def test_描述里明写性别就用它(desc, expected):
    assert _label(description=desc) == expected


def test_亲属称谓排在单字性别词前面():
    """回归：「黄婆干儿子」里若先撞上「婆」，这位男警员就成了「陌生女性」。

    光看关键词在不在，语义是会反过来的——亲属称谓说的是这个 NPC 自己是谁。
    """
    assert _label(description="二十八岁，呼兰县人民法院法警，黄婆干儿子，身材中等。") == "陌生男性"
    assert _label(description="六十二岁，本名王婆，普通农妇打扮，手持念珠。") == "陌生女性"


def test_不像人的先否决_不再往下判性别():
    """回归：「体型庞大的男子…生有巨大金狼头」先判性别就成了「陌生男性」。

    把人说成「不明存在」只是别扭，把怪物说成「陌生男性」才是砸场子。
    """
    assert _label(
        description="体型庞大的男子，浑身腐烂亚麻布，伤口涌出脓液，生有巨大金狼头。",
    ) == ni.UNKNOWN_LABEL
    assert _label(description="没有眼睛的人形怪物，面部呈菌状增生。") == ni.UNKNOWN_LABEL
    assert _label(description="半人半鱼的佝偻身形，皮肤覆着湿冷的鳞，指间连着蹼。") == ni.UNKNOWN_LABEL
    assert _label(description="居住在山中的神话生物，模仿声音迷惑猎物。") == ni.UNKNOWN_LABEL


def test_看得出是人但没写性别就不猜():
    """猜错性别比说「陌生人」糟得多，而名字是猜不出性别的。"""
    assert _label(description="穿着学生服的青年，苍白的皮肤，黑曜石般深邃的眼睛。") == "陌生人"
    assert _label(name="香澄澪", description="五十二岁，呼兰县公安局局长，眉头紧锁。") == "陌生人"


def test_名字里的敬称算明写的性别():
    assert _label(name="佐利先生", description="在街区贩卖烟卷和报纸的小贩，矮小。") == "陌生男性"
    assert _label(name="黄婆", description="手持念珠的老人。") == "陌生女性"


def test_什么线索都没有时兜底():
    assert _label(name="影", description="") == ni.UNKNOWN_LABEL


def test_遮名时用的就是这套称呼(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    module.npcs = [{"id": "n1", "name": "史蒂芬·诺特先生", "description": "一位焦虑的中年男子。"}]
    db.commit()
    assert ni.build_masker(db, session.id, module)("史蒂芬·诺特先生") == "陌生男性"


@pytest.mark.parametrize("raw,expected", [
    ("田间潜随者（莎布·尼古拉丝化身）", ("田间潜随者", "莎布·尼古拉丝化身")),
    ("呼子(蠕虫行者)", ("呼子", "蠕虫行者")),
    ("香澄澪", ("香澄澪", "")),
    ("", ("", "")),
    # 括号不在结尾的不算 KP 侧注释，别把人家名字拆了
    ("（代号）K先生", ("（代号）K先生", "")),
])
def test_拆名(raw, expected):
    assert ni.split_name(raw) == expected
