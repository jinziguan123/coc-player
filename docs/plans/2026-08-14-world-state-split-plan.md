# world_state 拆表：实施方案（2026-08-14）

> 进度（2026-08-14）：**已全部落地并逐项提交**，后端 pytest 1674 通过 + ruff 通过。
> 具体：Phase 1（combat → combat_states）、Phase 2（chase → chase_states）、
> Phase 3（usage/rag → session_stats）、Phase 4a（recaps → session_recaps）、
> Phase 4b（回合锁 turn_confirm → 既有 turn_state 列）、Phase 4c（剧情记忆 Pydantic
> schema + SCHEMA_VERSION 1→2 + fail-open 校验）。仍留在 world_state 的回合级 pending_*、
> 分头 party_locations、幂等台账 san_checked/scene_events_seen 走 schema 的 extra=allow 放行，
> 属后续轮次可选的收尾，不阻塞本目标。

> 落地 ADR-003 决策第 4 条，以及 docs/architecture.md §4.2「会话状态的实际分布」与 §8 P1 表
> 里点名「ADR-003 的拆表一项未做」的欠账。
>
> 现状一句话：GameSession.world_state 一个 JSON 列同时承载战斗、追逐、回合确认、分头位置、
> 幂等台账、剧情 flags、线索台账、NPC 记忆、幕后游标、滚动摘要、RAG 统计、token 用量、战报
> 等异构状态；world_state.py adapter 已给出 read/set_key/mutate 唯一口径，但整段 dict(ws) 加
> 整体重赋值的旧调用点仍有 36 处。

## 一、原则

1. 拆表优先级按「并发频率 + 查询需求 + 数据丢失风险」定，不按代码文件方便程度（ADR-003）。
2. 拆出的状态落到各自独立的表 + 各自的唯一读写口径（仿 world_state.py 的 read/mutate 语义，
   但落在新表上），版本号各自维护。
3. 每张新表 1:1 挂在 game_sessions：session_id 作主键 + 外键，删除会话时经 ORM 关系
   cascade="all, delete-orphan" 一并清理（与 SessionParticipant 同款）。
4. 迁移幂等 + 回填存量：建表后把 world_state 里已有的对应键搬进新表，再从 world_state 删除该键；
   参考 20260808_character_avatar_experiences.py 的 _columns() 防御。
5. 每步验收：后端 pytest -q（当前 1666 通过）+ ruff check + 前端 typecheck 全绿才提交。

## 二、拆表清单

| 顺序 | world_state 键 | 目标表 | 理由 |
|---|---|---|---|
| Phase 1 | combat | combat_states | 最高频、最易踩「原地改引用不落库」bug；已有 get_combat/_save_combat 唯一口径，切面最小 |
| Phase 2 | chase | chase_states | 同战斗，状态机高频强一致 |
| Phase 3 | session_usage、turn_usage、rag_stats | session_stats | 运行统计，不该进剧情 JSON；append-only 计数器，风险低 |
| 观察项（暂不动） | turn_confirm、party_locations、pending_*、san_checked、scene_events_seen | 待定（可能并入 turn_state） | 语义有一半已在 turn_state/回合事件里，先观察再动 |

保留在 world_state（剧情记忆，Phase 4 加 Pydantic 校验 + 版本迁移）：
flags、clue_ledger、npc_memory、team_memory、backstage、improvised_npcs、story_summary/story_summary_seq、
visited_scenes、handouts_issued、ending_reached/epilogue_done/end_vote、recaps（战报归档）。

## 三、新表形状（每张同构）

    class CombatState(Base, TimestampMixin):   # 例：combat_states
        __tablename__ = "combat_states"
        session_id = mapped_column(ForeignKey("game_sessions.id"), primary_key=True)
        state: Mapped[dict] = mapped_column(JSON, default=dict)
        version: Mapped[int] = mapped_column(default=1, server_default="1")

- 不复用 UUIDMixin：session_id 即主键，1:1。
- GameSession 加 combat_state 关系（uselist=False + cascade delete-orphan）。
- service 侧 get_combat / _save_combat 改读新表；上下文构建里唯一一处直读 world_state.combat
  （ai/context.py:898）改走 combat_service.get_combat。

## 四、迁移策略（Phase 1 示例）

upgrade()：create_table("combat_states") → 把 game_sessions.world_state 里 combat 非空的行
搬进 combat_states → 把 world_state 里的 combat 键删除（保留其它键）。

downgrade()：把 combat_states.state 写回 world_state.combat → drop_table。

## 五、验收与回滚

- 迁移回归测试（tests/test_migrations.py 风格）：空库、有 active combat 的旧库、重复跑迁移。
- 既有 test_combat*.py、test_chase*.py、test_world_state.py、test_room_sync.py 必须原样通过。
- 任一步红则在该分支内修；不达绿不提交。
