"""新手团模组的内容自洽性检查。

新手团是**数据**，不是代码——写错一个场景 id、漏一个技能，跑起来之前谁也发现不了，
而它偏偏是新用户见到的第一样东西。这里把「跑之前就该成立」的约束固化下来：
连通性、引用完整性、以及各引擎真正会去读的字段。

（旧版就踩过两个这类坑：预设调查员一个战斗技能都没有，进了战斗轮只能挨打；
system_data 写的是 moveRate，而 chase/combat 两个服务读的都是 move。）
"""

import re

from app.content.onboarding import SAMPLE_CHARACTER, SAMPLE_MODULE

SCENES = SAMPLE_MODULE["scenes"]
SCENE_IDS = {scene["id"] for scene in SCENES}


def test_scene_connections_resolve_and_are_bidirectional():
    """场景连接必须指向存在的场景，且两头互相连通——否则玩家会走进死路。"""
    for scene in SCENES:
        for target in scene.get("connections", []):
            assert target in SCENE_IDS, f"{scene['id']} 连到不存在的场景 {target}"
            back = next(s for s in SCENES if s["id"] == target)
            assert scene["id"] in back.get("connections", []), (
                f"{scene['id']} → {target} 是单向的，玩家过去就回不来"
            )


def test_all_scenes_reachable_from_first():
    """六幕必须从第一幕可达，教学关不能有孤岛。"""
    seen = {SCENES[0]["id"]}
    frontier = [SCENES[0]["id"]]
    while frontier:
        current = frontier.pop()
        scene = next(s for s in SCENES if s["id"] == current)
        for target in scene.get("connections", []):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == SCENE_IDS, f"以下场景从开局不可达：{SCENE_IDS - seen}"


def test_clue_and_npc_locations_exist():
    for clue in SAMPLE_MODULE["clues"]:
        assert clue["location"] in SCENE_IDS, f"线索 {clue['id']} 落在不存在的场景"
    for npc in SAMPLE_MODULE["npcs"]:
        assert npc["initial_location"] in SCENE_IDS, f"NPC {npc['id']} 落在不存在的场景"


def test_combat_npcs_have_fields_combat_service_reads():
    """战斗幕的敌人必须备齐 combat_service 建参战方要用的字段。"""
    enemies = [npc for npc in SAMPLE_MODULE["npcs"] if npc.get("weapon")]
    assert enemies, "教学关需要至少一个可参战的敌人"
    for npc in enemies:
        assert npc.get("hp"), f"{npc['name']} 缺 hp"
        assert npc.get("attributes"), f"{npc['name']} 缺 attributes"
        for key in ("STR", "CON", "SIZ", "DEX"):
            assert key in npc["attributes"], f"{npc['name']} 的 attributes 缺 {key}"
        assert npc.get("skills"), f"{npc['name']} 缺 skills"


def test_sanity_event_spec_is_parseable():
    """理智检定的 san_loss 必须是 planned_effects 能解析的「成功/失败」两段式，
    否则模组写死的规格会被静默丢弃、退回 AI 猜测。"""
    san_events = [
        event
        for scene in SCENES
        for event in scene.get("events", [])
        if event.get("kind") == "san_check"
    ]
    assert san_events, "教学关必须包含一次理智检定"
    for event in san_events:
        parts = re.split(r"\s*/\s*", str(event.get("san_loss") or ""), maxsplit=1)
        assert len(parts) == 2 and all(p.strip() for p in parts), (
            f"san_loss={event.get('san_loss')!r} 不是「成功/失败」两段式"
        )


def test_pregen_character_covers_every_skill_the_scenes_ask_for():
    """场景 events 点名要掷的技能，预设调查员必须都有——
    否则新手第一次跑就连着失败，学到的只有挫败感。"""
    asked = {
        str(event.get("skill"))
        for scene in SCENES
        for event in scene.get("events", [])
        if event.get("kind") == "dice_check" and event.get("skill")
    }
    owned = set(SAMPLE_CHARACTER["skills"])
    assert asked <= owned, f"场景要掷但角色没有的技能：{asked - owned}"


def test_pregen_character_can_fight_and_run():
    """战斗幕与追逐幕的最低可玩性：有战斗技能、有武器、移动力键名对得上引擎。"""
    skills = SAMPLE_CHARACTER["skills"]
    assert skills.get("斗殴", 0) >= 25, "没有能用的战斗技能，战斗教学幕只能挨打"
    system_data = SAMPLE_CHARACTER["system_data"]
    assert system_data.get("weapons"), "战斗幕需要至少一件武器"
    # chase_service._quarry_from_char 与 combat_service 读的都是 move，不是 moveRate
    assert system_data.get("move"), "移动力必须写在 move 键上，否则追逐会静默回落成默认值"


def test_every_scene_declares_a_valid_biome():
    """每一幕都要自带地貌。不写 map 的场景在沙盘上会回落成 plain（原野）——
    而这个团整个发生在北海岸的雾港，一张全是原野的沙盘是明显错的。"""
    from app.services import hex_map

    for scene in SCENES:
        biome = ((scene.get("map") or {}).get("biome") or "").strip()
        assert biome, f"场景 {scene['id']} 没声明地貌，沙盘上会变成原野"
        assert biome in hex_map.BIOMES, f"场景 {scene['id']} 的地貌 {biome!r} 不是合法枚举值"


def test_coastal_module_is_not_mapped_as_inland_plain():
    """这是个海岸模组：不该有 plain，且至少半数场景是水/岸。
    守的是「地貌与故事发生地对不上」这类错误，而不是某个具体取值。"""
    biomes = [(scene.get("map") or {}).get("biome") for scene in SCENES]
    assert "plain" not in biomes, "北海岸的雾港不该出现原野"
    coastal = sum(1 for b in biomes if b in ("coast", "water"))
    assert coastal >= len(SCENES) / 2, f"临海场景只有 {coastal}/{len(SCENES)}，与故事发生地不符"


def test_tutorial_beats_are_spelled_out_for_the_kp():
    """战斗与追逐是 KP 自主调用的工具、写不死，所以模组必须在 events 里明写要求。
    这条用例守的是「别把这两句提示不小心删了」。"""
    notes = " ".join(
        str(event.get("note") or "")
        for scene in SCENES
        for event in scene.get("events", [])
    )
    assert "start_combat" in notes, "战斗幕缺少切入战斗轮的明确指示"
    assert "start_chase" in notes, "追逐幕缺少切入追逐的明确指示"
