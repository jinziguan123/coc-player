"""配图画风后缀：单一出处，且不强制黑白。"""

from app.services import human_kp_service, illustration_service, module_image_service


def test_style_suffix_has_single_source():
    """三处配图（模组图 / 局内插图 / 真人 KP 发图）必须经同一个 style_suffix_for() 取画风。

    原先各存一份常量，改一处另两处就悄悄不一致——同一局里模组图和局内插图会是两种画风。
    现在画风还可以按模组/按局改，各存一份的后果从「文案不同步」升级成「设置根本不生效」。
    """
    assert not hasattr(illustration_service, "_ILLUST_STYLE_SUFFIX")
    assert not hasattr(human_kp_service, "_IMAGE_STYLE")
    assert module_image_service._STYLE_SUFFIX is module_image_service.IMAGE_STYLE_SUFFIX


def test_style_does_not_force_black_and_white():
    """不再强制黑白：每张图都压成灰的，场景之间就失去了区分度。

    煤油灯的暖黄、地窖的霉绿、雪夜的冷蓝本该是各自最有辨识度的东西，也是「场景氛围底」
    赖以成立的前提——它拿配图当色调来源，源图没颜色就没色调可取。
    """
    suffix = module_image_service.IMAGE_STYLE_SUFFIX.lower()
    assert "black and white" not in suffix
    assert "monochrome" not in suffix
    assert "grayscale" not in suffix
    # 但仍要保住阴郁墨线漫画的基调，别放成艳丽全彩
    assert "desaturated" in suffix or "low-saturation" in suffix
    assert "ink lineart" in suffix
    # 每个场景应有自己的主色倾向（氛围底靠它区分地点）
    assert "dominant color cast" in suffix


def test_every_prompt_writer_carries_the_content_rule():
    """所有写生图提示词的系统提示都要带内容红线。

    漏掉任何一处，那条路径的提示词就会把模组原文里「衣着不整的受害者」这类描述原样翻成
    露骨的英文提示词——而本地 SD 检查点不少是 NSFW 微调，出图就真会露骨。
    """
    from app.services import illustration_service as illust
    from app.services import module_map_service as maps

    prompts = {
        "模组场景": module_image_service.SCENE_PROMPT_SYS,
        "模组 NPC": module_image_service.NPC_PROMPT_SYS,
        "模组遭遇": module_image_service.ENCOUNTER_PROMPT_SYS,
        "模组线索": module_image_service.CLUE_PROMPT_SYS,
        "局内手书": illust._HANDOUT_PROMPT_SYS,
        "局内场景": illust._SCENE_ILLUST_PROMPT_SYS,
        "局内立绘": illust._NPC_PORTRAIT_PROMPT_SYS,
        "局内线索": illust._CLUE_ILLUST_PROMPT_SYS,
        "局内遭遇": illust._ENCOUNTER_ILLUST_PROMPT_SYS,
        "沙盘底图": maps._BACKDROP_PROMPT_SYS,
    }
    missing = [name for name, text in prompts.items()
               if module_image_service.SAFETY_PROMPT_RULE not in text]
    assert not missing, f"这些生图提示词漏了内容红线：{missing}"


def test_positive_suffix_carries_sfw_hint():
    """OpenAI 兼容的生图接口没有负面提示词参数，那条路径只能靠正向后缀兜底。"""
    suffix = module_image_service.IMAGE_STYLE_SUFFIX.lower()
    assert "fully clothed" in suffix and "sfw" in suffix


def test_comfyui_negative_always_includes_safety_terms():
    """负面词直接作用于采样，是最硬的一道闸；用户自定的负面词只能往上加、不能把它顶掉。"""
    from app.ai import comfyui

    def negatives(user_negative: str) -> list[str]:
        wf = comfyui.build_workflow("", "a foggy pier", user_negative)
        return [n["inputs"]["text"] for n in wf.values()
                if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode"]

    for user_negative in ("", "my own negative words"):
        joined = " ".join(negatives(user_negative))
        assert "nsfw" in joined and "nude" in joined, f"负面词漏了内容红线：{user_negative!r}"
    # 用户自己的负面词仍然保留，不是被替换掉
    assert "my own negative words" in " ".join(negatives("my own negative words"))
