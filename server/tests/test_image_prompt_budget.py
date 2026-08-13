"""生图提示词的词数预算：画风词必须和正文挤在同一个 CLIP 窗口里。

CLIP 一次只吃 77 token。正文一长，挂在尾部的画风后缀就掉进第二个 chunk、权重骤降，
模型回落到自己的写实倾向——实测同一句正文、同一个后缀，27 词出标准墨线漫画，
63 词出写实照片。所以词数不能只写进提示词里指望模型自觉，必须在代码里硬截。
"""

import pytest

from app.services import module_image_service as mis


def _words(text: str) -> int:
    return len(text.split())


def test_不超限的原样返回():
    raw = "dimly lit 1920s hallway, faded floral wallpaper, brass key on an oak desk"
    assert mis.trim_prompt(raw) == raw


def test_超限时砍在逗号处():
    """砍在词中间会留下半个短语（`single bare bulb casting a pale` 这种）。"""
    raw = (
        "dimly lit 1920s boarding house hallway, aged wallpaper with faded floral pattern, "
        "worn wooden stair railing, single bare bulb casting a pale circle of light on the floor, "
        "narrow door ajar revealing a cluttered study, dust motes drifting, heavy curtains drawn, "
        "quiet stillness, early evening atmosphere"
    )
    out = mis.trim_prompt(raw)
    assert _words(out) <= mis.PROMPT_MAX_WORDS
    assert _words(raw) > mis.PROMPT_MAX_WORDS          # 前提：这条确实超限
    assert out.startswith("dimly lit 1920s boarding house hallway")
    assert all(part.strip() in raw for part in out.split(","))   # 每段都是原文的完整短语


def test_首个短语再长也保留():
    """它是画面主体，砍掉就什么都不剩了——宁可超一点，也不能返回空。"""
    raw = " ".join(["a"] * 60) + ", extra detail"
    out = mis.trim_prompt(raw)
    assert out.startswith("a a a")
    assert "extra detail" not in out


def test_只取首行并清理空段():
    raw = "  1920s hallway,, , brass key  \n第二行应当忽略\n第三行"
    assert mis.trim_prompt(raw) == "1920s hallway, brass key"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_空输入返回空串(raw):
    assert mis.trim_prompt(raw) == ""


def test_画风纪律带上词数与画风原文():
    suffix = mis.style_suffix_for()
    text = mis.style_discipline(suffix)
    assert suffix in text                              # 写提示词的模型要看得到成图画风
    assert str(mis.PROMPT_MAX_WORDS) in text
    for banned in ("sepia", "photograph", "vibrant"):  # 媒介与调色两类都要点名
        assert banned in text


def test_正文加画风后缀留得下余量():
    """上限的意义在于给后缀留位置：两者相加仍应在 77 token 的量级内
    （英文词与 token 大致 1:1.3，这里按词数留出余量即可）。"""
    suffix_words = _words(mis.style_suffix_for())
    assert mis.PROMPT_MAX_WORDS + suffix_words <= 75
