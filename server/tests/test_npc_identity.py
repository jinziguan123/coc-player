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


def _seed_haunting(db):
    """《鬼屋》的真实数据形状：委托人、同姓的一家三口、以宅子命名的巫师。"""
    module, hero, session = _seed(db)
    module.npcs = [
        {"id": "n_knott", "name": "史蒂芬·诺特", "aliases": ["诺特先生", "诺特"],
         "unknown_as": "陌生男性"},
        {"id": "n_corbitt", "name": "沃尔特·科比特", "aliases": [], "unknown_as": "不明存在"},
        {"id": "n_teresa", "name": "特蕾莎·马卡里奥", "aliases": ["马卡里奥"],
         "unknown_as": "不明存在"},
        {"id": "n_vittorio", "name": "维托里奥·马卡里奥", "aliases": ["马卡里奥"],
         "unknown_as": "陌生男性"},
    ]
    module.world_setting = {
        "player_brief": "1920年代，玩家受房东诺特先生委托，调查波士顿市中心科比特老房子。",
    }
    db.commit()
    return module, hero, session


def test_叙事里叫的是别名_也算认得这个人(db_factory):
    """截图里的那一幕：叙事写了 10 次「诺特先生」，气泡仍是「陌生男性」——
    档案存的是全名「史蒂芬·诺特」，而场上没人这么叫他。"""
    db = db_factory()
    module, _hero, session = _seed_haunting(db)
    _narrate(db, session.id, "诺特先生本已走到门边，听莫妮卡这么一问，脚步顿住了。")
    assert ni.build_masker(db, session.id, module)("史蒂芬·诺特") == "史蒂芬·诺特"


def test_委托人开场即真名_房子的名字不解锁住在里面的东西(db_factory):
    """同一句 player_brief 里「诺特先生」与「科比特老房子」并存：
    前者是人，后者是建筑——只有前者算认得人。"""
    db = db_factory()
    module, _hero, session = _seed_haunting(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask("史蒂芬·诺特") == "史蒂芬·诺特"        # 委托人，开场就认识
    assert mask("沃尔特·科比特") == "不明存在"          # 房子叫科比特老宅，人还没露面


def test_同姓一家人共用的别名谁也解锁不了(db_factory):
    """「前租户马卡里奥一家搬走后」——这句话此刻指不到具体某个人。"""
    db = db_factory()
    module, _hero, session = _seed_haunting(db)
    _narrate(db, session.id, "前租户马卡里奥一家搬走后，房子就空到现在。")
    mask = ni.build_masker(db, session.id, module)
    assert mask("特蕾莎·马卡里奥") == "不明存在"
    assert mask("维托里奥·马卡里奥") == "陌生男性"
    # 但点名道姓写全名时照常认得
    _narrate(db, session.id, "疗养院的登记簿上写着维托里奥·马卡里奥。")
    assert ni.build_masker(db, session.id, module)("维托里奥·马卡里奥") == "维托里奥·马卡里奥"


def test_只有一个人列了共用的姓_照样不算数(db_factory):
    """回归：导入期的裁决不保证在一家人身上前后一致——实测「马卡里奥」在两位身上被否决、
    第三位漏了。只查「多人都列了」的话，漏网那个反而成了独占别名，
    一句「马卡里奥一家」就把活尸小女孩的身份解锁了。"""
    db = db_factory()
    module, _hero, session = _seed_haunting(db)
    for npc in module.npcs:
        if npc["id"] != "n_teresa":
            npc["aliases"] = [a for a in npc["aliases"] if a != "马卡里奥"]
    module.npcs = list(module.npcs)      # JSON 列整体重赋值才会脏
    db.commit()
    _narrate(db, session.id, "前租户马卡里奥一家搬走后，房子就空到现在。")
    assert ni.build_masker(db, session.id, module)("特蕾莎·马卡里奥") == "不明存在"


def test_别名命中也显示档案全名(db_factory):
    """免得同一个人一会儿「诺特先生」一会儿「史蒂芬·诺特」地跳。"""
    db = db_factory()
    module, _hero, session = _seed_haunting(db)
    _narrate(db, session.id, "诺特点点头。")
    assert ni.build_masker(db, session.id, module)("史蒂芬·诺特") == "史蒂芬·诺特"


def test_单字别名不收(db_factory):
    """「金·戴伯伦」的「金」在中文里随便一句话都撞得上，收了等于不遮。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    module.npcs = [{"id": "n_kim", "name": "金·戴伯伦", "aliases": ["金", "戴伯伦"],
                    "unknown_as": "陌生女性"}]
    db.commit()
    _narrate(db, session.id, "窗外金色的夕照落在长桌上。")
    assert ni.build_masker(db, session.id, module)("金·戴伯伦") == "陌生女性"


def test_call_names_收口():
    npc = {"name": "田间潜随者（莎布·尼古拉丝化身）", "aliases": ["潜随者", "它", "", "潜随者"]}
    # 括号里的神话身份不进称呼表（那是「知道它是什么」，不是「怎么称呼它」）；
    # 单字与重复项丢弃
    assert ni.call_names(npc) == ["田间潜随者", "潜随者"]
    assert ni.call_names({"name": "香澄澪"}) == ["香澄澪"]


def test_模组说玩家本就认识的人_开场就报真名(db_factory):
    """《鬼屋》的调查员是诺特请来的，开场白里没理由管他叫「陌生男性」。

    player_brief 按定义只写玩家角色本就清楚的前情（受谁委托、为何而来），
    委托人必然在里面——不认它的话，开场第一句叙事之前所有 NPC 一律陌生人。
    """
    db = db_factory()
    module, _hero, session = _seed(db)
    module.npcs = module.npcs + [
        {"id": "npc_knott", "name": "史蒂芬·诺特先生", "description": "一位焦虑的中年男子。"},
    ]
    module.world_setting = {
        "player_brief": "你们受房主史蒂芬·诺特先生委托，去查看那栋出过事的房子。",
    }
    db.commit()

    mask = ni.build_masker(db, session.id, module)
    assert mask("史蒂芬·诺特先生") == "史蒂芬·诺特先生"
    # 同一模组里 player_brief 没提的，照常遮着——这不是「有 brief 就全放行」
    assert mask(MONSTER) == ni.UNKNOWN_LABEL
    assert mask(STUDENT) == "陌生人"


def test_世界观导入不算认识(db_factory):
    """intro 是氛围铺陈，里面提到的名字不代表调查员认识本人。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    module.world_setting = {"intro": f"山民世代传说着{MONSTER}的故事。"}
    db.commit()
    assert ni.build_masker(db, session.id, module)(MONSTER) == ni.UNKNOWN_LABEL


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


def test_遭遇配图卡的名字也要遮住(db_factory, monkeypatch):
    """截图里的那一幕：战斗面板显示「不明存在」，遭遇卡却大字印着神话真名。

    这张卡是给玩家看的，此前直接印模组名——而模组名普遍是「外号（神话身份）」，
    KP 在旁边好好写着「一团比夜色更浓的黑」，卡上一行字就把旧日支配者的名号抖了出来。
    """
    from app.services import illustration_service

    monkeypatch.setattr(illustration_service, "_spawn_illustration",
                        lambda *a, **k: False)      # 不起生图任务
    db = db_factory()
    module, hero, session = _seed(db)
    _narrate(db, session.id, "门缝里探出一截暗色的东西，看不见头，也看不见眼。")

    enemies = [n for n in module.npcs if n["id"] == "npc_field"]
    chunks = illustration_service._maybe_encounter_illustration(
        db, session.id, module, enemies,
    )
    assert chunks
    card = [e for e in session_service.get_session_events(db, session.id)
            if (e.metadata_ or {}).get("icat") == "encounter"][-1]
    assert MONSTER not in card.content and "莎布" not in card.content
    assert ni.UNKNOWN_LABEL in card.content


def test_叙事里点过名的怪物_遭遇卡照常报真名(db_factory, monkeypatch):
    """遮的是「玩家还不知道」，不是无差别打码——KP 已经在叙事里直呼其名就该照显。"""
    from app.services import illustration_service

    monkeypatch.setattr(illustration_service, "_spawn_illustration", lambda *a, **k: False)
    db = db_factory()
    module, hero, session = _seed(db)
    _narrate(db, session.id, f"香澄压低声音：那是{MONSTER}，山里的老人管它叫田间潜随者。")

    enemies = [n for n in module.npcs if n["id"] == "npc_field"]
    illustration_service._maybe_encounter_illustration(db, session.id, module, enemies)
    card = [e for e in session_service.get_session_events(db, session.id)
            if (e.metadata_ or {}).get("icat") == "encounter"][-1]
    assert MONSTER in card.content
