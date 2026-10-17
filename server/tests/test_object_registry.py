from types import SimpleNamespace

from app.services import object_registry


def _fixture():
    module = SimpleNamespace(
        scenes=[{"id": "study", "name": "书房", "title": "书房"}],
        npcs=[{"id": "butler", "name": "管家"}],
    )
    session = SimpleNamespace(world_state={})
    return module, session


def test_resolve_scene_and_npc_to_stable_id():
    module, session = _fixture()
    assert object_registry.resolve(module, session, "书房") == "scene:study"
    assert object_registry.resolve(module, session, "管家") == "npc:butler"


def test_resolve_unknown_falls_back_to_normalized_text():
    module, session = _fixture()
    assert object_registry.resolve(module, session, "  地下室的门 ") == "地下室的门"


def test_registered_object_alias_resolves():
    module, session = _fixture()
    session.world_state = object_registry.register(
        {}, object_id="object:cellar_door", name="地下室的门",
        kind="door", aliases=("暗门",),
    )
    assert object_registry.resolve(module, session, "暗门") == "object:cellar_door"
