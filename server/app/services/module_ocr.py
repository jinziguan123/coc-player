"""图文模组的 OCR 前置：先把扫描页/插图转成文字，再交给文本解析链路。

**为什么值得单开一步。** 现在图文模组走的是 ``parse_module_images``：把最多 8 张图连同
一句提示词塞进**一次** vision 调用，让模型一边认字一边输出结构化 JSON。而纯文本模组走的
``parse_module_text`` 有两样图片路径没有的东西——被截断时的断点续写、以及对照原文的查漏
自检（``supplement_parse``）。同一个本子，走文本链路拿到的结构明显更全。

把「认字」和「理解结构」拆成两步，图文模组就能复用那条更强的链路：
每张图各自 OCR（互不干扰、失败只丢一张），拼成原文，后面完全走文本那套。

**与 Qwen-MM-Plugins 的关系。** 那个仓库 ``api`` 能力里的 ocr 工具，实现就是一次
OpenAI 兼容的 chat 调用：图片 + 一句固定中文提示词，base_url 默认 DashScope 的
compatible-mode，模型默认 qwen3.7-plus。没有专用的 OCR 端点，也没有别的黑魔法。
所以这里不引它的 MCP/uvx 那一套，直接复用本项目已有的 Provider 抽象——提示词沿用它那句
（见 OCR_PROMPT），密钥与端点走设置页的「视觉模型」槽位，与本项目「AI 密钥的唯一真源是
设置页」的约定一致。想换成本机部署的视觉模型也只是改一下那个槽位。
"""

from __future__ import annotations

import asyncio
import base64
import logging

logger = logging.getLogger(__name__)

#: 逐字沿用 Qwen-MM-Plugins ``api`` 能力里 ocr 工具的提示词，便于把差异归因到模型而非措辞。
OCR_PROMPT = "请对这张图片进行OCR文字识别，提取图片中所有可见的文字内容，保持原始排版格式。"

#: 并发上限。本项目此前没有任何生图/视觉调用的并发闸，一次 8~20 张图全量并发发出去，
#: 轻则被服务端限流（429 全灭），重则把本机部署的推理服务打满。3 是「快到有感、又不至于
#: 触发限流」的常见档位；真限流了失败的那几张会各自 fail-open，不影响整本解析。
MAX_CONCURRENCY = 3

#: 单张图的超时。扫描页可能很密，给足；但不能让一张坏图把整个上传任务钉死。
PER_IMAGE_TIMEOUT_S = 180


async def ocr_images(images: list[tuple[bytes, str]], llm=None) -> list[str]:
    """并发把每张图 OCR 成文字，按入参顺序返回；失败的那张返回空串（fail-open）。

    ``llm`` 缺省取「视觉模型」槽位。不支持看图的模型直接返回全空——调用方据此回落到
    原来的图片解析路径，不会把一本图文模组解析成空壳。

    项目硬约定：一切 LLM 调用不设 max_tokens（插件那边写死 4096，这里不跟）。
    """
    if not images:
        return []
    if llm is None:
        from app.ai.llm_factory import get_vision_llm

        llm = get_vision_llm()
    if not llm.supports_vision():
        logger.warning("OCR 前置跳过：当前没有可看图的模型（设置页可标记「视觉模型」）")
        return ["" for _ in images]

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(index: int, payload: tuple[bytes, str]) -> str:
        data, mime = payload
        async with sem:
            try:
                raw = await asyncio.wait_for(
                    llm.complete_vision(
                        OCR_PROMPT,
                        [(base64.b64encode(data).decode(), mime)],
                    ),
                    timeout=PER_IMAGE_TIMEOUT_S,
                )
            except Exception:
                logger.exception("第 %s 张图 OCR 失败（跳过该张）", index + 1)
                return ""
        return (raw or "").strip() if isinstance(raw, str) else ""

    return list(await asyncio.gather(*(one(i, im) for i, im in enumerate(images))))


def merge_ocr_text(pages: list[str], extra_text: str = "") -> str:
    """把逐页 OCR 结果与已有文字层拼成一份原文。

    分页标记保留下来是有用的：模组解析提示词里要求「手书正文逐字照抄原文」，
    页边界能帮模型判断一段文字到哪里结束，也方便人工核对是哪一页认错了。
    """
    parts = [t for t in (p.strip() for p in pages) if t]
    body = "\n\n".join(f"=== 第 {i} 页 ===\n{t}" for i, t in enumerate(parts, start=1))
    extra = (extra_text or "").strip()
    if extra and body:
        return f"{extra}\n\n{body}"
    return body or extra


def ocr_coverage(pages: list[str]) -> tuple[int, int]:
    """(认出文字的页数, 总页数)——供上传进度与 A/B 对比展示，判断 OCR 是不是白跑了。"""
    return sum(1 for p in pages if p.strip()), len(pages)
