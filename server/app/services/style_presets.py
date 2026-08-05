"""文风与画风预设：玩家可选的几档 + 自定义。

**取值约定（两个字段共用）**：字段存的是一个字符串，解析规则只有三条——

1. 空串 → 不做任何注入/替换，行为与没有本特性时**逐字相同**（存量模组与存量存档
   天然落在这一档，不必迁移数据）；
2. 命中预设 id（``terse`` / ``manga_noir`` 这类 **ASCII slug**）→ 用该预设的指令文本；
3. 其余一律当作**用户自定义的原文**，原样使用。

预设 id 全部是 ASCII，自定义文案是中文或英文关键词，二者不会互相误伤；这样一个
字段就够，不必再拿「是预设还是自定义」的第二个字段去和它保持同步（那种成对字段
迟早会不一致）。

**生效层级**：会话（本局）优先，留空则继承模组的默认值。所以模组作者可以给本子
定一套推荐风格，玩家仍能一局一局地改。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 文风

#: 注入 KP 系统提示词的文风指令。
#:
#: 每条只描述**怎么写**，绝不碰「写什么」——叙事纪律（不替玩家行动、不泄线索、
#: 检定先行等）是硬规则，任何文风都不得松动它们，注入处会再申明一次。
NARRATIVE_STYLES: dict[str, dict[str, str]] = {
    "terse": {
        "label": "克制冷硬",
        "hint": "短句、动词优先，情绪靠动作与物象承载",
        "prompt": (
            "行文克制冷硬：以短句为主，动词优先，形容词与副词能删则删；"
            "情绪不直说，交给动作、物象与留白去承载（写「他把杯子放回桌上，没有再拿起来」，"
            "而不是「他显得很失落」）。不做心理解说，不抒情，不总结意义。"
        ),
    },
    "dense": {
        "label": "古典绵密",
        "hint": "长句铺陈、考据式名物细节，缓慢逼近的不安",
        "prompt": (
            "行文古典绵密：容许长句与从句层层铺陈，讲究名物的确切称谓与年代质感"
            "（门闩、灯罩、纸张的成色都值得一笔）；不安从细节的累积中缓慢逼近，"
            "而非靠一句惊呼抵达。用词偏书面，但不堆砌生僻字。"
        ),
    },
    "plain": {
        "label": "白描简净",
        "hint": "只写看得见听得见的，几乎不用比喻",
        "prompt": (
            "行文白描简净：只写看得见、听得见、闻得到的东西，几乎不用比喻和象征；"
            "句子平实，节奏均匀，不制造戏剧性停顿。把判断权完全交给读者——"
            "呈现「窗台上有一道半干的水痕」，而不是「那道水痕透着诡异」。"
        ),
    },
    "cinematic": {
        "label": "影视化",
        "hint": "镜头感、硬切与特写，对白密度高",
        "prompt": (
            "行文影视化：像在写分镜——先给一个可落地的画面，再切到特写或声音，"
            "转场用硬切不用过渡句；对白密度高，让人物在说话中间做动作。"
            "少用全知视角的解说，多用「此刻能被摄影机拍到的东西」。"
        ),
    },
    "pulp": {
        "label": "通俗冒险",
        "hint": "节奏快、动作明快、悬念前置",
        "prompt": (
            "行文通俗冒险：节奏快，段落短，悬念前置——把最抓人的那句放在开头而不是结尾；"
            "动作写得明快利落，危险来得直接。允许轻度的戏剧化与俏皮，但不滑向玩笑，"
            "恐怖场面仍要压得住。"
        ),
    },
}

# ---------------------------------------------------------------- 画风

#: 生图提示词的画风后缀（英文，直接拼给 SD / DALL·E 类模型）。
#:
#: 不含内容红线——红线由 image_style_suffix() 无条件追加，任何预设与自定义都盖不掉。
IMAGE_STYLES: dict[str, dict[str, str]] = {
    "manga_noir": {
        "label": "阴郁漫画",
        "hint": "墨线与网点、低饱和有限色调（默认）",
        "prompt": (
            "moody manga illustration, bold ink lineart, cross-hatching and screentone shading, "
            "limited desaturated palette with one dominant color cast from the scene's own light "
            "source, muted low-saturation tones, gritty dark comic style"
        ),
    },
    "oil_classic": {
        "label": "复古油画",
        "hint": "巴洛克明暗对照、可见笔触、陈年清漆",
        "prompt": (
            "baroque oil painting, dramatic chiaroscuro lighting, visible impasto brushwork, "
            "deep earth tones with candlelit highlights, aged varnish and fine craquelure, "
            "old master canvas texture"
        ),
    },
    "watercolor_ink": {
        "label": "水彩淡墨",
        "hint": "淡彩晕染、墨线勾边、纸纹留白",
        "prompt": (
            "loose watercolor wash with ink outline, soft bleeding edges and granulation, "
            "pale muted washes, generous negative space, visible cold-press paper texture, "
            "delicate and airy"
        ),
    },
    "film_realistic": {
        "label": "胶片写实",
        "hint": "35mm 颗粒、浅景深、实用光源",
        "prompt": (
            "photorealistic film still, 35mm film grain, shallow depth of field, "
            "practical light sources only, naturalistic color grading, subtle halation, "
            "documentary framing"
        ),
    },
    "woodcut": {
        "label": "木刻版画",
        "hint": "高反差黑白块面、手工刻痕",
        "prompt": (
            "high-contrast woodcut print, bold black shapes and stark white paper, "
            "hand-carved gouge texture, coarse parallel hatching, two-tone limited palette, "
            "folk broadside engraving"
        ),
    },
}

#: 内容红线：无条件追加在**任何**画风（预设或自定义）之后。
#:
#: 见 module_image_service.SAFETY_PROMPT_RULE 的三道闸说明——这是其中的「正向兜底」那道，
#: 所以它必须在用户可编辑的部分**之后**，且不经过任何解析分支。
IMAGE_SAFETY_TAIL = "fully clothed, non-sexual, sfw"

#: 未选画风时用哪一档（保持本特性上线前的既有观感）。
DEFAULT_IMAGE_STYLE = "manga_noir"


# ---------------------------------------------------------------- 解析

def _resolve(value: str | None, table: dict[str, dict[str, str]]) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    preset = table.get(text)
    return preset["prompt"] if preset else text


def narrative_style_prompt(session_value: str | None, module_value: str | None = None) -> str:
    """本局生效的文风指令（会话优先，留空继承模组）；都没有则空串 = 不注入。"""
    return _resolve(session_value, NARRATIVE_STYLES) or _resolve(
        module_value, NARRATIVE_STYLES,
    )


def image_style_suffix(session_value: str | None = None, module_value: str | None = None) -> str:
    """本局生效的画风后缀，**末尾无条件带上内容红线**。

    都没选时回落到 DEFAULT_IMAGE_STYLE——生图不像叙事，没有「不加画风」这一档：
    不给画风词，模型会自己滚出一张风格随机的图，场景之间立刻失去一致性。
    """
    body = (
        _resolve(session_value, IMAGE_STYLES)
        or _resolve(module_value, IMAGE_STYLES)
        or IMAGE_STYLES[DEFAULT_IMAGE_STYLE]["prompt"]
    )
    return f"{body}, {IMAGE_SAFETY_TAIL}"


def style_options() -> dict[str, list[dict[str, str]]]:
    """给前端下拉用的预设清单（id/label/hint）。"""
    return {
        "narrative": [
            {"id": k, "label": v["label"], "hint": v["hint"]}
            for k, v in NARRATIVE_STYLES.items()
        ],
        "image": [
            {"id": k, "label": v["label"], "hint": v["hint"]}
            for k, v in IMAGE_STYLES.items()
        ],
    }
