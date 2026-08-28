"""新手团使用的项目原创短模组与预设调查员。

这一版把新手团从「两个场景的示例」改成**贯穿式教学关**：六幕依次覆盖
探索取证 → 技能检定与难度 → 理智检定 → 结构化战斗轮 → 追逐 → 结局，
每幕只教一件事、教完就收束，让玩家跑完一局就摸过跑团的全部主要环节。

关于「能写死」与「只能暗示」——
* 理智检定能写死：``planned_effects._apply_scene_sanity_mechanism`` 会用场景
  events 里的 ``san_loss`` 规格覆盖 AI 的猜测，所以第三幕的 0/1d3 是确定的。
* 战斗与追逐**写不死**：``start_combat`` / ``start_chase`` 是 KP 自主调用的工具。
  这两幕只能靠三重手段逼出来——把场景 ``danger`` 抬到 dangerous、在 events 里用
  ``note`` 明写「此处必须切入战斗轮／追逐」、并给敌方 NPC 备齐
  attributes/hp/armor/weapon，让工具一被调用就能跑起来。
"""

# 必须换 slug 才会重新播种：onboarding_service._sample_module 是按 slug 找现存模组的，
# 沿用 v1 的话，已经跑过新手团的用户永远拿不到这版教学关。
SAMPLE_SLUG = "first-case-v2"

SAMPLE_MODULE = {
    "title": "雾港失灯事件",
    "rule_system": "coc",
    "description": "一座雾港灯塔无故熄灭。六幕短团，边跑边学会调查、检定、理智、战斗与追逐。",
    "theme": "default",
    "world_setting": {
        "source": "trpg-player-original",
        "sample_slug": SAMPLE_SLUG,
        "era": "1920s",
        "region": "北海岸",
        "location": "雾港",
        "tone": "悬疑、轻度恐怖",
        "difficulty": "入门",
        "player_count": "1",
        "tags": ["原创", "新手", "教学", "调查"],
        "intro": (
            "北海岸的雾港靠一座老灯塔指引夜航。今夜，灯火第一次无故熄灭，"
            "而退潮比历书上早了整整两个钟头。"
        ),
        "player_brief": (
            "你是本地报社的记者许闻舟。港务员林恩连夜找上门，"
            "希望你赶在天亮那艘邮船进港之前，查明灯塔为什么灭了。"
        ),
        # 给 KP 的教学总纲：新手团的目的不只是跑完，而是让玩家逐个摸到机制。
        "kp_guidance": (
            "【这是新手教学关】玩家很可能是第一次跑团。请在保持沉浸的前提下做到："
            "①每一幕只推进一个新机制，不要把战斗和追逐挤进同一幕；"
            "②要求检定前，先用一句话说明为什么需要检定"
            "（如「铁梯结了盐霜，上去得过一次攀爬」），让玩家理解检定是怎么被触发的；"
            "③检定失败不要卡死流程，改判『成功但有代价』或换一条路继续；"
            "④玩家卡住超过两轮时，让 NPC 或环境给出下一步方向；"
            "⑤严格按各场景 events 写明的机制点推进——尤其是第三幕理智检定的损失规格、"
            "第四幕切入战斗轮、第五幕切入追逐这三处，不要用叙述糊弄过去。"
        ),
    },
    "raw_content": "本模组为 CoC Player 项目原创新手教学示例内容。",
    "truth": (
        "灯塔看守老崔三个月前在退潮的礁盘上捡到一块刻着螺旋纹的石板，此后夜夜梦见海底的钟声。"
        "他按石板的指示在灯室地板刻下螺旋、把备用灯芯全浸了海水——熄灯不是事故，是一次献祭，"
        "为了让海里的东西循着黑暗上岸接他。老崔本人已在昨夜退潮时走进海里；"
        "留在灯塔里的是先上来的一只深潜者幼体。只要灯重新亮起，潮水就会退回去。"
    ),
    "scenes": [
        # —— 第一幕：探索与对话。教「用自己的话行动」「检定是怎么来的」——
        {
            "id": "harbor_office",
            "map": {"biome": "coast"},
            "name": "港务所",
            "description": (
                "潮湿的木屋里堆着航海日志，煤油灯把林恩的影子钉在墙上。"
                "窗外浓雾贴着玻璃流动，远处传来一声闷钟。桌上摊着今夜的值班簿，"
                "最上面那一页边缘参差不齐——有一页被撕走了。"
            ),
            "danger": "calm",
            "atmosphere": "焦急、潮湿、钟声遥远",
            "connections": ["fog_pier"],
            "events": [
                {
                    "trigger": "玩家首次进入港务所",
                    "kind": "note",
                    "note": (
                        "开场只描述环境与林恩的紧张，不要替玩家决定做什么。"
                        "玩家第一次开口后，顺势点明他可以直接描述行动，也可以用「」说台词。"
                    ),
                },
                {
                    "trigger": "玩家调查值班簿或航海日志",
                    "kind": "dice_check",
                    "skill": "侦查",
                    "note": "成功给出『被撕下的航海日志』；失败只发现纸边新鲜，仍可换别的方式追查。",
                },
                {
                    "trigger": "玩家追问林恩昨夜听见了什么",
                    "kind": "dice_check",
                    "skill": "心理学",
                    "note": "成功让林恩吐露那三声钟响；失败他只说『风声罢了』，玩家可改用话术再试。",
                },
            ],
        },
        # —— 第二幕：检定难度。教「难度分级」与「失败也推进」——
        {
            "id": "fog_pier",
            "map": {"biome": "water"},
            "name": "雾中栈桥",
            "description": (
                "通往灯塔的木栈桥浸在齐膝的雾里，木板被水汽泡得发滑，缆绳结着盐霜。"
                "走到一半会发现有几块板子是新钉上去的——钉头还没生锈，钉得很急。"
            ),
            "danger": "uneasy",
            "atmosphere": "湿滑、能见度极低、脚下有回声",
            "connections": ["harbor_office", "lighthouse_base"],
            "events": [
                {
                    "trigger": "玩家通过湿滑的栈桥",
                    "kind": "dice_check",
                    "skill": "攀爬",
                    "note": (
                        "这一幕专教难度分级：先说明稳着走是普通难度，选择跑步冲过去则升为困难。"
                        "失败不判落水致死——改为滑落挂在桥沿（1d3 伤害或丢掉一件手持物），"
                        "仍可爬上来继续。"
                    ),
                },
                {
                    "trigger": "玩家检查新钉上的木板",
                    "kind": "dice_check",
                    "skill": "侦查",
                    "note": "成功给出『仓促修补的桥板』：破损是从下方顶坏的，不是浪打的。",
                },
            ],
        },
        # —— 第三幕：理智检定。san_loss 规格写死，引擎会照此覆盖 ——
        {
            "id": "lighthouse_base",
            "map": {"biome": "coast"},
            "name": "灯塔底层",
            "description": (
                "铁门虚掩。底层堆着煤油桶与备用灯芯，墙上却用粗盐画满同一种螺旋，"
                "一圈套一圈，从地面一直画到举灯照不到的高处。"
                "空气里是浓得发苦的海腥味，像退潮后晒了三天的滩涂。"
            ),
            "danger": "uneasy",
            "atmosphere": "盐、海腥、螺旋在灯下缓慢转动的错觉",
            "connections": ["fog_pier", "lamp_room"],
            "events": [
                {
                    "trigger": "玩家看清墙上用盐画满的螺旋",
                    "kind": "san_check",
                    "san_loss": "0/1d3",
                    "note": (
                        "本团第一次理智检定，也是教学点：投骰前先说明理智值是什么、为什么这里要掷；"
                        "掷完无论成败都给出具体的身体反应，不要只报数字。成功损失 0，失败损失 1d3。"
                    ),
                },
                {
                    "trigger": "玩家搜查煤油桶与备用灯芯",
                    "kind": "dice_check",
                    "skill": "侦查",
                    "note": "成功给出『泡过海水的灯芯』——灯芯是被人主动浸湿的，熄灯是人为。",
                },
            ],
        },
        # —— 第四幕：战斗。KP 需自行调用 start_combat，这里用 note 明写 ——
        {
            "id": "lamp_room",
            "map": {"biome": "interior"},
            "name": "灯室",
            "description": (
                "螺旋铁梯尽头就是灯室。巨大的菲涅尔透镜蒙着一层盐膜，灯座是冷的。"
                "地板正中刻着一枚半米宽的螺旋，凹槽里积着还没干的海水；"
                "从螺旋中心开始有一串湿脚印——它们没有从门口或窗边延伸过来。"
            ),
            "danger": "dangerous",
            "atmosphere": "黑暗、海风灌进破窗、金属摩擦声就在头顶",
            "connections": ["lighthouse_base", "tide_stair"],
            "events": [
                {
                    "trigger": "玩家走近灯座或试图重新点燃灯火",
                    "kind": "note",
                    "note": (
                        "【必须切入结构化战斗轮】潜伏在透镜背后的深潜者幼体扑下来。"
                        "此处调用 start_combat（enemies=深潜者幼体），不要用叙述代替战斗轮。"
                        "这是本团的战斗教学点：开打前用一句话说明接下来按先攻轮流行动。"
                        "另外记得幼体畏光——玩家若想到用火柴或手电逼退它，是合法解法，不必打死。"
                    ),
                },
                {
                    "trigger": "玩家在战斗结束后检查灯座",
                    "kind": "dice_check",
                    "skill": "侦查",
                    "note": "成功给出『刻在灯座下的石板』，指向老崔与献祭的真相。",
                },
            ],
        },
        # —— 第五幕：追逐。同样只能暗示，用 note 明写 ——
        {
            "id": "tide_stair",
            "map": {"biome": "water"},
            "name": "退潮阶梯",
            "description": (
                "灯塔背面有一道凿进礁石的阶梯，平时淹在水下，今夜整条露了出来，"
                "湿漉漉地通向礁盘深处。海面下有东西在动，不止一个。"
            ),
            "danger": "dangerous",
            "atmosphere": "海水倒灌的吸吮声、越来越近的钟响",
            "connections": ["lamp_room", "dawn_harbor"],
            "events": [
                {
                    "trigger": "玩家拿到真相后转身撤离灯塔",
                    "kind": "note",
                    "note": (
                        "【必须切入追逐】海面浮起更多身影，堵向阶梯。"
                        "此处调用 start_chase（pursuer=潮涌群），玩家为逃方。"
                        "这是本团的追逐教学点：开始前说明追逐按回合拉开/缩短距离，"
                        "不是一次检定定生死。若玩家此前已点亮灯塔，追兵畏光、"
                        "追逐应更快判脱身。"
                    ),
                },
            ],
        },
        # —— 第六幕：结局。按 flags 分支 ——
        {
            "id": "dawn_harbor",
            "map": {"biome": "coast"},
            "name": "黎明港口",
            "description": (
                "天边泛白，潮水正沿着礁盘退回去。港口的钟敲了五下，"
                "邮船的汽笛在雾里由远及近。林恩站在栈桥尽头，手里攥着那本值班簿。"
            ),
            "danger": "calm",
            "atmosphere": "疲惫、咸腥的晨风、劫后余生",
            "connections": ["tide_stair"],
            "events": [
                {
                    "trigger": "玩家抵达黎明港口",
                    "kind": "note",
                    "note": (
                        "【结局幕】按玩家掌握的情况收束，明确给出三种结局之一："
                        "①已重新点亮灯火（flag lighthouse_relit）——潮水退去、邮船安全进港，最好结局；"
                        "②知道真相但灯没亮（flag knows_truth）——船安全，但雾里的钟声还会再响，留悬念；"
                        "③两者皆无——邮船触礁，林恩沉默地合上值班簿，最差结局。"
                        "收束之后，用一段话回顾玩家这一路用过的机制"
                        "（调查取证、技能检定与难度、理智检定、战斗轮、追逐），"
                        "点明这就是一场完整跑团的骨架，并提示可以去「模组」页导入自己的剧本开新团。"
                    ),
                },
            ],
        },
    ],
    "npcs": [
        {
            "id": "harbor_master_lin",
            "name": "林恩港务员",
            "description": "一位眼下乌青、反复核对怀表的中年人。",
            "personality": "务实而紧张，不愿让港口陷入恐慌；被追问时先回避，再松口",
            "background": "负责记录船只进出与灯塔维护，与看守老崔共事十一年。",
            "secrets": [
                "昨夜听见灯塔方向传来三次短促钟声，却没有写进值班记录。",
                "他知道老崔这三个月夜夜独自去礁盘，一直替他瞒着。",
            ],
            "goals": ["赶在邮船进港前让灯重新亮起", "别让港口知道老崔出了事"],
            "initial_location": "harbor_office",
            "skills": {"话术": 45, "心理学": 35, "母语": 60},
            "attributes": {"STR": 50, "CON": 55, "SIZ": 60, "DEX": 45, "POW": 55, "EDU": 60},
            "hp": 11,
        },
        {
            "id": "deep_one_spawn",
            "name": "深潜者幼体",
            "description": "半人半鱼的佝偻身形，皮肤覆着湿冷的鳞，指间连着蹼。",
            "personality": "护巢、畏光；被火光或灯光逼近时退向破窗",
            "background": "循着灯塔的黑暗先一步上岸，守在灯室里阻止有人重新点灯。",
            "secrets": ["它怕光——重新点亮的灯火会让它主动撤离，不必杀死它。"],
            "goals": ["阻止任何人点亮灯塔"],
            "initial_location": "lamp_room",
            # 战斗必需：combat_service 从这几个字段建参战方（attributes/hp/armor/weapon/skills）
            "attributes": {"STR": 65, "CON": 60, "SIZ": 55, "DEX": 55, "POW": 50},
            "hp": 12,
            "armor": 1,
            "weapon": "利爪",
            "skills": {"斗殴": 50, "闪避": 40},
        },
        {
            "id": "tide_swarm",
            "name": "潮涌群",
            "description": "海面下浮起的一片身影，数不清有多少，只看得见连成一线的背脊。",
            "personality": "沉默、成群、不追进灯塔的光里",
            "background": "循着熄灭的灯上岸的同类。",
            "goals": ["把闯入者留在礁盘上"],
            "initial_location": "tide_stair",
            "attributes": {"STR": 60, "CON": 60, "SIZ": 55, "DEX": 60, "POW": 50},
            "hp": 14,
            "armor": 1,
            "weapon": "拖拽",
            "skills": {"斗殴": 45},
        },
    ],
    "clues": [
        {
            "id": "torn_log_page",
            "name": "被撕下的航海日志",
            "description": "纸边的新鲜纤维说明这一页刚被撕走，背页留下「退潮后开门」的压痕。",
            "location": "harbor_office",
            "trigger_condition": "调查值班簿并通过侦查检定",
        },
        {
            "id": "three_bells",
            "name": "没写进记录的三声钟",
            "description": "林恩承认昨夜灯塔方向传来三次短促钟声——那口钟三年前就锈死了。",
            "location": "harbor_office",
            "trigger_condition": "通过心理学或话术让林恩松口",
        },
        {
            "id": "patched_planks",
            "name": "仓促修补的桥板",
            "description": "新钉的桥板是从下方被顶坏的——有东西从水里上来过，不止一次。",
            "location": "fog_pier",
            "trigger_condition": "在栈桥检查新木板并通过侦查检定",
        },
        {
            "id": "salt_spiral",
            "name": "盐画的螺旋",
            "description": "同一枚螺旋从地面画到高处，笔画方向一致，是同一个人反复画了很多夜。",
            "location": "lighthouse_base",
            "trigger_condition": "进入灯塔底层即可见",
        },
        {
            "id": "soaked_wick",
            "name": "泡过海水的灯芯",
            "description": "备用灯芯全被浸在海水里拧干过——灯不是自己灭的，是有人让它灭的。",
            "location": "lighthouse_base",
            "trigger_condition": "搜查煤油桶与备用灯芯并通过侦查检定",
        },
        {
            "id": "keeper_tablet",
            "name": "刻在灯座下的石板",
            "description": (
                "巴掌大的石板压在灯座底下，螺旋纹之间刻着老崔的字："
                "「他们说只要灯不亮，就能上来接我。」"
            ),
            "location": "lamp_room",
            "trigger_condition": "战斗结束后检查灯座并通过侦查检定",
        },
    ],
    "maps": [],
    "triggers": [
        {
            "id": "trg_knows_truth",
            "when": "玩家取得『刻在灯座下的石板』，明白熄灯是老崔的献祭",
            "set_flags": ["knows_truth"],
            "description": "掌握真相，结局至少为「船安全但留悬念」。",
        },
        {
            "id": "trg_lighthouse_relit",
            "when": "玩家换上干燥灯芯并重新点亮灯塔",
            "set_flags": ["lighthouse_relit"],
            "description": "灯火重燃：潮水退去、深潜者畏光撤离，通向最好结局。",
        },
        {
            "id": "trg_tutorial_done",
            "when": "玩家抵达黎明港口并听完收束",
            "set_flags": ["tutorial_done"],
            "description": "教学关走完，可提示玩家导入自己的模组开新团。",
        },
    ],
    "handouts": [],
}

SAMPLE_CHARACTER = {
    "name": "许闻舟",
    "rule_system": "coc",
    "is_player": True,
    "base_attributes": {
        "STR": 50,
        "CON": 55,
        "SIZ": 50,
        "DEX": 60,
        "APP": 50,
        "INT": 70,
        "POW": 60,
        "EDU": 65,
        "LUCK": 55,
    },
    # 技能表按六幕配齐：每一幕要用到的技能都得有个能打的数值，
    # 否则新手第一次跑就连着失败，学到的只有挫败感。
    "skills": {
        "侦查": 65,      # 一/二/三/四幕取证
        "聆听": 55,
        "图书馆使用": 60,
        "心理学": 45,    # 一幕撬开林恩的嘴
        "话术": 40,      # 一幕的备选路径
        "攀爬": 45,      # 二幕栈桥
        "斗殴": 45,      # 四幕战斗——原版一个战斗技能都没有，进了战斗轮只能挨打
        "闪避": 40,
        "母语": 65,
        # 信用评级是 CoC 的正式技能，缺了它角色卡上的「信用评级」一栏恒为 0
        "信用评级": 35,
    },
    "system_data": {
        "occupation": "记者",
        "age": 29,
        "gender": "男",
        "residence": "雾港",
        "birthplace": "内陆·安溪",
        "hitPoints": {"current": 10, "max": 10},
        "sanity": {"current": 60, "max": 99},
        "magicPoints": {"current": 12, "max": 12},
        # 幸运在角色卡面板上单独占一行，也是属性雷达的一根轴。
        # 只写在 base_attributes.LUCK 里的话，老版面板读不到会显示 0。
        "luck": 55,
        "creditRating": 35,
        # 键名必须是 move：chase_service 与 combat_service 都读 sd["move"]，
        # 旧版写的 moveRate 两处都不认，追逐/战斗时会静默回落成默认值。
        "move": 8,
        "build": 0,
        "damageBonus": "0",
        "equipment": ["笔记本", "钢笔", "手电筒", "相机", "火柴"],
        # 四幕战斗要有东西可用：撬棍既是破门工具也是武器，符合记者闯灯塔的情境。
        "weapons": [
            {"name": "撬棍", "skill": "斗殴", "success": 45, "dam": "1d8", "range": "接触"},
        ],
    },
    "backstory": "地方报记者，对无法解释的细节有近乎固执的好奇心。",
}

# 角色卡「基本信息」页的背景分段与「档案」页的资产/人际，都从 system_data 的这些键读。
# 预设调查员是新用户见到的第一张角色卡，全空会让人以为功能坏了——这里补齐成一张完整的卡。
SAMPLE_CHARACTER["system_data"].update(
    {
        "personalDescription": "瘦高，风衣袖口常年沾着油墨；说话前习惯先按亮怀里的手电确认电量。",
        "ideologyBeliefs": "没有解释不了的事，只有还没找到的那一页记录。",
        "significantPeople": "带他入行的老主编，去年冬天倒在排版房里，稿子还压在手下。",
        "meaningfulLocations": "报社顶楼的晒版台——他在那里第一次看清整座雾港的轮廓。",
        "treasuredPossessions": "一台二手相机，镜头有道划痕，拍出来的照片左上角永远发白。",
        "traits": "过分好奇；一旦开始记笔记就很难停下。",
        "cash": 40,
        "spendingLevel": 10,
        "assets": "报社宿舍一间，旧自行车一辆。",
        "relations": [
            {"name": "林恩港务员", "relation": "旧识，偶尔互通消息"},
        ],
    }
)
