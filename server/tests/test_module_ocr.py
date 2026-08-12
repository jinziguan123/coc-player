"""OCR 前置：并发闸、fail-open、页序拼接，以及上传链路的接线（全程打桩，不打真实接口）。"""

import asyncio

from app.api import modules as modules_api
from app.services import module_ocr


class _Vision:
    """按图片字节返回固定文字的假视觉模型，并记录最大并发。"""

    def __init__(self, texts=None, fail_on=(), delay=0.0):
        self.texts = texts
        self.fail_on = set(fail_on)
        self.delay = delay
        self.calls: list[str] = []
        self.live = 0
        self.peak = 0

    def supports_vision(self):
        return True

    async def complete_vision(self, prompt, images, max_tokens=None):
        assert max_tokens is None, "项目硬约定：一切 LLM 调用不设 max_tokens"
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            b64 = images[0][0]
            self.calls.append(prompt)
            if b64 in self.fail_on:
                raise RuntimeError("boom")
            return self.texts.pop(0) if self.texts else "识别出的文字"
        finally:
            self.live -= 1


def _imgs(n):
    return [(f"page{i}".encode(), "image/png") for i in range(n)]


def test_逐页识别保持入参顺序():
    llm = _Vision(texts=["第一页", "第二页", "第三页"])
    pages = asyncio.run(module_ocr.ocr_images(_imgs(3), llm=llm))
    assert pages == ["第一页", "第二页", "第三页"]
    assert llm.calls[0] == module_ocr.OCR_PROMPT


def test_单页失败只丢那一页():
    import base64

    bad = base64.b64encode(b"page1").decode()
    llm = _Vision(texts=["A", "C"], fail_on=[bad])
    pages = asyncio.run(module_ocr.ocr_images(_imgs(3), llm=llm))
    assert pages[1] == ""                      # 坏的那页留空，不炸整本
    assert [p for p in pages if p] == ["A", "C"]


def test_并发不超过闸门():
    llm = _Vision(delay=0.02)
    asyncio.run(module_ocr.ocr_images(_imgs(9), llm=llm))
    assert llm.peak <= module_ocr.MAX_CONCURRENCY


def test_没有视觉模型时全空而不是报错():
    class TextOnly:
        def supports_vision(self):
            return False

    pages = asyncio.run(module_ocr.ocr_images(_imgs(3), llm=TextOnly()))
    assert pages == ["", "", ""]               # 调用方据此回落图片路径


def test_拼接带页码且并入已有文字层():
    merged = module_ocr.merge_ocr_text(["甲", "", "乙"], extra_text="封底文字")
    assert merged.startswith("封底文字")
    assert "=== 第 1 页 ===\n甲" in merged
    assert "=== 第 2 页 ===\n乙" in merged   # 空页不占页码
    assert module_ocr.ocr_coverage(["甲", "", "乙"]) == (2, 3)


def test_上传任务开关打开时改走文本链路(monkeypatch):
    """OCR 出文字后，图片路径不该再被调用——整本已经变成纯文本模组。"""
    seen = {}

    async def fake_ocr(images, llm=None):
        return ["第一页正文", "第二页正文"]

    async def fake_text(raw_text, rule_system, on_progress=None):
        seen["text_raw"] = raw_text
        return {"title": "T", "scenes": [], "npcs": [], "clues": []}

    async def fake_images(*a, **k):
        seen["图片路径被调用"] = True
        return {}

    monkeypatch.setattr(modules_api.module_ocr, "ocr_images", fake_ocr)
    monkeypatch.setattr(modules_api.module_service, "parse_module_text", fake_text)
    monkeypatch.setattr(modules_api.module_service, "parse_module_images", fake_images)
    monkeypatch.setattr(modules_api.module_service, "supplement_parse",
                        lambda raw, parsed, rs: asyncio.sleep(0, result=parsed))

    job = modules_api._job_new()
    asyncio.run(modules_api._run_upload_job(job, "", _imgs(2), "coc", ocr_prepass=True))

    assert "图片路径被调用" not in seen
    assert "第一页正文" in seen["text_raw"] and "第二页正文" in seen["text_raw"]


def test_OCR全失败时回落图片路径(monkeypatch):
    """一张都没认出来 → 不能让实验开关把本来能解析的模组变成解析不了。"""
    seen = {}

    async def fake_ocr(images, llm=None):
        return ["", ""]

    async def fake_images(images, rule_system, extra_text=""):
        seen["图片路径被调用"] = True
        return {"title": "T", "scenes": [], "npcs": [], "clues": []}

    monkeypatch.setattr(modules_api.module_ocr, "ocr_images", fake_ocr)
    monkeypatch.setattr(modules_api.module_service, "parse_module_images", fake_images)
    monkeypatch.setattr(modules_api.module_service, "supplement_parse",
                        lambda raw, parsed, rs: asyncio.sleep(0, result=parsed))

    job = modules_api._job_new()
    asyncio.run(modules_api._run_upload_job(job, "", _imgs(2), "coc", ocr_prepass=True))
    assert seen.get("图片路径被调用") is True


def test_pdf抽图两种取法():
    """OCR 前置要页序（扫描件一页一张图，按体积挑会丢页乱序）；视觉解析仍按体积挑。"""
    class _Img:
        def __init__(self, data):
            self.data = data
            self.image = None

    class _Page:
        def __init__(self, imgs):
            self.images = imgs

    class _Reader:
        # 页序：小、大、中
        pages = [_Page([_Img(b"s" * 4000)]), _Page([_Img(b"L" * 9000)]), _Page([_Img(b"m" * 6000)])]

    monkey = []

    def fake_norm(pil, data):
        monkey.append(data[:1])
        return (data, "image/jpeg")

    orig = modules_api._normalize_image
    modules_api._normalize_image = fake_norm
    try:
        monkey.clear()
        modules_api._select_pdf_images(_Reader(), max_images=3, keep_page_order=True)
        assert monkey == [b"s", b"L", b"m"]      # 页序
        monkey.clear()
        modules_api._select_pdf_images(_Reader(), max_images=3)
        assert monkey == [b"L", b"m", b"s"]      # 体积降序
    finally:
        modules_api._normalize_image = orig
