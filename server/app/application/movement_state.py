"""移动存档兼容边界：读取值快照，写回时保留未参与处理的历史字段。"""
from copy import deepcopy
from dataclasses import dataclass, field, replace


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

    @property
    def waiting_ids(self) -> tuple[str, ...]:
        return tuple(cid for cid, status in self.states if status == "pending")

    def with_states(self, states: tuple[tuple[str, str], ...]) -> "PendingMove":
        return replace(self, states=states)

    def to_record(self) -> dict:
        result = deepcopy(self._source)
        result["chars"] = dict(self.states)
        return result
