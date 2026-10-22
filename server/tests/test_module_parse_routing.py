"""模组解析分流：文字层充足走文本解析、扫描件走视觉解析、视觉解析截断回落 OCR + 文本解析。

全程打桩，不调用任何真实模型。
"""

import asyncio
import json

import pytest

from app.api import modules as modules_api
from app.services import module_extract

_PARSED = {"title": "T", "scenes": [{"id": "s1", "name": "灯塔", "kind": "location"}],
           "npcs": [], "clues": []}


def _imgs(n, prefix="pdf"):
    # 字节长度递增，便于断言「按体积重排」
    return [(f"{prefix}{i}".encode() * (i + 1), "image/jpeg") for i in range(n)]


def _page(n: int) -> str:
    return f"第{n}页 雨鲸\n灯塔福利院的走廊尽头传来潮水声，铁门上锈迹斑斑，调查员们停下脚步。"


@pytest.fixture
def spy(monkeypatch):
    """打桩解析链路上所有会碰模型的环节，并记录调用。"""
    seen: dict = {"stages": [], "ocr_calls": []}

    async def fake_text(raw_text, rule_system, on_progress=None):
        seen["text_raw"] = raw_text
        if seen.get("text_raises"):
            raise json.JSONDecodeError("truncated", "{", 1)
        return json.loads(json.dumps(_PARSED))

    async def fake_images(images, rule_system, extra_text=""):
        seen["vision_count"] = len(images)
        if seen.get("vision_raises"):
            raise json.JSONDecodeError("truncated", "{", 1)
        return json.loads(json.dumps(_PARSED))

    async def fake_ocr(images, llm=None):
        seen["ocr_calls"].append(list(images))
        return list(seen.get("ocr_pages") or [_page(i + 1) for i in range(len(images))])

    async def fake_grounding(images, scenes, *a, **k):
        seen["map_images"] = list(images)
        return {"index": -1, "matched": 0, "proposals": [], "detections": []}

    async def passthrough(parsed, rule_system):
        return parsed

    async def none(*a, **k):
        return None

    ms = modules_api.module_service
    monkeypatch.setattr(ms, "parse_module_text", fake_text)
    monkeypatch.setattr(ms, "parse_module_images", fake_images)
    monkeypatch.setattr(ms, "supplement_parse",
                        lambda raw, parsed, rs: asyncio.sleep(0, result=parsed))
    monkeypatch.setattr(ms, "redact_player_facing", passthrough)
    monkeypatch.setattr(ms, "generate_character_guidance", none)
    monkeypatch.setattr(modules_api.module_map_service, "generate_map_backdrop", none)
    monkeypatch.setattr(modules_api.module_ocr, "ocr_images", fake_ocr)
    monkeypatch.setattr(modules_api.module_map_vision, "locate_scenes_on_map", fake_grounding)
    monkeypatch.setattr(modules_api, "_rag_index_bg", lambda module_id: None)

    real_update = modules_api._job_update

    def record(job_id, **fields):
        if fields.get("stage"):
            seen["stages"].append(fields["stage"])
        real_update(job_id, **fields)

    monkeypatch.setattr(modules_api, "_job_update", record)
    return seen


def _run(raw_text, images, **kw):
    job = modules_api._job_new()
    asyncio.run(modules_api._run_upload_job(job, raw_text, images, "coc", **kw))
    return modules_api._upload_jobs[job]


# ── 阈值 ──────────────────────────────────────────────────────────────────

def test_文字层阈值():
    # 雨鲸实测：25 页 36356 字
    assert module_extract.is_text_rich(36356, 25)
    # 文字最稀的真实模组（鬼屋）：每页 668 字
    assert module_extract.is_text_rich(20069, 30)
    # 扫描件：文字层接近 0
    assert not module_extract.is_text_rich(0, 40)
    # 每页只有页眉/页码的扫描件：总数过线，但每页均值不够
    assert not module_extract.is_text_rich(100 * 40, 100)
    # 太短的文字层（封面一行字）不算充足
    assert not module_extract.is_text_rich(800, 2)
    # docx / txt 没有页的概念，只看总字数
    assert module_extract.is_text_rich(5000)


def test_抽取阶段按文字层决定抽图方式(monkeypatch):
    calls = []

    def fake_read(content):
        return (("正文" * 2000), 10) if content == b"rich" else ("", 30)

    def fake_imgs(content, max_images=8, keep_page_order=False):
        calls.append((content, max_images, keep_page_order))
        return [(b"img", "image/jpeg")]

    monkeypatch.setattr(module_extract, "_read_pdf", fake_read)
    monkeypatch.setattr(module_extract, "_extract_pdf_images", fake_imgs)

    ex = module_extract._extract_uploads(
        [("雨鲸.pdf", "application/pdf", b"rich"), ("日记.png", "image/png", b"diary")],
        vision=True, ocr_prepass=False,
    )
    assert ex.text_rich is True
    assert ex.attached == [(b"diary", "image/png")]        # 单独上传的图分开放
    assert ex.images == [(b"img", "image/jpeg")]
    # 文字充足：图只供地图定位，按体积挑
    assert calls[-1] == (b"rich", module_extract.MAX_EMBEDDED_IMAGES, False)

    ex = module_extract._extract_uploads(
        [("扫描件.pdf", "application/pdf", b"scan")], vision=True, ocr_prepass=False,
    )
    assert ex.text_rich is False
    # 扫描件：按页序、取到 OCR 上限，截断回落时逐页 OCR 才不会乱序
    assert calls[-1] == (b"scan", module_extract.OCR_MAX_PAGES, True)

    # 一份文字充足 + 一份扫描件：整体不算充足（扫描件的内容只能靠看图）
    ex = module_extract._extract_uploads(
        [("a.pdf", "application/pdf", b"rich"), ("b.pdf", "application/pdf", b"scan")],
        vision=True, ocr_prepass=False,
    )
    assert ex.text_rich is False


# ── 上传任务分流 ───────────────────────────────────────────────────────────

def test_文字充足走文本解析_图片仍用于地图定位(spy):
    pdf_imgs = _imgs(3)
    attached = [(b"diary-png", "image/png")]
    spy["ocr_pages"] = [_page(1)]
    job = _run("雨鲸正文" * 500, pdf_imgs, attached=attached, text_rich=True)

    assert job["status"] == "done", job
    assert "vision_count" not in spy                      # 视觉解析没被调用
    # 只 OCR 单独上传的图，PDF 内嵌插画不进解析
    assert spy["ocr_calls"] == [attached]
    assert "=== 附图 1 ===" in spy["text_raw"] and _page(1) in spy["text_raw"]
    assert spy["text_raw"].startswith("雨鲸正文")
    # 地图定位：单独上传的图在前，PDF 图按体积降序
    assert spy["map_images"] == attached + sorted(pdf_imgs, key=lambda im: len(im[0]), reverse=True)
    assert any("文字解析" in s for s in spy["stages"])


def test_附图OCR失败不影响文本解析(spy):
    spy["ocr_pages"] = [""]
    job = _run("正文" * 1000, _imgs(2), attached=[(b"x", "image/png")], text_rich=True)
    assert job["status"] == "done"
    assert "附图" not in spy["text_raw"]


def test_扫描件走视觉解析(spy):
    job = _run("", _imgs(12), text_rich=False)
    assert job["status"] == "done", job
    assert spy["vision_count"] == modules_api.MAX_IMAGES_PER_VISION_PARSE
    assert spy["ocr_calls"] == []
    assert "text_raw" not in spy
    assert any("图片解析" in s for s in spy["stages"])


def test_旧调用方不传text_rich时按原文字数判定(spy):
    _run("短", _imgs(2))
    assert "vision_count" in spy and "text_raw" not in spy


def test_视觉解析截断回落OCR加文本解析(spy):
    spy["vision_raises"] = True
    imgs = _imgs(10)
    job = _run("", imgs, text_rich=False)

    assert job["status"] == "done", job
    assert spy["ocr_calls"] == [imgs]                     # 全部图（不止前 8 张）逐张 OCR
    assert _page(1) in spy["text_raw"] and _page(10) in spy["text_raw"]
    assert any("正在回落" in s for s in spy["stages"])


def test_回落OCR一张没认出_给出可读原因(spy):
    spy["vision_raises"] = True
    spy["ocr_pages"] = ["", "", ""]
    job = _run("", _imgs(3), text_rich=False)

    assert job["status"] == "failed"
    d = job["detail"]
    assert "图片解析输出过长被截断" in d and "OCR 回落" in d and "视觉模型" in d
    assert "text_raw" not in spy                          # 没文字可解析，不白跑文本解析
    assert "换更稳定的模型" not in d


def test_回落后文本解析仍截断_说明走过的路(spy):
    spy["vision_raises"] = True
    spy["text_raises"] = True
    job = _run("", _imgs(2), text_rich=False)

    assert job["status"] == "failed"
    d = job["detail"]
    assert "图片解析输出过长被截断" in d and "已回落为 OCR（2/2 张图识出文字）" in d
    assert "分批导入" in d


def test_文本解析截断_说明是文字解析(spy):
    spy["text_raises"] = True
    job = _run("正文" * 1000, [], text_rich=True)
    assert job["status"] == "failed"
    assert job["detail"].startswith("文字解析") and "截断" in job["detail"]
