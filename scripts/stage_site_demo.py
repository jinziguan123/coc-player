"""造一个只含原创内容的干净库，并在里面摆好介绍页截图要用的四局演示对局。

    server/.venv/bin/python scripts/stage_site_demo.py              # 默认写到 tmp/site-demo/
    server/.venv/bin/python scripts/stage_site_demo.py --out <目录>

为什么要有它：介绍页（site/）的截图会上公网，而日常开发库 server/trpg.db 里有商业模组与
规则书原文——截进去就等于对外分发受版权保护的内容。这里每次从零建库，只播种项目原创的
《雾港失灯事件》（app/content/onboarding.py）与预设调查员许闻舟，再把对局一条条写进去。

四局分别对应介绍页的几张图：

- ``game``      AI 当 KP 的普通对局（港务所 → 雾中栈桥），聊天流 / 对话框两种显示方式都截它；
- ``combat``    AI 当 KP、灯室里对深潜者幼体的战斗轮；
- ``kp-desk``   真人 KP 局的 KP 席，普通场景（KP 值班台）；
- ``kp-combat`` 另一局真人 KP 局，已切进战斗轮（KP 观局台）。

叙事、台词、玩家行动都是为这份原创模组手写的，不调用任何 AI 模型。能走真实服务层的
地方都走服务层（建局、认领席位、真人 KP 动作、战斗引擎），掷骰事件按引擎同一套判据生成，
前端看到的就是正常对局落下的数据。战斗的随机性用固定种子钉住，每次跑出来的局面一样。

跑完在 stdout 打一段 JSON（各局会话 id 与各席 token），``scripts/shoot-site-shots.mjs``
从 ``<目录>/demo.json`` 读它。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"

# 配图都是为这份原创模组 AI 生成的，可以公开。开发库的 images/ 里还混着别的模组的图，
# 所以只按文件名点名拷这三张，不整目录复制。
IMAGES = {
    "harbor_office": "0284ac811a3a45b3a643bedd359321ef.jpg",   # 港务所
    "fog_pier": "9140423012bb4c0eb808c31b6ca8e333.jpg",        # 雾中栈桥
    "harbor_master_lin": "9b27bd7045e6480ab46ef44da33174c2.jpg",  # 林恩港务员立绘
}

# 固定 token：截图脚本按它们入座。只是本机演示库里的随机串，不是任何真实身份。
PLAYER_TOKEN = "site-demo-player-7f3a9c"     # 介绍页「你」：AI KP 两局的玩家
FRIEND_TOKEN = "site-demo-friend-2b81d4"     # 真人 KP 两局里坐玩家席的朋友
KP_TOKEN = "site-demo-keeper-c05e17"         # 真人 KP 两局的 KP 席
ARCHIVE_TOKEN = "site-demo-archive-90d1e2"   # 战斗那局的卡挂在这里，见 _stage_ai_combat

# 战斗随机性的种子：挑的是「玩家先手、撬棍砸中、对方扑过来等玩家选反击还是闪避」这种好看又不一边倒的局面
SEEDS = {"ai_combat": int(os.environ.get("SEED_AI", 8)), "kp_combat": int(os.environ.get("SEED_KP", 4471))}


def _image_source_dirs() -> list[Path]:
    """配图从哪儿拷：worktree 里通常没有 server/images，回落到主仓库那份。"""
    dirs = [SERVER / "images"]
    common = Path(os.popen(f"git -C {ROOT} rev-parse --path-format=absolute --git-common-dir").read().strip())
    if common.name == ".git":
        dirs.append(common.parent / "server" / "images")
    return dirs


def _copy_images(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in IMAGES.values():
        for d in _image_source_dirs():
            if (d / name).is_file():
                shutil.copy2(d / name, dest / name)
                break
        else:
            raise SystemExit(f"找不到配图 {name}（在 server/images/ 下）。这三张是原创模组的配图，截图离不开它们。")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "tmp" / "site-demo"), help="干净库目录（会被清空重建）")
    args = ap.parse_args()
    out = Path(args.out).resolve()

    # 只认这个脚本自己建的目录，防止手滑把别的目录清掉
    if out.exists():
        if not (out / ".site-demo").exists() and any(out.iterdir()):
            raise SystemExit(f"{out} 不是空目录，也不像本脚本建的演示库，拒绝清空。")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / ".site-demo").write_text("scripts/stage_site_demo.py 建的演示库，可随时整目录删掉重建。\n", encoding="utf-8")
    (out / "logs").mkdir()
    (out / "plugins").mkdir()

    # 必须在 import app 之前设好：Settings 在导入时就读环境变量定库路径。
    # ai_settings.json 跟随 DB_PATH 落在同一目录，于是新库没有任何模型配置、不会带出密钥。
    os.environ["DB_PATH"] = str(out / "trpg.db")
    os.environ["LOG_DIR"] = str(out / "logs")
    os.environ["PLUGINS_DIR"] = str(out / "plugins")
    sys.path.insert(0, str(SERVER))
    os.chdir(SERVER)   # alembic.ini 按相对路径找

    _copy_images(out / "images")
    result = asyncio.run(_stage())
    result["db_path"] = str(out / "trpg.db")
    (out / "demo.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ───────────────────────────── 造数据 ─────────────────────────────


async def _stage() -> dict:
    from app.config import settings
    from app.database import SessionLocal, run_migrations

    if Path(settings.db_path).resolve() != Path(os.environ["DB_PATH"]).resolve():
        raise SystemExit(f"库路径没有指到演示库：{settings.db_path}")
    run_migrations()
    db = SessionLocal()
    try:
        module = _seed_module(db)
        ids = {
            "kp_desk": await _stage_human_kp_desk(db, module),
            "kp_combat": await _stage_human_kp_combat(db, module),
            "combat": await _stage_ai_combat(db, module),
            # 首页「继续上次的桌」挑最近动过的那局：_touch 把这局排在最前
            "game": _stage_ai_game(db, module),
        }
        return {
            "module_id": module.id,
            "tokens": {"player": PLAYER_TOKEN, "friend": FRIEND_TOKEN, "kp": KP_TOKEN},
            "sessions": ids,
            "character_id": _session(db, ids["game"]).player_character_id,
        }
    finally:
        db.close()


def _seed_module(db):
    from app.services.onboarding_service import _ensure_module

    module = _ensure_module(db)
    scenes = [dict(s) for s in module.scenes]
    for s in scenes:
        if s["id"] in IMAGES:
            s["image"] = _img(s["id"])
    npcs = [dict(n) for n in module.npcs]
    for n in npcs:
        if n["id"] in IMAGES:
            n["portrait"] = _img(n["id"])
    module.scenes = scenes
    module.npcs = npcs
    db.commit()
    return module


def _img(key: str) -> str:
    return f"/api/images/{IMAGES[key]}"


def _session(db, sid):
    from app.models.session import GameSession
    return db.get(GameSession, sid)


def _new_investigator(db, module, token: str, *, hp: int | None = None, san: int | None = None,
                      weapon: str | None = None):
    """每局一张许闻舟：一张角色卡同时只能坐一桌（session_service.active_character_ids）。"""
    from copy import deepcopy

    from app.content.onboarding import SAMPLE_CHARACTER
    from app.models.character import Character

    data = deepcopy(SAMPLE_CHARACTER)
    sd = data["system_data"]
    if hp is not None:
        sd["hitPoints"]["current"] = hp
    if san is not None:
        sd["sanity"]["current"] = san
    if weapon:
        # 战斗里手上拿的那件（combat_service._char_participant 读 combat.weapon，缺省徒手）
        sd["combat"] = {"weapon": weapon}
    ch = Character(module_id=module.id, owner_token=token, **data)
    db.add(ch)
    db.commit()
    return ch


class Log:
    """往一局里按顺序写事件的小帮手。格式与真实对局落库的一致（见 event_store.add_event）。"""

    def __init__(self, db, sid: str, module):
        self.db, self.sid, self.module = db, sid, module
        from app.services import rule_options_service
        self.rs = rule_options_service.ruleset_by_id(db, sid)

    def add(self, event_type, content, *, actor_id=None, actor_name="", metadata=None, visibility=None):
        from app.services import session_service
        return session_service.add_event(
            self.db, self.sid, event_type, content,
            actor_id=actor_id, actor_name=actor_name,
            metadata=dict(metadata or {}), visibility=visibility,
        )

    def intro(self):
        m = self.module
        ws = m.world_setting or {}
        meta = " · ".join(x for x in [ws.get("tone"), ws.get("era"), ws.get("region"),
                                       f"难度 {ws.get('difficulty')}", f"建议 {ws.get('player_count')} 人"] if x)
        self.add("system", m.description, actor_name="系统",
                 metadata={"kind": "module_intro", "title": m.title, "meta": meta})

    def arrive(self, scene_id: str):
        """进场：切当前场景、记足迹，有配图就落一张场景配图卡（聊天流里清晰出现一次，此后成氛围底）。"""
        from app.models.session_navigation import SessionNavigation

        sess = _session(self.db, self.sid)
        sess.current_scene_id = scene_id
        nav = self.db.get(SessionNavigation, self.sid)
        if nav is None:
            nav = SessionNavigation(session_id=self.sid, visited_scenes=[])
            self.db.add(nav)
        if scene_id not in (nav.visited_scenes or []):
            nav.visited_scenes = [*(nav.visited_scenes or []), scene_id]
        ws = dict(sess.world_state or {})
        cards = list(ws.get("scene_cards") or [])
        if f"{scene_id}|base" not in cards:
            cards.append(f"{scene_id}|base")
        ws["scene_cards"] = cards
        sess.world_state = ws
        self.db.commit()
        scene = next(s for s in self.module.scenes if s["id"] == scene_id)
        if scene.get("image"):
            self.add("system", f"—— 抵达 {scene['name']} ——", actor_name="系统", metadata={
                "kind": "illustration", "icat": "scene", "title": scene["name"],
                "image_kind": "scene", "image_item_id": scene_id, "image_field": "image",
                "visual_state_key": "base", "scene_id": scene_id, "image": scene["image"],
            })

    def kp(self, text: str):
        self.add("narration", text.strip(), actor_name="KP")

    def npc(self, npc_id: str, text: str):
        n = next(x for x in self.module.npcs if x["id"] == npc_id)
        meta = {}
        if n.get("portrait"):
            meta = {"image_kind": "npc", "image_item_id": npc_id, "image_field": "portrait",
                    "portrait": n["portrait"]}
        self.add("dialogue", text, actor_id=npc_id, actor_name=n["name"], metadata=meta)

    def act(self, ch, text: str):
        self.add("action", text, actor_id=ch.id, actor_name=ch.name)

    def say(self, ch, text: str):
        self.add("dialogue", text, actor_id=ch.id, actor_name=ch.name)

    def ask_check(self, ch, skill: str, difficulty: str = "regular"):
        import uuid
        self.add("system", f"请 {ch.name} 进行一次「{skill}」检定", actor_name="系统", metadata={
            "check_request": True, "id": uuid.uuid4().hex, "skill": skill, "difficulty": difficulty,
            "char_ref": "", "char_id": ch.id, "actor_name": ch.name, "source": "",
            "bonus": 0, "penalty": 0,
        })

    def roll(self, ch, skill: str, d100: int, difficulty: str = "regular"):
        """按引擎同一套判据（rs.check.judge / achieved_tier）把一次已掷出的 d100 落成掷骰事件。"""
        from app.services.dice_runtime import TIER_LABEL

        value = (ch.skills or {}).get(skill) or (ch.base_attributes or {}).get(skill) or 0
        target = {"hard": value // 2, "extreme": value // 5}.get(difficulty, value)
        outcome, desc = self.rs.check.judge(d100, value, difficulty, target)
        tier = self.rs.check.achieved_tier(d100, value)
        tens, units = (d100 // 10) * 10 % 100, d100 % 10
        self.add("dice", f"{ch.name}｜{skill} 检定（{difficulty}）：{TIER_LABEL.get(tier, tier)}（{desc}）",
                 actor_name="系统", metadata={
                     "skill": skill, "skill_value": value, "roll": d100, "target": target,
                     "outcome": outcome, "tier": tier, "actor": ch.name,
                     "dice": {"kind": "check", "result": d100, "tens": [tens], "tens_kept": tens,
                              "units": units, "bonus": 0, "penalty": 0},
                 })


def _open_ai_session(db, module, ch):
    """AI KP 单人局：与新手引导同一条路（建局后直接 active，跳过只有自己的大厅）。"""
    from app.services import session_service

    game = session_service.create_session(
        db, module.id, [{"character_id": ch.id, "role": "human", "is_primary": True}],
        creator_token=PLAYER_TOKEN, commit=False,
    )
    game.status = "active"
    db.commit()
    db.refresh(game)
    return game


def _open_human_kp_session(db, module, ch):
    """真人 KP 局：KP 建房只占 KP 席，朋友认领玩家席，KP 在大厅开局——与真实流程一致。"""
    from app.services import session_service

    game = session_service.create_session(
        db, module.id, [{"character_id": None, "role": "human", "is_primary": True}],
        creator_token=KP_TOKEN, kp_mode="human",
    )
    session_service.claim_seat(db, game.id, 0, ch.id, FRIEND_TOKEN)
    session_service.set_ready(db, game.id, FRIEND_TOKEN, True)
    session_service.start_game(db, game.id, KP_TOKEN)
    db.refresh(game)
    return game


def _touch(db, sid: str, minutes_ago: int) -> None:
    """错开各局「最后活跃」时间。首页按最后一条事件的时间挑「继续上次的桌」，而这里几局是在
    同一秒里写完的、时间戳打平，挑中谁全凭运气——把整局的事件时间一起往前挪，让 AI KP 那局排第一。"""
    from sqlalchemy import text

    sess = _session(db, sid)
    sess.updated_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.execute(
        text("UPDATE event_logs SET created_at = datetime(created_at, :shift) WHERE session_id = :sid"),
        {"shift": f"-{minutes_ago} minutes", "sid": sid},
    )
    db.commit()


# ── 第一局：AI KP，港务所 → 雾中栈桥 ──


def _stage_ai_game(db, module) -> str:
    xu = _new_investigator(db, module, PLAYER_TOKEN)
    game = _open_ai_session(db, module, xu)
    log = Log(db, game.id, module)

    log.intro()
    log.arrive("harbor_office")
    log.kp("""
雾是从海上一寸一寸爬进来的。你推开港务所的门时，门轴发出一声潮湿的呻吟，屋里那盏煤油灯跟着晃了晃，把墙上一排航海日志的书脊照得忽明忽暗。

窗外什么都看不见。往常这个钟点，灯塔的光每隔九秒扫过一次窗棂；今夜，玻璃上只贴着一层灰白，像有人从外面糊了一张湿纸。远处传来一声闷钟，沉在雾底，分不清是从哪个方向来的。
""")
    log.npc("harbor_master_lin",
            "许记者，劳你连夜跑一趟。灯塔……灭了。天亮那班邮船要进港，没有灯，它就只能在雾里摸着礁石走。")
    log.kp("""
林恩说完这句，又低头去看怀表。他今晚大概已经看过上百次了——表盖边缘被拇指磨得发亮。

桌上摊着今夜的值班簿。你注意到最上面那一页的边缘参差不齐，纸茬还是白的。
""")
    log.act(xu, "我把值班簿转过来对着灯，先看那页被撕掉的残边，再用铅笔侧锋轻轻涂下一页，看有没有压痕。")
    log.ask_check(xu, "侦查")
    log.roll(xu, "侦查", 23)
    log.kp("""
铅灰在纸上铺开，一行字从底下浮了出来——写字的人下笔很重，力透了一页纸：

**「退潮后开门。」**

字迹歪斜，最后一笔拖得很长，像是写到一半手就抖了。你认得这种笔迹：上周你采访灯塔看守老崔，他在你的本子上签过名。
""")
    log.say(xu, "林恩先生，这页是老崔撕的？昨天夜里，你到底听见了什么？")
    log.npc("harbor_master_lin", "……风声罢了。雾港哪天夜里不刮风。")
    log.kp("""
他答得太快。说完他自己也察觉了，手指在怀表盖上停住，没有再按下去。屋里只剩下灯芯偶尔爆出的一粒火星。

你没有再追问。灯塔在栈桥尽头，答案多半也在那边。
""")
    log.act(xu, "我把那页拓印折好塞进笔记本，提上手电，叫林恩带路去栈桥。")
    log.arrive("fog_pier")
    log.kp("""
栈桥浸在齐膝深的雾里，手电的光柱照出去不到三步就被吞掉了。木板被水汽泡得发软，每踩一步都往下陷一点，缆绳上结了一层白霜似的盐。

走到一半，你的鞋底踩到一块颜色不对的板子——它比两边的新，钉头还亮着。旁边还有两块，钉得歪歪扭扭，像是赶在天黑之前慌忙补上的。
""")
    log.act(xu, "我蹲下来，用手电贴着木板照那几颗新钉子，再探身去看桥板底下。")
    log.ask_check(xu, "侦查")
    log.roll(xu, "侦查", 41)
    log.kp("""
钉子是从上面钉进去的，可木板的断口朝上翻着——这几块板子原先是被什么东西**从水里往上顶断的**。断茬上挂着几缕滑腻的东西，你用笔尖挑起来，在手电下泛着暗绿的光，像鳞，又不完全像。

桥下的海水在退。退得太快了，礁石一块接一块地露出来，湿漉漉地反着光。

雾的深处，钟又响了一声。这一次你听清了：是从灯塔那边传来的。
""")
    log.npc("harbor_master_lin",
            "那口钟三年前就锈死了，许记者。昨夜它响了三下——我没敢写进簿子里。")
    _touch(db, game.id, 2)
    return game.id


# ── 第二局：AI KP，灯室里的战斗轮 ──


async def _stage_ai_combat(db, module) -> str:
    from app.services import combat_service

    xu = _new_investigator(db, module, PLAYER_TOKEN, hp=8, weapon="撬棍")
    game = _open_ai_session(db, module, xu)
    # 席位归玩家（入座、操作都按席位的 owner_token 认），但卡本身挂到一个只用于归档的 token 下：
    # 角色名录按卡的 owner_token 列，不这样的话玩家的名录里会并排出现两个许闻舟。
    xu.owner_token = ARCHIVE_TOKEN
    db.commit()
    log = Log(db, game.id, module)

    log.intro()
    log.arrive("harbor_office")
    log.kp("雾港的灯塔灭了。你在港务所的值班簿上拓出一行「退潮后开门」，又从林恩嘴里撬出那三声不该响的钟，随即提着手电上了栈桥。")
    log.arrive("fog_pier")
    log.kp("桥板是从水里往上顶断的。海水退得飞快，礁盘一块块露出来，钟声就在灯塔那头。")
    log.arrive("lighthouse_base")
    log.kp("""
铁门虚掩着。底层墙上用粗盐画满了同一枚螺旋，一圈套一圈，从地面一直爬到手电照不到的高处。你盯着看了太久——那些螺旋好像在缓缓转动。
""")
    # 理智检定走真实结算（扣卡上的 SAN、落同款掷骰事件）；种子钉住，这一掷是失败、1d3 掷出 2
    from app.services import turn_effects
    random.seed(5)
    turn_effects._settle_san_target(db, game.id, _session(db, game.id), xu, "0", "1d3", "盐画的螺旋")
    log.act(xu, "我咬住手电，攥紧撬棍，踩着螺旋铁梯往灯室上去。")
    log.arrive("lamp_room")
    log.kp("""
灯室里冷得像冰窖。巨大的菲涅尔透镜蒙着一层盐膜，灯座是冷的。地板正中刻着一枚半米宽的螺旋，凹槽里积着还没干的海水，一串湿脚印从螺旋中心走出来——它们没有从门口进来，也没有从窗边出去。

头顶传来金属摩擦的声音。你抬起手电的那一刻，透镜背后有什么东西松开了攀着铁架的蹼爪，朝你扑了下来。
""")

    # 战斗走真实引擎：建参战方、排先攻、方格布阵、跑到第一个真人回合为止。
    # 种子钉住，先攻与敌方第一击每次都一样。
    random.seed(SEEDS["ai_combat"])
    rs = log.rs
    spawn = next(n for n in module.npcs if n["id"] == "deep_one_spawn")
    lin = next(n for n in module.npcs if n["id"] == "harbor_master_lin")
    player_side = [
        combat_service._char_participant(xu, "player", True, rs=rs),
        # 林恩跟着上了灯塔：作为 NPC 队友入场，战斗轮里由引擎替他行动
        combat_service._npc_participant({**lin, "weapon": "提灯"}, "ally", rs=rs),
    ]
    # 不套「未识别」面具：这张图要让读者一眼看出在跟什么打（玩家此前已在桥板上见过它的鳞）
    enemies = [combat_service._npc_participant(spawn, "enemy", rs=rs)]
    state = combat_service.start_combat(
        db, game.id, player_side, enemies, trigger="深潜者幼体从透镜背后扑下",
        deployment={xu.id: {"x": 4, "y": 3}, "harbor_master_lin": {"x": 3, "y": 4}, "deep_one_spawn": {"x": 5, "y": 3}},
    )
    log.add("system", "战斗爆发：透镜背后的东西扑了下来！", actor_name="战斗")
    await combat_service.drive_npcs(db, game.id, state, None, "灯室")
    log.act(xu, "我抡起撬棍，照着那东西湿滑的脑袋砸下去。")
    await _exchange(db, game.id, xu)
    _touch(db, game.id, 9)
    return game.id


async def _exchange(db, sid: str, xu) -> None:
    """玩家用撬棍打一下（命中则亲自掷伤害），随后引擎替敌方 / NPC 队友跑完各自的回合，
    停在下一个需要真人拍板的地方。战报、掷骰卡、HP 变化都是引擎自己落的。"""
    from app.services import combat_service

    # 它是扑下来的，落点就在身边：默认布阵把两边摆在棋盘两头，先把它挪到贴身的格子
    state = combat_service.get_combat(_session(db, sid))
    me = next(p for p in state["initiative"] if p.get("char_id") == xu.id)
    foe = next(p for p in state["initiative"] if p["id"] == "deep_one_spawn")
    if max(abs(me["pos"]["x"] - foe["pos"]["x"]), abs(me["pos"]["y"] - foe["pos"]["y"])) > 1:
        foe["pos"] = {"x": me["pos"]["x"] + 1, "y": me["pos"]["y"]}
        combat_service._save_combat(db, sid, state)

    await combat_service.resolve_player_action(
        db, sid, xu.id, {"type": "attack", "target_id": "deep_one_spawn", "weapon": "撬棍"},
    )
    state = combat_service.get_combat(_session(db, sid))
    if state and (state.get("pending_roll") or {}).get("actor_id") == xu.id:
        await combat_service.resolve_combat_roll(db, sid, xu.id)


# ── 第三局：真人 KP，值班台（普通场景）──


async def _stage_human_kp_desk(db, module) -> str:
    from app.services.human_kp_actions import execute_human_kp_action

    xu = _new_investigator(db, module, FRIEND_TOKEN)
    game = _open_human_kp_session(db, module, xu)
    log = Log(db, game.id, module)

    async def kp(action: str, **payload):
        sess = _session(db, game.id)
        await execute_human_kp_action(db, game.id, sess, module, action, payload)

    log.intro()
    log.arrive("harbor_office")
    await kp("narration", content=(
        "港务所里只点着一盏煤油灯。窗外的雾贴着玻璃缓缓流动，往常每隔九秒扫过一次的灯塔光，"
        "今夜一次也没有来过。远处有一声闷钟，沉在雾底。"
    ))
    await kp("dialogue", npc_id="harbor_master_lin",
             content="许记者，你可算来了。灯塔灭了，天亮那班邮船还在海上。")
    log.act(xu, "我先不急着问，把湿透的帽子挂到门后，走到桌边看那本摊开的值班簿。")
    await kp("narration", content=(
        "值班簿最上面那一页边缘参差，纸茬还是白的——有一页刚被撕走不久。"
        "林恩看见你低头，下意识伸手想把簿子合上，手伸到一半又缩了回去。"
    ))
    log.say(xu, "林恩先生，这一页上原来记的是什么？")
    await kp("dialogue", npc_id="harbor_master_lin", content="……我进来的时候，那页就已经没了。")
    log.act(xu, "我用铅笔侧锋轻轻涂下一页，看看撕走那页的字有没有透下来。")
    log.ask_check(xu, "侦查")
    log.roll(xu, "侦查", 38)
    await kp("narration", content=(
        "铅灰底下浮出一行压痕，下笔很重：「退潮后开门」。最后一笔拖得很长，像是写到一半手就抖了。"
    ))
    await kp("narration", content=(
        "就在这时，窗外的雾里又传来一声钟响。很轻，很闷，像是隔着一整片海。"
        "林恩的手指在怀表盖上停住了。"
    ))
    log.say(xu, "……你也听见了，对吧？")
    # 这一张留着等玩家掷：值班台的待办里就有一条「等许闻舟投骰」
    await kp("dice_check", skill="聆听", char="许闻舟", reason="钟声是从哪个方向传来的")
    _touch(db, game.id, 30)
    return game.id


# ── 第四局：真人 KP，灯室开战 ──


async def _stage_human_kp_combat(db, module) -> str:
    from app.services.human_kp_actions import execute_human_kp_action

    xu = _new_investigator(db, module, FRIEND_TOKEN, hp=9, san=57, weapon="撬棍")
    game = _open_human_kp_session(db, module, xu)
    log = Log(db, game.id, module)

    async def kp(action: str, **payload):
        sess = _session(db, game.id)
        await execute_human_kp_action(db, game.id, sess, module, action, payload)

    log.intro()
    log.arrive("harbor_office")
    log.arrive("fog_pier")
    log.arrive("lighthouse_base")
    log.arrive("lamp_room")
    await kp("narration", content=(
        "螺旋铁梯的尽头就是灯室。透镜蒙着一层盐膜，灯座冰冷，地板正中那枚螺旋的凹槽里积着海水，"
        "一串湿脚印从螺旋中心走出来，哪儿也没去。"
    ))
    log.act(xu, "我划亮一根火柴，凑近灯座，想看看灯芯还能不能用。")
    await kp("narration", content=(
        "火柴的光刚亮起来，头顶的铁架就发出一声刺耳的刮擦。透镜背后有什么东西松开了蹼爪——"
        "它冲着那一点火光扑了下来。"
    ))
    random.seed(SEEDS["kp_combat"])
    await kp("start_combat", enemies="深潜者幼体", trigger="透镜背后的东西扑向火光")
    log.act(xu, "火柴掉在地上还没灭。我反手抄起撬棍，冲它的肩膀横扫过去。")
    await _exchange(db, game.id, xu)
    await kp("narration", content=(
        "撬棍砸在鳞片上，发出一声闷响，像打在泡涨的皮革上。那东西喉咙里咕噜了一声，"
        "蹼爪在地板的螺旋凹槽里一撑，又朝你压了过来。"
    ))
    _touch(db, game.id, 20)
    return game.id


if __name__ == "__main__":
    main()
