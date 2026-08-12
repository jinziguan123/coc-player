"""从模组自带的地图/平面图上定位场景，产出沙盘坐标提议。

**这解决的是什么。** 现在的沙盘落位（``module_map_service.enrich_module_map``）只看**文字**：
把场景描述喂给 LLM，让它凭「码头应该靠海、山道应该在山上」这类常识猜一组 (q,r)。而很多模组
本身就画了地图——那张图上白纸黑字标着每个地点在哪、谁挨着谁。那份现成的几何信息此前被完整
丢掉了，沙盘等于重新发明了一遍作者已经画好的东西。

**分工。** grounding 只管**位置**，地貌与连通仍归文字那条链路——图上看不出「这是沼泽还是原野」，
也看不出「这两个门是不是通的」。两者产出同一种提议形状 ``{id, q, r}``，一起交给既有的确定性
修复器（``hex_map.ensure_scene_maps``）收口，「只补洞、不推翻」的原则不变。

**与 Qwen-MM-Plugins 的关系。** 提示词与解析逻辑取自该仓库 ``api`` 能力的 grounding 工具
（``vl/grounding.py``）：要求模型输出 ``[{"label": "...", "bbox_2d": [x1,y1,x2,y2]}]``、
坐标归一化到 0-1000，并保留它那个 ``<ref>/<box>`` 的兜底解析。同样不引它的 MCP/uvx——
那只是一次 OpenAI 兼容的 vision 调用，本项目的 Provider 抽象已经能做。
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re

from app.services import hex_map

logger = logging.getLogger(__name__)

#: 归一化坐标的值域，与插件一致（模型被要求把 bbox 缩放到 0-1000）。
NORM_RANGE = 1000

#: 判定「这张图是不是本模组的地图」的门槛：至少认出这么多个能对上场景名的标注。
#: 一两个匹配可能只是插画里恰好写了个地名，成不了一张地图。
MIN_MATCHED_LABELS = 3

#: 最多试几张候选图。模组 PDF 常有几十张图，绝大多数是插画；地图通常是最大的那几张之一
#: （``_select_pdf_images`` 默认就按体积降序），试前几张即可，不必逐张烧钱。
MAX_CANDIDATES = 5


def build_prompt(hint: str = "地图上标注的所有地点") -> str:
    """grounding 提示词。格式与归一化区间沿用插件，只把检测目标换成模组地点。"""
    return (
        f"这是一张 TRPG 模组的地图。请检测并定位图中{hint}——"
        "每一处有名字的建筑、房间、区域或地标都要框出来，标签用图上写的那个名字。"
        "以JSON数组格式输出，每个对象一个元素：\n"
        '[{"label": "名称", "bbox_2d": [x1, y1, x2, y2]}]\n'
        f"bbox_2d为归一化坐标(0-{NORM_RANGE})，表示左上角和右下角。仅输出JSON。"
        "如果这张图不是地图、或图上没有任何带名字的地点，输出空数组 []。"
    )


def parse_grounding(text: str) -> list[dict]:
    """解析模型产出：先按 JSON，失败再退回 ``<ref>标签</ref><box>(x1,y1),(x2,y2)</box>``。

    两种格式都要认是插件那边踩出来的经验——同一个模型在不同轮次会换着输出。
    """
    out = _parse_json(text)
    return out if out else _parse_ref_box(text)


def _parse_json(text: str) -> list[dict]:
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    raw = (fence.group(1) if fence else text).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("detections") or data.get("objects") or data.get("results") or []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("object") or "").strip()
        bbox = item.get("bbox_2d") or item.get("bbox") or item.get("box") or item.get("bounding_box")
        if not label or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            out.append({"label": label, "bbox": [float(v) for v in bbox]})
        except (TypeError, ValueError):
            continue
    return out


_REF_RE = re.compile(r"<ref>(.*?)</ref>")
_BOX_RE = re.compile(r"<box>\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*,\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)</box>")


def _parse_ref_box(text: str) -> list[dict]:
    out: list[dict] = []
    refs = list(_REF_RE.finditer(text))
    for i, ref in enumerate(refs):
        end = refs[i + 1].start() if i + 1 < len(refs) else len(text)
        for box in _BOX_RE.finditer(text[ref.end():end]):
            out.append({
                "label": ref.group(1).strip(),
                "bbox": [float(box.group(j)) for j in range(1, 5)],
            })
    return out


_PUNCT = re.compile(r"[\s　·・\-—_()（）\[\]【】<>《》\"'“”‘’,，.。:：;；!！?？/\\|]+")


def _norm_name(s: str) -> str:
    return _PUNCT.sub("", str(s or "")).lower()


def match_labels(detections: list[dict], scenes: list[dict]) -> list[tuple[str, float, float]]:
    """把检测标签对到场景 id，返回 ``[(scene_id, cx, cy)]``（bbox 中心，归一化坐标）。

    匹配三档，逐档放宽：完全同名 → 场景名包含标签 → 标签包含场景名。地图上的标注往往是
    「祠堂」而模组里叫「村中祠堂」，反过来也有；但不做编辑距离之类的模糊匹配——
    连错一个地点，整张沙盘的方位就是错的，宁可少认。一个场景只取第一次命中。
    """
    by_norm: list[tuple[str, str]] = []
    for s in scenes:
        if not isinstance(s, dict) or s.get("kind") == "chapter" or not s.get("id"):
            continue
        name = str(s.get("title") or s.get("name") or "").strip()
        if name:
            by_norm.append((str(s["id"]), _norm_name(name)))

    used: set[str] = set()
    out: list[tuple[str, float, float]] = []
    for det in detections:
        label = _norm_name(det.get("label"))
        if not label:
            continue
        hit = next(
            (sid for sid, nm in by_norm if sid not in used and nm == label),
            None,
        ) or next(
            (sid for sid, nm in by_norm if sid not in used and (label in nm or nm in label)),
            None,
        )
        if not hit:
            continue
        x1, y1, x2, y2 = det["bbox"]
        used.add(hit)
        out.append((hit, (x1 + x2) / 2, (y1 + y2) / 2))
    return out


def detections_to_axial(points: list[tuple[str, float, float]]) -> list[dict]:
    """把图上的像素中心换算成 axial 坐标提议 ``[{"id","q","r"}]``。

    图像的 y 轴向下、沙盘的 r 轴也向下（南），所以不用翻转。换算是 ``hexXY`` 的逆：
    ``r = y / (1.5·s)``、``q = x / (√3·s) − r/2``，``s`` 是「一格对应多少图上单位」。

    ``s`` 不能拍死：地图画得紧凑还是松散因本而异，取小了所有地点挤成一团、取大了满屏空地。
    所以从密到疏试一串 ``s``，取**第一个满足落位规则**（互不重叠、场景间至少隔一格）的那个——
    既保住了地图上的相对方位，又直接产出合法坐标，后面的确定性修复器几乎不用动它。
    全都不满足就返回最疏的那档，剩下的冲突交给修复器按「只补洞」的老规矩收拾。
    """
    if len(points) < 2:
        return [{"id": sid, "q": 0, "r": 0} for sid, _, _ in points[:1]]

    cx = sum(p[1] for p in points) / len(points)
    cy = sum(p[2] for p in points) / len(points)
    span = max(
        max(p[1] for p in points) - min(p[1] for p in points),
        max(p[2] for p in points) - min(p[2] for p in points),
    ) or 1.0

    best: list[dict] = []
    # s 越小越疏。从「整张图约 6 格宽」一路试到约 40 格宽。
    for hexes_across in range(6, 41, 2):
        s = span / hexes_across
        coords: dict[str, tuple[int, int]] = {}
        for sid, x, y in points:
            rf = (y - cy) / (1.5 * s)
            qf = (x - cx) / (math.sqrt(3) * s) - rf / 2
            coords[sid] = hex_map._axial_round(qf, rf)
        best = [{"id": sid, "q": q, "r": r} for sid, (q, r) in coords.items()]
        vals = list(coords.values())
        if all(
            hex_map.axial_distance(a, b) >= 2
            for i, a in enumerate(vals) for b in vals[i + 1:]
        ):
            return best
    logger.info("地图落位：%s 个地点在最疏的一档仍有拥挤，交给修复器收口", len(points))
    return best


async def locate_scenes_on_map(
    images: list[tuple[bytes, str]], scenes: list[dict], llm=None,
) -> dict:
    """在候选图里找出本模组的地图，返回 ``{"index", "matched", "proposals", "detections"}``。

    没找到（不是地图 / 认出的地点对不上）时 ``proposals`` 为空列表，调用方照常走文字落位。
    全程 fail-open：任何一张图的调用失败只跳过那一张。
    """
    if llm is None:
        from app.ai.llm_factory import get_vision_llm

        llm = get_vision_llm()
    empty = {"index": -1, "matched": 0, "proposals": [], "detections": []}
    if not images or not llm.supports_vision():
        return empty

    prompt = build_prompt()
    best = empty
    for i, (data, mime) in enumerate(images[:MAX_CANDIDATES]):
        try:
            raw = await llm.complete_vision(prompt, [(base64.b64encode(data).decode(), mime)])
        except Exception:
            logger.exception("第 %s 张候选图 grounding 失败（跳过）", i + 1)
            continue
        detections = parse_grounding(raw or "")
        matched = match_labels(detections, scenes)
        logger.info(
            "第 %s 张候选图：检出 %s 个标注，对上 %s 个场景", i + 1, len(detections), len(matched),
        )
        if len(matched) > best["matched"]:
            best = {
                "index": i,
                "matched": len(matched),
                "proposals": detections_to_axial(matched),
                "detections": detections,
            }
    if best["matched"] < MIN_MATCHED_LABELS:
        # 一两个匹配可能只是插画里恰好写了个地名，据此摆整张沙盘还不如按文字猜。
        return empty
    return best
