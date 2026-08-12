"""沙盘落位 A/B：文字猜位置 vs 照着模组自带的地图摆。

    cd server && .venv/bin/python scripts/ab_map_grounding.py <模组标题或id> <PDF / 图片 / 文件夹...>

素材可以混着给。真实模组的地图常常**不在 PDF 里**——鬼屋那本的城市地图就是配套图片包里的
一张独立 PNG，PDF 内嵌的反而是用数字编号房间的楼层平面图（编号对不上任何场景名）。
给文件夹时按体积降序取前几张候选，与从 PDF 抽图同一口径。

现状（A）：``enrich_module_map`` 只看文字，让 LLM 凭常识猜每个场景的 (q,r)。
实验（B）：先在 PDF 的图里找出本模组的地图，grounding 出每个地点的位置，换算成 axial 坐标。

**不写库**。两组坐标都打在终端，并画成 ASCII 版沙盘直接比对；grounding 的原始检出
写到 output/ab_map/ 供核对「是不是认错了地点」。

判断标准不是「谁更好看」，而是**和地图上的实际方位一致吗**：
打开那张地图，看西边的地点在沙盘上是不是也在西边。文字猜的那组经常整体镜像或旋转——
它没有任何依据，只是在按常识编。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.modules import _extract_pdf_images  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.module import Module  # noqa: E402
from app.services import hex_map, module_map_vision  # noqa: E402


_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _collect_images(sources: list[Path]) -> list[tuple[bytes, str, str]]:
    """把 PDF / 散图 / 文件夹统一收成 ``[(字节, mime, 出处)]``，按体积降序取前几张候选。

    地图未必在 PDF 里：鬼屋那本的城市地图是配套图片包里的独立 PNG，
    PDF 内嵌的反而是用数字编号房间的楼层平面图。
    """
    out: list[tuple[bytes, str, str]] = []
    for src in sources:
        paths = sorted(src.rglob("*")) if src.is_dir() else [src]
        for p in paths:
            if p.suffix.lower() == ".pdf":
                for i, (data, mime) in enumerate(
                    _extract_pdf_images(p.read_bytes(), max_images=module_map_vision.MAX_CANDIDATES), 1,
                ):
                    out.append((data, mime, f"{p.name} 内嵌图 {i}"))
            elif p.suffix.lower() in _IMG_EXT:
                mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                out.append((p.read_bytes(), mime, p.name))
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return out[:module_map_vision.MAX_CANDIDATES]


def _agreement(coords: dict[str, tuple[int, int]], truth: dict[str, tuple[float, float]]) -> tuple[int, int]:
    """方位一致率：对每一对场景，比较布局与地图上的东西 / 南北关系是否同向。

    这是本功能唯一站得住的判据——沙盘坐标只承诺方位与相对远近，不承诺比例尺，
    所以不比距离、只比「谁在谁的东边 / 北边」。地图本身就是标准答案。
    每对贡献 2 分（一个轴一分），并列（同一列或同一行）不计分也不扣分。
    """
    ids = sorted(set(coords) & set(truth))
    ok = total = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            (qa, ra), (qb, rb) = coords[a], coords[b]
            lx, ly = (qa + ra / 2) - (qb + rb / 2), ra - rb
            tx, ty = truth[a][0] - truth[b][0], truth[a][1] - truth[b][1]
            for lv, tv in ((lx, tx), (ly, ty)):
                if abs(tv) < 1e-6 or abs(lv) < 1e-6:
                    continue
                total += 1
                ok += (lv > 0) == (tv > 0)
    return ok, total


def _ascii_map(coords: dict[str, tuple[int, int]], names: dict[str, str]) -> str:
    """把 axial 坐标画成一张粗糙的俯视图：行=r，列按 q+r/2 偏移，看方位够用了。"""
    if not coords:
        return "（无坐标）"
    rows: dict[int, list[tuple[float, str]]] = {}
    for sid, (q, r) in coords.items():
        rows.setdefault(r, []).append((q + r / 2, names.get(sid, sid)[:6]))
    out = []
    for r in sorted(rows):
        cells = sorted(rows[r])
        line = "".join(f"{nm:<8}" if i == 0 else f"{'':<2}{nm:<8}" for i, (_, nm) in enumerate(cells))
        out.append(f"r={r:>3} | {line}")
    return "\n".join(out)


async def main(key: str, sources: list[Path]) -> int:
    db = SessionLocal()
    module = (
        db.get(Module, key)
        or db.query(Module).filter(Module.title == key).first()
    )
    if module is None:
        print(f"找不到模组：{key}")
        return 2
    scenes = [s for s in (module.scenes or []) if isinstance(s, dict) and s.get("kind") != "chapter"]
    names = {str(s["id"]): str(s.get("title") or s.get("name") or s["id"]) for s in scenes if s.get("id")}
    print(f"模组「{module.title}」：{len(scenes)} 个 location 场景\n")

    # A：现状——文字猜出来的那组坐标（已经落库，直接读）
    a_coords = {
        str(s["id"]): c for s in scenes
        if s.get("id") and (c := hex_map.scene_coord(s)) is not None
    }
    print("=== A 现状：文字猜的落位 ===")
    print(_ascii_map(a_coords, names))

    # B：实验——在给定素材里找地图，grounding 出位置
    images = _collect_images(sources)
    print(f"\n候选图 {len(images)} 张（按体积降序，地图通常是最大的那几张之一）")
    for i, (_, _, tag) in enumerate(images, 1):
        print(f"  {i}. {tag}")
    images = [(data, mime) for data, mime, _ in images]
    found = await module_map_vision.locate_scenes_on_map(images, scenes)
    if not found["proposals"]:
        print(
            "\n没能从图里找到本模组的地图。可能是：这本没有地图、地图不是内嵌位图（矢量绘制）、"
            f"或图上的地名与模组场景名对不上（门槛 {module_map_vision.MIN_MATCHED_LABELS} 个）。"
        )
        det = found.get("detections") or []
        if det:
            print(f"（最好的一张检出了 {len(det)} 个标注，但只对上 {found['matched']} 个场景）")
        return 3

    b_coords = {p["id"]: (p["q"], p["r"]) for p in found["proposals"]}
    print(f"\n=== B 实验：照第 {found['index'] + 1} 张图摆的落位（对上 {found['matched']} 个地点）===")
    print(_ascii_map(b_coords, names))

    out_dir = Path(__file__).resolve().parent.parent.parent / "output" / "ab_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "grounding原始检出.json").write_text(
        json.dumps(found["detections"], ensure_ascii=False, indent=2), "utf-8",
    )
    (out_dir / "地图图片.jpg").write_bytes(images[found["index"]][0])

    # 方位一致率：拿地图本身当标准答案，看两组落位各自还原对了多少
    truth = {sid: (x, y) for sid, x, y in module_map_vision.match_labels(found["detections"], scenes)}
    a_ok, a_all = _agreement(a_coords, truth)
    b_ok, b_all = _agreement(b_coords, truth)
    print("\n=== 方位一致率（以地图为准，每对场景比东西/南北两个轴）===")
    print(f"  A 文字猜的：{a_ok}/{a_all}" + (f"  {a_ok / a_all:.0%}" if a_all else ""))
    print(f"  B 照图摆的：{b_ok}/{b_all}" + (f"  {b_ok / b_all:.0%}" if b_all else ""))

    only_a = sorted(set(a_coords) - set(b_coords))
    print(f"\n地图上没认出、仍需文字兜底的场景：{[names[i] for i in only_a] or '无'}")
    print(f"原始检出与那张地图已写入 {out_dir}——请对着图核对方位是否一致。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1], [Path(a) for a in sys.argv[2:]])))
