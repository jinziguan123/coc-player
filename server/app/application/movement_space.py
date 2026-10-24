"""将存储中的模组字段投影为领域空间快照；不持有 ORM 对象或可变 JSON 引用。"""
from app.domain.movement_language import EXIT_WORD_SET
from app.domain.movement_space import ExitSnapshot, SceneSnapshot, SpaceSnapshot


def project_scene(scene: dict) -> SceneSnapshot:
    sid = str(scene.get("id") or "")
    terms = {sid.strip()}
    for key in ("name", "title"):
        value = str(scene.get(key) or "").strip()
        if value:
            terms.add(value)
    for kw in scene.get("keywords") or []:
        kw = str(kw or "").strip()
        if len(kw) >= 2 and kw not in EXIT_WORD_SET:
            terms.add(kw)
    exits = tuple(
        ExitSnapshot(
            target=str(ex.get("to") or "").strip(),
            names=(str(ex.get("name") or ""), *(str(a) for a in ex.get("aliases") or [])),
            description=str(ex.get("description") or ""),
        )
        for ex in scene.get("exits") or [] if isinstance(ex, dict)
    )
    return SceneSnapshot(sid, frozenset(t for t in terms if t),
                         str(scene.get("description") or ""), exits)


def project_space(scenes: list[dict], location_ids: list[str], candidates: list[str]) -> SpaceSnapshot:
    return SpaceSnapshot(tuple(project_scene(s) for s in scenes), tuple(location_ids), tuple(candidates))
