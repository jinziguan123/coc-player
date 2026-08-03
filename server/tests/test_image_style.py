"""配图画风后缀：单一出处，且不强制黑白。"""

from app.services import human_kp_service, illustration_service, module_image_service


def test_style_suffix_has_single_source():
    """三处配图（模组图 / 局内插图 / 真人 KP 发图）必须共用同一段画风文案。

    原先各写一份完全相同的字符串，改一处另两处就悄悄不一致——同一局里模组图和局内插图
    会是两种画风。
    """
    suffix = module_image_service.IMAGE_STYLE_SUFFIX
    assert illustration_service._ILLUST_STYLE_SUFFIX is suffix
    assert human_kp_service._IMAGE_STYLE is suffix
    assert module_image_service._STYLE_SUFFIX is suffix


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
