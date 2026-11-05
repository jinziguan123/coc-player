"""批量配图生成的条目收集与进度测试。"""

import asyncio

from app.models.module import Module
from app.services import module_image_service


def test_generate_all_images_collects_locations_npcs_clues(monkeypatch):
    module = Module(
        title="配图模组", rule_system="coc",
        scenes=[
            {"id": "s1", "kind": "location"},
            {"id": "s2", "kind": "event"},
            {"id": "s3", "kind": "chapter"},
            {"id": "s4"},
        ],
        npcs=[{"id": "n1", "name": "管家"}],
        clues=[{"id": "c1", "name": "信"}],
    )
    calls: list[tuple] = []

    async def fake(db, module, kind, item_id, field=None, **kwargs):
        calls.append((kind, item_id, field))
        return "/api/images/x.jpg"

    monkeypatch.setattr(module_image_service, "regenerate_module_image", fake)
    progress: list[tuple] = []
    result = asyncio.run(module_image_service.generate_all_images(
        None, module, on_progress=lambda *args: progress.append(args),
    ))

    assert result == {"done": 4, "total": 4, "created": 4}
    assert calls == [
        ("scene", "s1", None),
        ("scene", "s4", None),
        ("npc", "n1", "portrait"),
        ("clue", "c1", None),
    ]
    assert progress[-1][0] == 4 and progress[-1][1] == 4


def test_generate_all_images_continues_after_single_failure(monkeypatch):
    module = Module(
        title="配图模组", rule_system="coc",
        scenes=[{"id": "s1"}, {"id": "s2"}], npcs=[], clues=[],
    )
    calls: list[str] = []

    async def flaky(db, module, kind, item_id, field=None, **kwargs):
        calls.append(item_id)
        if item_id == "s1":
            raise RuntimeError("boom")
        return "/api/images/y.jpg"

    monkeypatch.setattr(module_image_service, "regenerate_module_image", flaky)
    result = asyncio.run(module_image_service.generate_all_images(None, module))

    assert calls == ["s1", "s2"]
    assert result["done"] == 2 and result["created"] == 1
