from sqlalchemy.orm import Session

from app.models.character import Character
from app.rules import ruleset_for
from app.services import rule_options_service


def roll_attribute_sets(rule_system: str, count: int = 3) -> list[dict[str, int]]:
    return ruleset_for(rule_system).character.roll_attribute_sets(count)


def create_character(db: Session, data: dict) -> Character:
    rule_system = data["rule_system"]
    # 按这一桌的村规建卡：房主改过的技能表（新增技能、起始值）要体现在新卡上
    character_rules = rule_options_service.village_ruleset(db, rule_system).character

    computed = character_rules.create_character(data)

    ok, errors = character_rules.validate_character(computed)
    if not ok:
        raise ValueError(f"角色数据校验失败: {', '.join(errors)}")

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
    db.commit()
    return True
