"""移动存档兼容边界：读取值快照，写回时保留未参与处理的历史字段。

字段所有权：快照只读出业务需要的值；``with_states`` 是唯一的更新入口，只改个人状态
（裁定记录同时按个人状态重算整条状态）。其余字段原样来自存档，写回时从私有副本恢复，
不供业务规则读取或修改。新建记录走 ``new``：先编码成存档格式再读回，所以新建与读取写回
共用一套键名与默认值。存档 JSON 键只在本模块出现。
"""
from copy import deepcopy
from dataclasses import dataclass, field, replace

from app.domain.movement import person_status, rollup


@dataclass(frozen=True)
class PendingMove:
    anchor: int | None
    mover_ids: tuple[str, ...]
    origin: str | None
    target: str | None
    skill: str
    shared: bool
    states: tuple[tuple[str, str], ...]
    # 私有副本只用于无损写回，不供业务规则读取或修改。
    _source: dict = field(repr=False, compare=False)

    @classmethod
    def from_record(cls, record: dict) -> "PendingMove":
        source = deepcopy(record)
        movers = tuple(source.get("movers") or ())
        states = source.get("chars") or {cid: "pending" for cid in movers}
        return cls(
            anchor=source.get("anchor"), mover_ids=movers, origin=source.get("origin"),
            target=source.get("target"), skill=source.get("skill", ""),
            shared=bool(source.get("shared")), states=tuple(states.items()), _source=source,
        )

    @classmethod
    def new(
        cls, *, anchor: int, mover_ids: tuple[str, ...], origin: str | None, target: str | None,
        skill: str, exit: str, shared: bool,
    ) -> "PendingMove":
        """裁定时新建：所有移动者都待检定。"""
        return cls.from_record({
            "anchor": anchor, "movers": list(mover_ids), "origin": origin, "target": target,
            "skill": skill, "exit": exit, "shared": shared,
            "chars": {cid: "pending" for cid in mover_ids},
        })

    @property
    def waiting_ids(self) -> tuple[str, ...]:
        return tuple(cid for cid, status in self.states if status == "pending")

    def with_states(self, states: tuple[tuple[str, str], ...]) -> "PendingMove":
        return replace(self, states=states)

    def to_record(self) -> dict:
        result = deepcopy(self._source)
        result["chars"] = dict(self.states)
        return result


@dataclass(frozen=True)
class MoveRecord:
    """本回合一条移动裁定（落位、待检定、受阻、商量……）的读取模型。

    ``status`` 是整条裁定的状态；``states`` 是按人记的检定状态（pending/tried/moved/failed），
    旧记录或非门控裁定可能没有。某人的状态以个人状态优先，没有才沿用整条状态。
    """
    anchor: int | None
    status: str
    mover_ids: tuple[str, ...]
    mover_names: tuple[str, ...]
    origin: str | None
    target: str | None
    stayer_names: tuple[str, ...]
    exit: str
    skill: str
    reason: str
    clues: tuple[str, ...]
    states: tuple[tuple[str, str], ...]
    _source: dict = field(repr=False, compare=False)
    _dirty: bool = field(default=False, repr=False, compare=False)

    @classmethod
    def from_record(cls, record: dict) -> "MoveRecord":
        source = deepcopy(record)
        return cls(
            anchor=source.get("anchor"), status=str(source.get("status") or ""),
            mover_ids=tuple(source.get("movers") or ()),
            mover_names=tuple(source.get("mover_names") or ()),
            origin=source.get("origin"), target=source.get("target"),
            stayer_names=tuple(source.get("stayers") or ()),
            exit=str(source.get("exit") or ""), skill=str(source.get("skill") or ""),
            reason=str(source.get("reason") or ""), clues=tuple(source.get("clues") or ()),
            states=tuple((source.get("char_status") or {}).items()), _source=source,
        )

    @classmethod
    def new(
        cls, *, anchor: int, status: str, mover_ids: tuple[str, ...], mover_names: tuple[str, ...],
        origin: str | None, target: str | None, stayer_names: tuple[str, ...] = (),
        exit: str = "", skill: str = "", reason: str = "", clues: tuple[str, ...] = (),
        states: tuple[tuple[str, str], ...] | None = None, shared: bool | None = None,
    ) -> "MoveRecord":
        """裁定时新建。只有门控/落位裁定才带个人状态，只有待检定裁定才记共同障碍标志。"""
        record = {
            "anchor": anchor, "status": status, "movers": list(mover_ids),
            "mover_names": list(mover_names), "origin": origin, "target": target,
            "stayers": list(stayer_names), "exit": exit, "skill": skill, "reason": reason,
            "clues": list(clues),
        }
        if states is not None:
            record["char_status"] = dict(states)
        if shared is not None:
            record["shared"] = shared
        return cls.from_record(record)

    def status_of(self, char_id: str) -> str:
        """某人在这条裁定里的对外状态；共同障碍上的 tried 对外仍是待检定。"""
        per = dict(self.states)
        if char_id in per:
            return person_status(per[char_id])
        return self.status

    def names_of(self, char_ids) -> tuple[str, ...]:
        """按记录里的移动者顺序取这些人的名字；旧记录缺名字时返回空。"""
        wanted = set(char_ids)
        return tuple(n for i, n in zip(self.mover_ids, self.mover_names) if i in wanted)

    def concerns(self, entry: PendingMove) -> bool:
        """这条裁定是否就是该待检定移动建立时写下的那条。"""
        return self.anchor == entry.anchor and set(self.mover_ids) == set(entry.mover_ids)

    def with_states(self, states: tuple[tuple[str, str], ...]) -> "MoveRecord":
        """合并个人状态，并据此重算整条状态；未提及的人保持原状态。"""
        merged = {**dict(self.states), **dict(states)}
        return replace(self, states=tuple(merged.items()), status=rollup(merged), _dirty=True)

    def to_record(self) -> dict:
        result = deepcopy(self._source)
        if self._dirty:
            result["char_status"] = dict(self.states)
            result["status"] = self.status
        return result


def load_move_records(records, anchor: int | None = None) -> tuple[MoveRecord, ...]:
    """把存档里的裁定记录读成快照；给了回合锚点就只取该回合的。"""
    loaded = tuple(MoveRecord.from_record(r) for r in records or ())
    if anchor is None:
        return loaded
    return tuple(r for r in loaded if r.anchor == anchor)


def busy_ids(records: tuple[MoveRecord, ...]) -> set[str]:
    """已有移动裁定的人（无论结果）；不等于待检定名单。"""
    return {cid for r in records for cid in r.mover_ids}
