"""模组上传文件的内容抽取：文字层 / 内嵌图 / Word / 纯文本。纯函数，只依赖 pypdf、Pillow、python-docx。

**为什么单独成模块、并在子进程里跑**：抽图要解码 PDF 里的每张内嵌图，而 Pillow 解码
JPEG2000 时全程不释放 GIL（实测「陵墓」一张 2485×1621 的图 1.37s，35 张 87s）。放在线程里
照样把事件循环一张张卡住，整个服务所有接口都跟着顿。子进程有自己的解释器与 GIL，主进程
不受影响。子进程用 spawn 方式启动、只 import 本模块，所以这里刻意不碰 app 的其余部分
（数据库、配置、路由……），保持轻量、无副作用。
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

logger = logging.getLogger(__name__)


class ExtractError(Exception):
    """文件无法抽取（编码不明、扫描件无文字、缺 .doc 转换器……），带给用户看的说明。

    要能跨进程 pickle，所以参数原样交给 Exception（不另存私有状态）。
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail)
        self.status_code = status_code
        self.detail = detail


def _decode_text(content: bytes) -> str:
    """容错解码文本：UTF-8(含BOM) → GB18030(覆盖 GBK/GB2312) → Big5 → UTF-16 依次尝试。

    中文 txt 常是 GBK 编码（非 UTF-8），直接 utf-8 解码会抛 UnicodeDecodeError。
    全部失败（多半是二进制文件）时抛友好错误而非 500。
    """
    for enc in ("utf-8-sig", "gb18030", "big5", "utf-16"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ExtractError(422, "无法识别文件编码（可能是二进制文件）。请上传 PDF / Word(.docx) / 纯文本(UTF-8 或 GBK)")


def _read_pdf_text(content: bytes) -> str:
    """提取 PDF 文字层（扫描件可能为空）。"""
    import io

    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


#: JPEG2000 降档解码后最短也要保住的长边像素（降一档后低于它就不降）
_JP2_MIN_SIDE = 1000


def _normalize_image(pil, data: bytes) -> tuple[bytes, str] | None:
    """把 PDF 里抽出的图规整成「视觉接口一定能解码」的 RGB JPEG（限尺寸），失败返回 None。

    pypdf 的 img.data 对某些图（CMYK/索引色/特殊滤镜/掩膜）并非合法的独立 PNG/JPEG，
    直接当 image/png 发出去会被接口 400 拒收。统一经 Pillow 重新编码可规避，并顺带压体积。
    """
    import io

    from PIL import Image

    im = pil
    if im is None:
        try:
            im = Image.open(io.BytesIO(data))
        except Exception:
            return None
    if getattr(im, "format", None) == "JPEG2000" and max(im.size) >= 2 * _JP2_MIN_SIDE:
        # JPEG2000 可以只解到低一档分辨率：解码量约降到 1/4（实测 1.4s → 0.46s），而结果
        # 本来就要缩到 1600 以内。只降一档——有的文件分辨率层级不够，降两档直接解码失败。
        im.reduce = 1
        try:
            im.load()
        except Exception:
            try:
                im = Image.open(io.BytesIO(data))   # 降档失败：退回原尺寸重新解
            except Exception:
                return None
    try:
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((1600, 1600))  # 限制最长边，避免过大 payload
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return None


def _select_pdf_images(
    reader, max_images: int = 8, min_bytes: int = 3000, keep_page_order: bool = False,
) -> list[tuple[bytes, str]]:
    """从 PdfReader 抽取内嵌位图并规整为合法 JPEG。

    两种取法，对应两种用途：
    - 默认（喂视觉解析）：**按体积降序**取前 N 张。地图通常是页面里最大的那张图，
      而那次调用只塞得下几张，得优先挑信息量大的。
    - ``keep_page_order``（喂 OCR 前置）：**保持页序**。扫描件每页就是一整张图，
      按体积挑等于随机丢页、还把顺序打乱，OCR 出来的原文会前言不搭后语。

    每张经 Pillow 重新编码（统一 RGB JPEG、限尺寸），无法解码的直接跳过——
    避免把畸形图发给视觉接口触发 400。
    """
    candidates: list[tuple[object, bytes, int]] = []
    for page in reader.pages:
        for img in getattr(page, "images", None) or []:
            data = getattr(img, "data", b"") or b""
            if len(data) < min_bytes:
                continue
            try:
                pil = img.image  # pypdf 已解码的 PIL Image（访问即解码，可能抛错）
            except Exception:
                pil = None
            candidates.append((pil, data, len(data)))
    if not keep_page_order:
        candidates.sort(key=lambda t: t[2], reverse=True)
    out: list[tuple[bytes, str]] = []
    for pil, data, _ in candidates:
        norm = _normalize_image(pil, data)
        if norm:
            out.append(norm)
        if len(out) >= max_images:
            break
    if len(candidates) > len(out):
        # 不做无声截断：读日志的人得知道这本书还有多少页没进解析。
        logger.warning(
            "PDF 内嵌图 %s 张，本次只取 %s 张（上限 %s）", len(candidates), len(out), max_images,
        )
    return out


#: 从 PDF 抽内嵌图的上限。抽出来是**给多个消费者用的**：结构化解析、地图定位、OCR，
#: 各自有各自的取用量（见下）。抽得多本身不花钱，只是多解码几张图。
MAX_EMBEDDED_IMAGES = 40

#: 一次性视觉解析**单个请求**里最多带几张图。这个不能跟着上限走：parse_module_images
#: 是把图全部塞进一次调用，每张限长边 1600px 的 JPEG、base64 后 300~500KB——
#: 40 张就是十几 MB 的请求体，在 provider 的 120s 超时下几乎必然读超时
#: （实测报的就是一个 message 为空的 httpx 超时）。按体积降序取前几张最有信息量的。
MAX_IMAGES_PER_VISION_PARSE = 8

#: OCR 前置的取图上限。扫描件一页一张图，8 张连一章都盖不住；OCR 是逐张独立调用、
#: 有并发闸，页数多只是慢一点，不像视觉解析那样受单次请求体限制。
OCR_MAX_PAGES = 60


def _extract_pdf_images(
    content: bytes, max_images: int = 8, keep_page_order: bool = False,
) -> list[tuple[bytes, str]]:
    import io

    from pypdf import PdfReader
    return _select_pdf_images(
        PdfReader(io.BytesIO(content)), max_images=max_images, keep_page_order=keep_page_order,
    )


def _convert_doc_to_text(content: bytes) -> str | None:
    """把旧版二进制 Word(.doc) 转成纯文本。

    .doc 是 OLE 复合格式，python-docx 读不了；这里复用系统现成转换器（不引入重依赖）：
    优先 macOS 自带 textutil，其次 LibreOffice(soffice/libreoffice)。都没有则返回 None。
    """
    import os
    import shutil
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.doc")
        with open(src, "wb") as f:
            f.write(content)

        # macOS：textutil 直接转纯文本到 stdout
        if shutil.which("textutil"):
            try:
                out = subprocess.run(
                    ["textutil", "-convert", "txt", "-stdout", src],
                    capture_output=True, timeout=60,
                )
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.decode("utf-8", errors="replace")
            except (subprocess.SubprocessError, OSError):
                pass

        # 跨平台：LibreOffice headless 转 txt 文件再读
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                subprocess.run(
                    [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", tmp, src],
                    capture_output=True, timeout=120,
                )
                txt = os.path.join(tmp, "in.txt")
                if os.path.exists(txt):
                    with open(txt, "rb") as f:
                        data = f.read()
                    if data.strip():
                        return _decode_text(data)
            except (subprocess.SubprocessError, OSError, ExtractError):
                pass

    return None


def _extract_doc_text(content: bytes, filename: str) -> str:
    """docx / 旧版 doc / 纯文本（含 GBK 等）解码。PDF 由调用方单独处理。"""
    fn = filename.lower()
    if fn.endswith(".docx"):
        import io

        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if fn.endswith(".doc"):
        text = _convert_doc_to_text(content)
        if text and text.strip():
            return text
        raise ExtractError(
            422,
            f"「{filename}」是旧版 Word(.doc)，本机未找到可用的转换器（macOS 自带 textutil "
            "或安装 LibreOffice）。请另存为 .docx / PDF 后上传，或安装 LibreOffice 后重试",
        )
    try:
        return _decode_text(content)
    except ExtractError as e:
        raise ExtractError(e.status_code, f"「{filename}」{e.detail}")


def _extract_uploads(
    uploads: list[tuple[str, str, bytes]], vision: bool, ocr_prepass: bool,
) -> tuple[list[str], list[tuple[bytes, str]]]:
    """把上传的文件抽成文字段落与图片（同步、CPU 密集，调用方放线程里跑）。"""
    _IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    multi = len(uploads) > 1
    parts: list[str] = []
    images: list[tuple[bytes, str]] = []   # (bytes, mime) —— 图片走多模态视觉解析
    for filename, ct, content in uploads:
        fn = filename.lower()
        if ct.startswith("image/") or fn.endswith(_IMG_EXT):
            images.append((content, ct or "image/png"))
        elif fn.endswith(".pdf") or ct == "application/pdf":
            text = _read_pdf_text(content)
            # OCR 前置要页序、要全本；否则按体积降序抽到上限，供解析/地图定位各取所需。
            pdf_imgs = _extract_pdf_images(
                content,
                max_images=OCR_MAX_PAGES if ocr_prepass else MAX_EMBEDDED_IMAGES,
                keep_page_order=ocr_prepass,
            ) if vision else []
            if text.strip():
                parts.append(f"=== 文件：{filename} ===\n{text}" if multi else text)
            images.extend(pdf_imgs)
            if not text.strip() and not pdf_imgs:
                raise ExtractError(
                    422,
                    f"「{filename}」似乎是扫描件（无可提取文字）。请改用含文字层的 PDF，"
                    "或到设置页把一个支持视觉的配置标记为「视觉模型」后重试（届时会直接识图，"
                    "带团用的主模型不必更换）",
                )
        else:
            text = _extract_doc_text(content, filename)
            parts.append(f"=== 文件：{filename} ===\n{text}" if multi else text)
    return parts, images


# ── 子进程执行 ─────────────────────────────────────────────────────────────

_pool: ProcessPoolExecutor | None = None


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        # 单 worker：上传是低频操作，串行足够；spawn 与平台无关、也是打包后唯一安全的方式
        _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _pool


def shutdown() -> None:
    """关停抽取子进程池（服务退出时调用）；之后再抽取会按需重建。"""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def _needs_process(uploads: list[tuple[str, str, bytes]]) -> bool:
    """只有 PDF 值得开子进程（要解码内嵌图）；txt/docx 很快，线程里跑即可，省掉进程启动开销。"""
    return any(
        fn.lower().endswith(".pdf") or ct == "application/pdf" for fn, ct, _ in uploads
    )


async def run_extract(
    uploads: list[tuple[str, str, bytes]], vision: bool, ocr_prepass: bool,
) -> tuple[list[str], list[tuple[bytes, str]]]:
    """异步抽取上传文件：PDF 进子进程，其余进线程。子进程不可用（池崩溃等）时回落线程。"""
    global _pool
    if not _needs_process(uploads):
        return await asyncio.to_thread(_extract_uploads, uploads, vision, ocr_prepass)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _get_pool(), _extract_uploads, uploads, vision, ocr_prepass,
        )
    except BrokenProcessPool:
        logger.warning("抽取子进程不可用，回落到线程（期间服务可能短暂卡顿）", exc_info=True)
        _pool = None
        return await asyncio.to_thread(_extract_uploads, uploads, vision, ocr_prepass)
