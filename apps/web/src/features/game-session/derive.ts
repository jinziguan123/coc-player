/**
 * 游戏页的纯派生逻辑。
 *
 * 从 `GameSessionPage`（两千多行）里抽出来，不是为了把文件变短，而是因为这些规则
 * 一点都不平凡——「只算本场战斗」「3D 骰未落定先跳过」「OOC 与后端 split_ooc 对齐」——
 * 却因为埋在组件的 `useMemo` 里而**一条测试都没有**，改坏了只能靠肉眼复查。
 *
 * 这里只放纯函数：输入输出确定、不碰 React、可直接测。
 */

import type { CombatLogEntry, CombatResultView, CombatState } from '@/components/game/CombatStage'
import type { SeatKind } from '@/components/game/SeatIcon'

/** KP 指令标签：不该出现在玩家可见文本里。 */
export const CMD_TAG_RE =
  /\[(DICE_CHECK|NPC_ACT|SCENE_CHANGE|SAY|GROUP|MOVE|MAP_MARK|HANDOUT)[^\]]*\]|\[\/SAY\]/g

/** 场外发言（OOC）：中英文小括号，与后端 `split_ooc` 的约定一致。 */
export const OOC_RE = /（[^（）]*）|\([^()]*\)/g

const HTML_TAG_RE =
  /<\/?(?:b|i|u|s|em|strong|br|p|span|div|h[1-6]|ul|ol|li|code|pre|blockquote|hr|a)\b[^>]*>/gi

/** NPC 名字 → 稳定色相，保证同一 NPC 每次颜色一致。 */
export function npcHue(name?: string): number {
  let h = 0
  for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

/** 去掉 KP 指令标签与裸 HTML，并压掉多余空行。 */
export function stripCommandTags(text: string): string {
  return text
    .replace(CMD_TAG_RE, '')
    .replace(HTML_TAG_RE, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** 拆出正式行动与 OOC（小括号场外）内容，与后端 `split_ooc` 对齐。 */
export function splitOOC(text: string): { inChar: string; ooc: string } {
  const parts = text.match(OOC_RE) || []
  const inChar = text.replace(OOC_RE, '').trim()
  const ooc = parts.map((p) => p.slice(1, -1).trim()).filter(Boolean).join(' ')
  return { inChar, ooc }
}

export function fmtTime(ts?: number): string {
  if (!ts) return ''
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ── 队伍与身份 ────────────────────────────────────────────────────────────

export interface PartyMember {
  isMine: boolean
  role: string
}

interface ParticipantLike {
  character_name?: string | null
  character_id?: string | null
  is_mine: boolean
  role: string
}

/** 全桌只有自己一个真人？回合确认制是给多人同桌用的（各自写完、凑齐再一起交给 KP）——
 *  一个人时那一步没有协同对象，只是每回合多一次点击，于是发送即推进。
 *
 *  判错的代价不对称：误判成单人会让多人局在队友还没写完时就把回合交出去，所以只认
 *  「已占角色席的真人」，AI 队友与 KP 席都不算，宁可多点一次也不要提前交卷。
 *  无 participants 的旧单人会话同样算独自开团。 */
export function isSoloTable(participants: ParticipantLike[] | undefined): boolean {
  const humans = (participants || []).filter((p) => p.role === 'human' && p.character_id)
  return humans.length <= 1
}

export function buildPartyByName(
  participants: ParticipantLike[] | undefined,
): Record<string, PartyMember> {
  const m: Record<string, PartyMember> = {}
  for (const p of participants || []) {
    if (p.character_name) m[p.character_name] = { isMine: p.is_mine, role: p.role }
  }
  return m
}

/** 决定一条消息用哪种席位图标。玩家角色一视同仁，没有「主角」特权。 */
export function resolveActorKind(
  partyByName: Record<string, PartyMember>,
  name?: string,
  isPlayer?: boolean,
): SeatKind {
  if (isPlayer) return 'me'
  const p = name ? partyByName[name] : undefined
  if (p?.isMine) return 'me'
  if (p?.role === 'ai') return 'ai'
  if (p?.role === 'human') return 'human'
  return 'npc'
}

// ── 战斗日志与最近结算 ────────────────────────────────────────────────────

interface MessageLike {
  id?: string
  type: string
  content: string
  sequence_num?: number | null
  metadata?: Record<string, unknown> | null
}

/**
 * 战斗日志抽屉的内容：消息流里带 `combat_log` 标记的机械结算行。
 *
 * `since` 是本场战斗的起点 seq，用来把上一场的结算行挡在外面；重连时后端未给出
 * 起点则为 null，此时全收（与落库历史一致）。派生自 messages，因此实时/历史/重连
 * 三条路径天然一致，不必单独维护一份日志数组。
 */
export function selectCombatLog(
  messages: MessageLike[],
  since: number | null,
): CombatLogEntry[] {
  const out: CombatLogEntry[] = []
  for (const m of messages) {
    if (m.metadata?.combat_log !== true) continue
    if (since != null && m.sequence_num != null && m.sequence_num <= since) continue
    if (!m.id) continue
    out.push({ id: m.id, kind: m.type === 'dice' ? 'dice' : 'system', content: m.content })
  }
  return out
}

/**
 * 战斗态下钉在面板顶部的「本场最近一次掷骰结算」。
 *
 * 从后往前找最近一条 dice；两条关键规则：
 * - **3D 投掷未落定的先跳过**，否则结果会先于骰子动画蹦出来，剧透成败；
 * - **越过本场起点即停**，不显示上一场或开战前的旧结果。
 */
export function selectCombatResult(input: {
  combat: CombatState | null
  messages: MessageLike[]
  since: number | null
  diceAnimating: Set<string>
  revealedDice: Set<string>
}): CombatResultView | null {
  const { combat, messages, since, diceAnimating, revealedDice } = input
  if (!combat) return null
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (since != null && m.sequence_num != null && m.sequence_num <= since) break
    if (m.type !== 'dice' || !m.metadata) continue
    if (m.id && diceAnimating.has(m.id) && !revealedDice.has(m.id)) continue
    return { content: m.content, metadata: m.metadata as Record<string, unknown> }
  }
  return null
}
