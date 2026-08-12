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


#: 测试用的「像样的一页正文」——短于 _MIN_PAGE_CHARS 的产物会被当成没认出东西丢掉，
#: 所以桩数据得有真实体量，不能用「第一页」这种两三个字的占位。
def _page(n: int) -> str:
    return f"第{n}页 陵墓探查\n沙丘之下露出半截石阶，风把碎沙吹进门缝里，调查员们停在阶前。"


def test_逐页识别保持入参顺序():
    llm = _Vision(texts=[_page(1), _page(2), _page(3)])
    pages = asyncio.run(module_ocr.ocr_images(_imgs(3), llm=llm))
    assert pages == [_page(1), _page(2), _page(3)]
    assert llm.calls[0] == module_ocr.OCR_PROMPT


def test_单页失败只丢那一页():
    import base64

    bad = base64.b64encode(b"page1").decode()
    llm = _Vision(texts=[_page(1), _page(3)], fail_on=[bad])
    pages = asyncio.run(module_ocr.ocr_images(_imgs(3), llm=llm))
    assert pages[1] == ""                      # 坏的那页留空，不炸整本
    assert [p for p in pages if p] == [_page(1), _page(3)]


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


def test_插画自述被丢弃而不是拼进原文():
    """模组 PDF 里混着大量插画与装饰底纹，模型会老实回一段「这张图没有文字」。
    那些自述若原样拼进解析输入，等于给解析器灌无关描述，比不做 OCR 还差。"""
    llm = _Vision(texts=[
        "这张图片中没有包含任何文字内容。\n\n它展示的是一本打开的空白旧书，页面顶部有红色装饰边框。",
        "第一章 陵墓入口\n沙丘之下露出半截石阶，风把碎沙吹进门缝里。调查员们停在阶前。",
        "经过仔细检查，这张图片是一幅插画（描绘了探险者面对木乃伊的场景），图片中没有任何可见的文字内容。",
        "无字",   # 太短：同样视为没认出东西
    ])
    pages = asyncio.run(module_ocr.ocr_images(_imgs(4), llm=llm))
    assert pages[0] == "" and pages[2] == "" and pages[3] == ""
    assert pages[1].startswith("第一章 陵墓入口")
    assert module_ocr.ocr_coverage(pages) == (1, 4)
    merged = module_ocr.merge_ocr_text(pages)
    assert "没有" not in merged and "插画" not in merged   # 自述一个字都没进原文
    assert "=== 第 1 页 ===" in merged                      # 有效页重新编号


def test_正文末尾附带说明不会整页丢掉():
    """真有正文的页面偶尔会在末尾附一句说明，只看开头判定，不该误伤。"""
    llm = _Vision(texts=[
        "第二章 石棺\n棺盖上刻着一行看不懂的楔形文字。\n\n（注：图片右下角有污渍，部分文字无法辨认）",
    ])
    pages = asyncio.run(module_ocr.ocr_images(_imgs(1), llm=llm))
    assert pages[0].startswith("第二章 石棺")


def test_抽图上限与单次请求上限是两回事(monkeypatch):
    """抽 40 张备用，但一次性视觉解析只带前 8 张。

    混同这两个数会把请求体撑到十几 MB——每张限长边 1600px 的 JPEG、base64 后 300~500KB，
    而 provider 的读超时是 120s。用户实测撞到的就是一个 message 为空的 httpx 超时。
    """
    seen = {}

    async def fake_images(imgs, rule_system, extra_text=""):
        seen["count"] = len(imgs)
        return {"title": "T", "scenes": [], "npcs": [], "clues": []}

    monkeypatch.setattr(modules_api.module_service, "parse_module_images", fake_images)
    monkeypatch.setattr(modules_api.module_service, "supplement_parse",
                        lambda raw, parsed, rs: asyncio.sleep(0, result=parsed))

    async def no_grounding(*a, **k):
        return {"index": -1, "matched": 0, "proposals": [], "detections": [], "pairs": []}

    monkeypatch.setattr(modules_api.module_map_vision, "locate_scenes_on_map", no_grounding)

    asyncio.run(modules_api._run_upload_job(modules_api._job_new(), "", _imgs(40), "coc"))
    assert seen["count"] == modules_api.MAX_IMAGES_PER_VISION_PARSE == 8
    assert modules_api.MAX_EMBEDDED_IMAGES == 40


def test_连接超时的报错要说得出是什么(monkeypatch):
    """httpx 超时类异常多数不带 message，str() 是空串——日志与 detail 都不能只剩一个冒号。"""
    import httpx

    async def boom(*a, **k):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(modules_api.module_service, "parse_module_text", boom)
    job = modules_api._job_new()
    asyncio.run(modules_api._run_upload_job(job, "正文", [], "coc"))

    detail = modules_api._upload_jobs[job]["detail"]
    assert "ReadTimeout" in detail          # 说得出异常类型
    assert "超时" in detail                  # 且给了可操作的方向
