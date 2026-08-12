"""图文模组解析的 A/B：直接喂图 vs 先 OCR 再走文本链路。

用法（后端不必启动，脚本自己建库会话；密钥与端点取设置页的「视觉模型」槽位）：

    cd server && .venv/bin/python scripts/ab_ocr_parse.py 某本图文模组.pdf
    cd server && .venv/bin/python scripts/ab_ocr_parse.py 扫描页1.jpg 扫描页2.jpg

**不写库**：两条链路都只跑到「解析出结构化 JSON」为止，产物存到 output/ab_ocr/ 供人工核对。
对比的是同一份文件、同一个模型，唯一变量是走哪条链路。

为什么这个对比值得做：图片链路是**一次性调用**（最多几张图 + 一句提示词直接产出 JSON），
而文本链路有断点续写和对照原文的查漏自检——后者对图文模组此前根本没机会生效，
因为 supplement_parse 拿不到原文，直接原样返回首轮结果。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.modules import OCR_MAX_PAGES, _extract_pdf_images, _read_pdf_text  # noqa: E402
from app.services import module_ocr, module_service  # noqa: E402

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _load(paths: list[Path]) -> tuple[str, list[tuple[bytes, str]]]:
    """把入参文件读成 (已有文字层, [(图片字节, mime)])，与上传端点同口径。"""
    text_parts: list[str] = []
    images: list[tuple[bytes, str]] = []
    for p in paths:
        data = p.read_bytes()
        if p.suffix.lower() in _IMG_EXT:
            images.append((data, "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"))
        elif p.suffix.lower() == ".pdf":
            if t := _read_pdf_text(data).strip():
                text_parts.append(t)
            images.extend(_extract_pdf_images(data, max_images=OCR_MAX_PAGES, keep_page_order=True))
        else:
            text_parts.append(data.decode("utf-8", "ignore"))
    return "\n\n".join(text_parts), images


def _shape(parsed: dict) -> dict:
    """结构化产物的可比指标。数量之外还看「有没有把该逐字照抄的东西抄回来」。"""
    scenes = parsed.get("scenes") or []
    npcs = parsed.get("npcs") or []
    return {
        "场景": len(scenes),
        "场景机制点": sum(len(s.get("events") or []) for s in scenes if isinstance(s, dict)),
        "NPC": len(npcs),
        "带属性的 NPC": sum(1 for n in npcs if isinstance(n, dict) and n.get("attributes")),
        "线索": len(parsed.get("clues") or []),
        "手书": len(parsed.get("handouts") or []),
        "结局": len(parsed.get("endings") or []),
        "幕后真相字数": len(str(parsed.get("truth") or "")),
        "手书正文总字数": sum(
            len(str(h.get("content") or "")) for h in (parsed.get("handouts") or [])
            if isinstance(h, dict)
        ),
    }


async def main(paths: list[Path]) -> int:
    raw_text, images = _load(paths)
    if not images:
        print("没有可用的图片——这个对比只对图文/扫描件模组有意义")
        return 2
    print(f"输入：{len(images)} 张图，已有文字层 {len(raw_text)} 字\n")
    out_dir = Path(__file__).resolve().parent.parent.parent / "output" / "ab_ocr"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}

    # A：现状——直接把图喂给视觉解析（一次性调用），再走一次查漏自检（对图片模组通常空转）
    t0 = time.monotonic()
    a = await module_service.parse_module_images(images, "coc", raw_text)
    a = await module_service.supplement_parse(raw_text, a, "coc")
    results["A_直接喂图"] = {"耗时秒": round(time.monotonic() - t0, 1), **_shape(a)}
    (out_dir / "A_直接喂图.json").write_text(json.dumps(a, ensure_ascii=False, indent=2), "utf-8")

    # B：OCR 前置——逐张认字 → 拼成原文 → 完全走文本链路（含断点续写与真正生效的查漏自检）
    t0 = time.monotonic()
    pages = await module_ocr.ocr_images(images)
    hit, total = module_ocr.ocr_coverage(pages)
    ocr_text = module_ocr.merge_ocr_text(pages, raw_text)
    print(f"OCR：{hit}/{total} 页识出文字，共 {len(ocr_text)} 字")
    (out_dir / "B_ocr原文.txt").write_text(ocr_text, "utf-8")
    b = await module_service.parse_module_text(ocr_text, "coc")
    b = await module_service.supplement_parse(ocr_text, b, "coc")
    results["B_OCR后走文本链路"] = {"耗时秒": round(time.monotonic() - t0, 1), **_shape(b)}
    (out_dir / "B_OCR后走文本链路.json").write_text(json.dumps(b, ensure_ascii=False, indent=2), "utf-8")

    keys = list(next(iter(results.values())).keys())
    width = max(len(k) for k in keys) + 2
    print(f"\n{'指标'.ljust(width)}{'A 直接喂图':>14}{'B OCR+文本':>14}")
    for k in keys:
        print(f"{k.ljust(width)}{str(results['A_直接喂图'][k]):>14}{str(results['B_OCR后走文本链路'][k]):>14}")
    print(f"\n产物已写入 {out_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main([Path(a) for a in sys.argv[1:]])))
