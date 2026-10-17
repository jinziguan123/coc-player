import type { CombatState, PendingReaction, PendingRoll } from '@/components/game/CombatStage'
import type { ChaseState } from '@/components/game/ChasePanel'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isCombatGrid(value: unknown): value is NonNullable<CombatState['grid']> {
  if (!isRecord(value)) return false
  return typeof value.cols === 'number'
    && typeof value.rows === 'number'
    && typeof value.cell_m === 'number'
}

function isCombatant(value: unknown): value is CombatState['order'][number] {
  if (!isRecord(value)) return false
  return typeof value.id === 'string'
    && typeof value.name === 'string'
    && (value.side === 'player' || value.side === 'ally' || value.side === 'enemy')
    && typeof value.is_human === 'boolean'
    && typeof value.hp === 'number'
    && typeof value.max_hp === 'number'
    && typeof value.status === 'string'
}

export function parseCombatState(value: unknown): CombatState | null {
  if (!isRecord(value)) return null
  if (typeof value.round !== 'number') return null
  if (value.turn !== null && typeof value.turn !== 'string') return null
  if (!Array.isArray(value.order) || !value.order.every(isCombatant)) return null
  if (value.started_seq !== undefined && typeof value.started_seq !== 'number') return null
  const pendingRoll = parsePendingRoll(value.pending_roll)
  return {
    round: value.round,
    turn: value.turn,
    order: value.order,
    ...(value.started_seq === undefined ? {} : { started_seq: value.started_seq }),
    ...(pendingRoll ? { pending_roll: pendingRoll } : {}),
    ...(isCombatGrid(value.grid) ? { grid: value.grid } : {}),
    ...(typeof value.kp_controls_enemies === 'boolean' ? { kp_controls_enemies: value.kp_controls_enemies } : {}),
    ...(typeof value.awaiting_kp === 'string' ? { awaiting_kp: value.awaiting_kp } : {}),
  }
}

function parsePendingRoll(value: unknown): PendingRoll | null {
  if (!isRecord(value)) return null
  if (typeof value.actor_id !== 'string' || typeof value.kind !== 'string') return null
  return {
    actor_id: value.actor_id,
    kind: value.kind,
    label: typeof value.label === 'string' ? value.label : '投掷',
    ...(typeof value.victim_id === 'string' ? { victim_id: value.victim_id } : {}),
  }
}

export function parsePendingReaction(value: unknown): PendingReaction | null {
  if (!isRecord(value)) return null
  if (typeof value.attacker_id !== 'string' || typeof value.defender_id !== 'string') return null
  if (typeof value.weapon !== 'string' || typeof value.ranged !== 'boolean') return null
  if (!isStringArray(value.allowed)) return null
  if (typeof value.attacker_name !== 'string' || typeof value.defender_name !== 'string') return null
  return {
    attacker_id: value.attacker_id,
    defender_id: value.defender_id,
    weapon: value.weapon,
    ranged: value.ranged,
    allowed: value.allowed,
    attacker_name: value.attacker_name,
    defender_name: value.defender_name,
  }
}

export function parseChaseState(value: unknown): ChaseState | null {
  if (!isRecord(value)) return null
  if (typeof value.round !== 'number' || typeof value.gap !== 'number') return null
  if (typeof value.escape_at !== 'number' || typeof value.caught_at !== 'number') return null
  if (typeof value.quarry !== 'string' || typeof value.pursuer !== 'string') return null
  const parsed: ChaseState = {
    round: value.round,
    gap: value.gap,
    escape_at: value.escape_at,
    caught_at: value.caught_at,
    quarry: value.quarry,
    pursuer: value.pursuer,
    // 新后端才带：比拼技能与双方 MOV（旧存档 / 旧后端缺省即不显示）
    skill: typeof value.skill === 'string' ? value.skill : undefined,
    quarry_mov: typeof value.quarry_mov === 'number' ? value.quarry_mov : undefined,
    pursuer_mov: typeof value.pursuer_mov === 'number' ? value.pursuer_mov : undefined,
  }
  // 群体追逐名单：只有新后端 / 多目标追逐才带，旧态不凭空补
  if (Array.isArray(value.quarries)) {
    const names = value.quarries.filter((item): item is string => typeof item === 'string')
    if (names.length) parsed.quarries = names
  }
  if (Array.isArray(value.pursuers)) {
    const names = value.pursuers.filter((item): item is string => typeof item === 'string')
    if (names.length) parsed.pursuers = names
  }
  return parsed
}
