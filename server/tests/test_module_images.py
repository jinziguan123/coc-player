"""模组图片失效修复 API。"""

import asyncio
import base64
import io

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app
from app.models import Base, Module  # noqa: F401


def _png_b64() -> str:
    image = Image.new("RGB", (8, 8), (20, 120, 80))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_regenerate_missing_module_image_updates_json(tmp_path, monkeypatch):
    from app.services import image_store, module_image_service

    engine = create_engine(f"sqlite:///{tmp_path / 'module-images.db'}")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(image_store, "IMAGES_DIR", tmp_path / "images")

    class PromptLLM:
        async def complete(self, messages, **kwargs):
            assert "提示词工程师" in messages[0]["content"]
            return "old chapel interior"

    class ImageLLM:
        def supports_image_gen(self):
            return True

        async def generate_image(self, prompt, size="1024x1024"):
            assert prompt.startswith("old chapel interior")
            return _png_b64()

    monkeypatch.setattr(module_image_service, "get_fast_llm", lambda: PromptLLM())
    monkeypatch.setattr(module_image_service, "get_image_llm", lambda: ImageLLM())
    try:
        with TestClient(app) as client:
            db = testing_session()
            module = Module(
                title="m", rule_system="coc",
                scenes=[{"id": "s1", "name": "教堂", "image": "/api/images/missing.jpg"}],
                npcs=[{"id": "n1", "name": "守墓人", "portrait": "/api/images/missing2.jpg"}],
                clues=[{"id": "c1", "name": "日记", "image": "/api/images/missing3.jpg"}],
            )
            db.add(module)
            db.commit()
            module_id = module.id
            db.close()

            response = client.post(
                f"/api/modules/{module_id}/images/regenerate",
                json={"kind": "scene", "item_id": "s1", "field": "image"},
            )
            assert response.status_code == 200, response.text
            url = response.json()["url"]
            assert url.startswith("/api/images/")
            saved = testing_session().get(Module, module_id)
            assert saved.scenes[0]["image"] == url
            assert (tmp_path / "images" / url.rsplit("/", 1)[-1]).is_file()
    finally:
        app.dependency_overrides.clear()


def test_regenerate_reuses_existing_file(tmp_path, monkeypatch):
    from app.services import image_store, module_image_service

    monkeypatch.setattr(image_store, "IMAGES_DIR", tmp_path / "images")
    url = image_store.save_image_b64(_png_b64())
    engine = create_engine(f"sqlite:///{tmp_path / 'module-images.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(title="m", rule_system="coc", scenes=[{"id": "s1", "image": url}])
    db.add(module)
    db.commit()

    class NoLLM:
        def supports_image_gen(self):
            raise AssertionError("已有图片文件时不应调用模型")

    monkeypatch.setattr(module_image_service, "get_image_llm", lambda: NoLLM())
    assert asyncio.run(module_image_service.regenerate_module_image(db, module, "scene", "s1")) == url


def test_encounter_image_uses_encounter_prompt(tmp_path, monkeypatch):
    from app.services import image_store, module_image_service

    monkeypatch.setattr(image_store, "IMAGES_DIR", tmp_path / "images")
    engine = create_engine(f"sqlite:///{tmp_path / 'encounter-images.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(
        title="m", rule_system="coc",
        npcs=[{"id": "n1", "name": "怪物", "description": "触手", "weapon": "撕咬", "encounter_image": "/api/images/deleted.jpg"}],
    )
    db.add(module)
    db.commit()

    class PromptLLM:
        async def complete(self, messages, **kwargs):
            assert "遭遇战敌人" in messages[0]["content"]
            assert "武器/攻击方式：撕咬" in messages[1]["content"]
            return "monster encounter"

    class ImageLLM:
        def supports_image_gen(self):
            return True

        async def generate_image(self, prompt, size="1024x1024"):
            return _png_b64()

    monkeypatch.setattr(module_image_service, "get_fast_llm", lambda: PromptLLM())
    monkeypatch.setattr(module_image_service, "get_image_llm", lambda: ImageLLM())
    url = asyncio.run(module_image_service.regenerate_module_image(db, module, "npc", "n1", "encounter_image"))
    assert url and db.get(Module, module.id).npcs[0]["encounter_image"] == url


def test_regenerate_scene_visual_variant_updates_variant_cache(tmp_path, monkeypatch):
    from app.services import image_store, module_image_service

    monkeypatch.setattr(image_store, "IMAGES_DIR", tmp_path / "images")
    engine = create_engine(f"sqlite:///{tmp_path / 'scene-variant.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(
        title="m", rule_system="coc",
        scenes=[{"id": "s1", "title": "教堂", "image_variants": {}}],
    )
    db.add(module)
    db.commit()

    class PromptLLM:
        async def complete(self, messages, **kwargs):
            return "flooded chapel"

    class ImageLLM:
        def supports_image_gen(self):
            return True

        async def generate_image(self, prompt, size="1024x1024"):
            return _png_b64()

    monkeypatch.setattr(module_image_service, "get_fast_llm", lambda: PromptLLM())
    monkeypatch.setattr(module_image_service, "get_image_llm", lambda: ImageLLM())
    url = asyncio.run(module_image_service.regenerate_module_image(
        db, module, "scene", "s1", "image_variant", "flooded",
    ))
    assert url and db.get(Module, module.id).scenes[0]["image_variants"]["flooded"] == url


def test_force_regenerate_replaces_existing_image(tmp_path, monkeypatch):
    """用户点「重新生成」必须真的重出一张。

    默认的 force=False 是 <img onError> 的自愈语义：图还在就复用，不为每次加载报错重花
    一次生图的钱。但用户主动点按钮时若也走这条短路，就会拿回原来那张，点了跟没点一样。
    """
    from app.services import image_store, module_image_service

    monkeypatch.setattr(image_store, "IMAGES_DIR", tmp_path / "images")
    old = image_store.save_image_b64(_png_b64())
    engine = create_engine(f"sqlite:///{tmp_path / 'force.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(title="m", rule_system="coc", scenes=[{"id": "s1", "name": "教堂", "image": old}])
    db.add(module); db.commit()

    class PromptLLM:
        async def complete(self, messages, **kwargs):
            return "old chapel interior"

    class ImageLLM:
        def supports_image_gen(self):
            return True

        async def generate_image(self, prompt, size="1024x1024"):
            return _png_b64()

    monkeypatch.setattr(module_image_service, "get_fast_llm", lambda: PromptLLM())
    monkeypatch.setattr(module_image_service, "get_image_llm", lambda: ImageLLM())

    new = asyncio.run(
        module_image_service.regenerate_module_image(db, module, "scene", "s1", force=True))
    assert new and new != old, "force=True 必须产出新图，不能复用旧的"
    assert db.get(Module, module.id).scenes[0]["image"] == new   # 已回写


def test_upload_module_image_writes_back(tmp_path, monkeypatch):
    """手动上传换图：没有它，配图完全受制于文生图——没配生图模型的人一张图都拿不到。"""
    from app.services import image_store

    engine = create_engine(f"sqlite:///{tmp_path / 'upload.db'}")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(image_store, "IMAGES_DIR", tmp_path / "images")
    try:
        with TestClient(app) as client:
            db = testing_session()
            module = Module(
                title="m", rule_system="coc",
                scenes=[{"id": "s1", "name": "教堂"}],          # 本来就没有图
                npcs=[{"id": "n1", "name": "守墓人"}],
            )
            db.add(module); db.commit()
            module_id = module.id
            db.close()

            png = base64.b64decode(_png_b64())
            r = client.post(
                f"/api/modules/{module_id}/images/upload",
                files={"file": ("x.png", png, "image/png")},
                data={"kind": "scene", "item_id": "s1", "field": "image"},
            )
            assert r.status_code == 200, r.text
            url = r.json()["url"]
            saved = testing_session().get(Module, module_id)
            assert saved.scenes[0]["image"] == url
            # 一律重存为 JPEG（与生成图同一条落盘路径，两者在系统里没有区别）
            assert url.endswith(".jpg")
            assert (tmp_path / "images" / url.rsplit("/", 1)[-1]).is_file()

            # 不是图片的文件要被挡住，而不是原样落进图片目录
            bad = client.post(
                f"/api/modules/{module_id}/images/upload",
                files={"file": ("x.txt", b"this is not an image", "text/plain")},
                data={"kind": "scene", "item_id": "s1", "field": "image"},
            )
            assert bad.status_code == 422

            # 条目不存在要报 404，不能静默丢图
            missing = client.post(
                f"/api/modules/{module_id}/images/upload",
                files={"file": ("x.png", png, "image/png")},
                data={"kind": "scene", "item_id": "不存在", "field": "image"},
            )
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()
