from sqlalchemy.orm import Session

from app.models.character import Character
from app.rules import ruleset_for
from app.services import rule_options_service


def roll_attribute_sets(rule_system: str, count: int = 3) -> list[dict[str, int]]:
    return ruleset_for(rule_system).character.roll_attribute_sets(count)


def create_character(db: Session, data: dict) -> Character:
    rule_system = data["rule_system"]
    # 按这一桌的村规建卡：房主改过的技能表（新增技能、起始值）、建卡上限要体现在新卡上
    rs = rule_options_service.village_ruleset(db, rule_system)
    character_rules = rs.character

    computed = character_rules.create_character(data)

    ok, errors = character_rules.validate_character(computed)
    if not ok:
        raise ValueError(f"角色数据校验失败: {', '.join(errors)}")

    creation = data.get("creation")
    if creation:
        if not rs.has("creation"):
            raise ValueError("本规则系统没有建卡规则，不能按建卡记录校验")
        problems = rs.creation.validate_build(
            creation, computed["base_attributes"], computed["skills"],
        )
        if problems:
            raise ValueError("不符合建卡规则：" + "；".join(problems))
        computed["system_data"]["creation"] = rs.creation.CreationRecord.model_validate(
            creation,
        ).model_dump()

    character = Character(
        name=data["name"],
        module_id=data.get("module_id"),
        origin_character_id=data.get("origin_character_id"),
        rule_system=rule_system,
        is_player=data.get("is_player", True),
        owner_token=data.get("owner_token"),
        base_attributes=computed["base_attributes"],
        skills=computed["skills"],
        system_data=computed["system_data"],
        backstory=data.get("backstory", ""),
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return character


def weapon_holders(db: Session, rule_system: str) -> dict[str, int]:
    """武器名 → 持有它的角色数（卡上武器栏或战斗默认武器里有它）。

    规则页删武器时提示用：被删的武器，已有角色战斗时会改按卡上的数据结算。"""
    counts: dict[str, int] = {}
    rows = db.query(Character.system_data).filter(Character.rule_system == rule_system).all()
    for (sd,) in rows:
        sd = sd or {}
        names = {str(w.get("name") or "").strip() for w in (sd.get("weapons") or []) if isinstance(w, dict)}
        names.add(str((sd.get("combat") or {}).get("weapon") or "").strip())
        for n in names - {"", "徒手格斗"}:
            counts[n] = counts.get(n, 0) + 1
    return counts


def get_character(db: Session, character_id: str) -> Character | None:
    return db.get(Character, character_id)


def list_characters(db: Session, module_id: str | None = None) -> list[Character]:
    q = db.query(Character)
    if module_id:
        q = q.filter(Character.module_id == module_id)
    return q.order_by(Character.created_at.desc()).all()


def delete_character(db: Session, character_id: str) -> bool:
    char = db.get(Character, character_id)
    if not char:
        return False
    db.delete(char)
    db.commit()
    return True


def update_character(db: Session, character_id: str, updates: dict) -> Character | None:
    char = db.get(Character, character_id)
    if not char:
        return None
    for key, val in updates.items():
        if val is not None and hasattr(char, key):
            setattr(char, key, val)
    db.commit()
    db.refresh(char)
    return char


def change_conditions(
    db: Session, char: Character, add: list[str] | None = None, remove: list[str] | None = None,
    *, commit: bool = True,
) -> bool:
    """改角色卡上的持续状态（``system_data.conditions``，如流血）。返回是否真的有变化。

    只放规则钩子挂上/摘下的、跨越战斗的状态；擒抱、缴械、着火这类只在战斗态里有意义的不落卡。
    """
    sd = dict(char.system_data or {})
    before = list(sd.get("conditions") or [])
    after = [c for c in before if c not in (remove or [])]
    after += [c for c in (add or []) if c not in after]
    if after == before:
        return False
    sd["conditions"] = after
    char.system_data = sd
    db.add(char)
    db.commit() if commit else db.flush()
    return True
