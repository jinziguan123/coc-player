// 战斗面板共享元数据/纯函数：图标映射、成败取色、出局/血量小工具。
// 图标全走 react-icons/gi（game-icons 风格），禁 emoji（项目硬性守则）。
import {
  GiCrossedSwords, GiShield, GiRun, GiBrickWall, GiFirstAidKit, GiBinoculars, GiGrab, GiBrokenAxe, GiAmmoBox, GiCrosshair, GiHandcuffs, GiFlame, GiFireBottle, GiDeathSkull, GiBrokenHeart, GiKnockedOutStars, GiBrokenBone, GiBleedingWound, GiAura,
} from 'react-icons/gi'
import type { Combatant, CombatStatus } from './types'

// 检定成败取色（战斗面板本地版，避免跨文件耦合 GameSessionPage 的 diceAccent）。
export function outcomeAccent(outcome: string): string {
  const s = (outcome || '').toLowerCase()
  if (s.includes('critical') || s.includes('大成功')) return 'var(--color-dice-gold)'
  if (s.includes('fumble') || s.includes('大失败')) return 'var(--color-dice-fumble)'
  if (s.includes('hard_success') || s.includes('success') || s.includes('成功')) return 'var(--color-success)'
  if (s.includes('fail') || s.includes('失败')) return 'var(--color-danger)'
  return 'var(--color-text-secondary)'
}
export function outcomeLabel(outcome: string): string {
  const s = (outcome || '').toLowerCase()
  if (s.includes('critical') || s === '大成功') return '大成功'
  if (s.includes('fumble') || s === '大失败') return '大失败'
  if (s.includes('hard_success')) return '困难成功'
  if (s.includes('success') || s === '成功') return '成功'
  if (s.includes('fail') || s.includes('失败')) return '失败'
  return outcome || ''
}

// 反应按钮：图标全走 react-icons/gi（game-icons 风格），禁 emoji。
export const REACTION_META: Record<string, { label: string; Icon: typeof GiCrossedSwords }> = {
  fight_back: { label: '反击', Icon: GiCrossedSwords },
  dodge: { label: '闪避', Icon: GiShield },
  cover: { label: '扑掩体', Icon: GiBrickWall },
}

// 状态徽标：正常（ok）不显示；其余各给中文标签与语义色。
export const STATUS_META: Record<Exclude<CombatStatus, 'ok'>, { label: string; color: string }> = {
  major_wound: { label: '重伤', color: 'var(--color-danger)' },
  dying: { label: '濒死', color: 'var(--color-danger-deep)' },
  unconscious: { label: '昏迷', color: 'var(--color-text-secondary)' },
  dead: { label: '死亡', color: 'var(--color-danger-deep)' },
  fled: { label: '逃离', color: 'var(--color-text-secondary)' },
}

// 令牌右上角状态小徽标（棋盘用）：状态 → gi 图标 + 语义色。
export const TOKEN_STATUS_META: Record<Exclude<CombatStatus, 'ok'>, { Icon: typeof GiCrossedSwords; color: string; label: string }> = {
  major_wound: { Icon: GiBrokenBone, color: 'var(--color-danger)', label: '重伤' },
  dying: { Icon: GiBrokenHeart, color: 'var(--color-danger)', label: '濒死' },
  unconscious: { Icon: GiKnockedOutStars, color: 'var(--color-text-secondary)', label: '昏迷' },
  dead: { Icon: GiDeathSkull, color: 'var(--color-danger-deep)', label: '死亡' },
  fled: { Icon: GiRun, color: 'var(--color-text-secondary)', label: '逃离' },
}

// 条件徽标：内置状态的专属图标（gi 图标 + 中文），叠加渲染于卡片。
export const CONDITION_META: Record<string, { label: string; Icon: typeof GiCrossedSwords }> = {
  grappled: { label: '被擒', Icon: GiHandcuffs },
  disarmed: { label: '缴械', Icon: GiBrokenAxe },
  burning: { label: '着火', Icon: GiFlame },
  // 来自可选规则组件「流血」（coc7.bleeding）：重伤后每轮失血，急救止血
  bleeding: { label: '流血', Icon: GiBleedingWound },
}

// 不认识的状态（如第三方规则插件新增的）用的通用徽标。
export const GENERIC_CONDITION_ICON = GiAura

/**
 * 参战方身上的状态 → 徽标（名称 + 图标）。内置状态用专属图标；其余用通用徽标，名称取
 * 组件声明的中文名（后端随 order 下发的 condition_labels），实在没有才显示键名。
 */
export function conditionBadges(c: Combatant): { key: string; label: string; Icon: typeof GiCrossedSwords }[] {
  return (c.conditions || []).map((key) => {
    const known = CONDITION_META[key]
    return {
      key,
      label: c.condition_labels?.[key] || known?.label || key,
      Icon: known?.Icon ?? GENERIC_CONDITION_ICON,
    }
  })
}

// 武器：拳头（徒手格斗）永远可选并置顶；其余从角色卡武器栏（system_data.weapons）来；
// 末尾「其它(手填)」切换成自由文本输入。UNARMED 与后端 resolve_weapon 的徒手口径一致。
export const UNARMED = '徒手格斗'
export const WEAPON_OTHER = '__other__'

// 主动动作元数据：图标 + 标签 + 目标类型（敌方/己方/无）。全部 gi 图标，已确认存在。
export type ActionKey = 'attack' | 'first_aid' | 'observe' | 'grapple' | 'disarm' | 'reload' | 'aim' | 'extinguish' | 'flee'
export const ACTIONS: Record<ActionKey, { label: string; Icon: typeof GiCrossedSwords; target: 'enemy' | 'ally' | 'none' }> = {
  attack: { label: '攻击', Icon: GiCrossedSwords, target: 'enemy' },
  first_aid: { label: '急救', Icon: GiFirstAidKit, target: 'ally' },
  observe: { label: '观察', Icon: GiBinoculars, target: 'none' },
  grapple: { label: '擒抱', Icon: GiGrab, target: 'enemy' },
  disarm: { label: '缴械', Icon: GiBrokenAxe, target: 'enemy' },
  reload: { label: '装填', Icon: GiAmmoBox, target: 'none' },
  aim: { label: '瞄准', Icon: GiCrosshair, target: 'none' },
  extinguish: { label: '灭火', Icon: GiFireBottle, target: 'none' },
  flee: { label: '逃跑', Icon: GiRun, target: 'none' },
}

// 死亡/逃离：该参战方已出局，格子灰掉、不可作为目标。
export function isOut(c: Combatant): boolean {
  return c.status === 'dead' || c.status === 'fled'
}

/**
 * 从当前行动者数到我，中间还隔着几个人（环形，跳过已出局者）。
 *
 * 给玩家看「再过 N 个就到你」用——比单说「轮到某某」有用：玩家据此决定现在能不能
 * 腾手去翻角色卡。0 表示轮到我自己（或算不出来，调用方据此不显示）。
 */
export function turnsUntil(order: Combatant[], activeId: string | null, myId: string | null): number {
  if (!activeId || !myId) return 0
  const alive = order.filter((c) => !isOut(c))
  const from = alive.findIndex((c) => c.id === activeId)
  const mine = alive.findIndex((c) => c.id === myId)
  // 当前行动者已出局（刚被打死、广播还没跟上）或我已出局 → 数不出距离，不显示
  if (from < 0 || mine < 0) return 0
  return mine >= from ? mine - from : alive.length - from + mine
}

export function pctOf(c: Combatant): number {
  return c.max_hp > 0 ? Math.max(0, Math.min(100, (c.hp / c.max_hp) * 100)) : 0
}

/**
 * 阵营取色：己方冷调 / 盟友冷灰 / 敌方血红。
 *
 * 曾经是「己方 --color-accent、敌方 --color-danger」。在暗色主题下那是「琥珀 vs 血红」，
 * 分得很开；可到了羊皮纸主题，这两个变量分别是 #8b2500 和 #8b0000——棋子上一个
 * rgb(139,37,0) 一个 rgb(139,0,0)，**肉眼分不出敌我**。在别处这只是配色偏好，
 * 在战棋图上是功能故障：分清敌我是这张图存在的理由。
 *
 * 所以改成按**冷暖**分而不是按「两种红」分——冷暖对比在任何主题、任何尺寸下都读得出。
 * 具体色值按主题定在 index.css（--combat-friend / --combat-ally / --combat-foe）。
 */
export function sideColor(c: Combatant): string {
  if (c.side === 'enemy') return 'var(--combat-foe)'
  if (c.side === 'ally') return 'var(--combat-ally)'
  return 'var(--combat-friend)'
}
