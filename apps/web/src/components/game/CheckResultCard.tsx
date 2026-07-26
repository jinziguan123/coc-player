// 技能/属性检定结果卡：把「掷出多少 / 目标多少 / 成没成」从散文里拆出来做成读数。
//
// 后端的 dice 事件 content 形如
//   「周卫国｜侦查 检定（普通）：普通成功（成功 (34 ≤ 60)）」
// —— 点数与成败各出现两次，且全都是同一字号的行内文字。而这三件事恰恰是玩家
// 唯一想立刻知道的。metadata 里已备齐 actor/skill/roll/target/outcome/tier，
// 所以有元数据时改画结构化读数，没有（旧事件/追逐/自定义骰）时回落散文行。
import type { ReactNode } from 'react'
import { GiRollingDices } from 'react-icons/gi'

/** 检定结果按成败取强调色。兼容引擎英文枚举与 SAN 检定的中文。 */
export function diceAccent(outcome: string): string {
  const s = String(outcome || '')
  if (s.includes('critical') || s.includes('大成功')) return 'var(--color-dice-gold)'    // 大成功：金黄
  if (s.includes('fumble') || s.includes('大失败')) return 'var(--color-dice-fumble)'    // 大失败：刺目血色（暗底上黑色不可见）
  if (s.includes('success') || s === '成功') return 'var(--color-success)'    // 其余成功：绿
  if (s.includes('fail') || s.includes('失败')) return 'var(--color-danger)'  // 普通失败：红
  return 'var(--color-text-secondary)'
}

/** 检定 outcome 枚举 → 中文短标签（对抗卡每侧的成败注脚）。 */
export function outcomeLabel(outcome: string): string {
  const s = String(outcome || '')
  if (s.includes('critical') || s === '大成功') return '大成功'
  if (s.includes('fumble') || s === '大失败') return '大失败'
  if (s.includes('hard_success')) return '困难成功'
  if (s.includes('success') || s === '成功') return '成功'
  if (s.includes('fail') || s.includes('失败')) return '失败'
  return outcome
}

/** 达成等级（纯按骰值 vs 技能值）→ 中文，与后端 TIER_LABEL 对齐。 */
const TIER_LABEL: Record<string, string> = {
  critical: '大成功',
  extreme: '极难成功',
  hard: '困难成功',
  regular: '普通成功',
  fail: '普通失败',
  fumble: '大失败',
}

/** 目标值相对技能值的倍率 → 本次要求的难度档。
 *  后端没把 difficulty 放进 metadata，但 target 是由它算出来的：
 *  普通=技能值、困难=半值、极难=五分之一。据此还原，散文里的「（困难）」就不丢了。 */
function difficultyLabel(skillValue?: number, target?: number): string {
  if (!skillValue || !target || skillValue <= 0) return ''
  if (target === skillValue) return ''                       // 普通：不必标，默认即是
  if (target === Math.floor(skillValue / 2)) return '困难'
  if (target === Math.floor(skillValue / 5)) return '极难'
  return ''
}

export interface CheckResultMeta {
  actor?: string
  skill?: string
  skill_value?: number
  roll?: number
  target?: number
  outcome?: string
  tier?: string
}

/** 元数据是否够画结构化读数——差一样就整体回落散文，不做半截卡。 */
export function hasCheckReadout(meta: CheckResultMeta | undefined): boolean {
  return !!meta
    && typeof meta.roll === 'number'
    && typeof meta.target === 'number'
    && !!meta.outcome
}

export function CheckResultCard({
  meta, blind, animClass, chips, ts,
}: {
  meta: CheckResultMeta
  /** 暗投：结果对玩家隐藏 → 走中性灰，不按成败着色，也不放大点数 */
  blind: boolean
  animClass: string
  /** 奖励/惩罚骰、贯穿/燃烧等注记，由调用方渲染后传入 */
  chips?: ReactNode
  ts?: string
}) {
  const accent = blind ? 'var(--color-text-secondary)' : diceAccent(meta.outcome || '')
  const label = outcomeLabel(meta.outcome || '')
  // 「达成等级」注脚只在真正有信息量时出现：本次判失败、但骰值其实够到了某个成功档
  // （例：技能 80 掷 55，普通档够、可这次要求困难 40 → 判失败）。
  // 若成败与等级本就同向（成功→普通成功、失败→普通失败），补这一枚只是复述，反成噪声。
  const outcomeFailed = /fail|fumble|失败/.test(String(meta.outcome || ''))
  const tierIsSuccess = ['critical', 'extreme', 'hard', 'regular'].includes(String(meta.tier || ''))
  const tierNote = outcomeFailed && tierIsSuccess ? TIER_LABEL[String(meta.tier)] : ''
  const diff = difficultyLabel(meta.skill_value, meta.target)
  const subject = [meta.actor, meta.skill].filter(Boolean).join(' · ')

  return (
    <div
      className={`dice-card dice-readout rounded-md px-3 py-2 flex items-center gap-3 ${animClass}`}
      style={{ borderLeft: `3px solid ${accent}`, width: 'fit-content', maxWidth: '100%' }}
    >
      <GiRollingDices style={{ color: accent, fontSize: '1.15rem', flexShrink: 0 }} />

      <div className="min-w-0">
        {subject && (
          <div
            className="truncate"
            style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}
          >
            {subject}
          </div>
        )}
        {/* 读数：掷出的点数是这张卡的主角，目标值作分母跟在后面 */}
        <div className="flex items-baseline gap-1">
          <span
            className="dice-readout-roll"
            style={{ color: accent }}
          >
            {blind ? '??' : meta.roll}
          </span>
          <span
            className="font-mono tabular-nums"
            style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}
          >
            /{meta.target}
          </span>
          {diff && (
            <span
              className="ml-0.5"
              style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)', opacity: 0.8 }}
            >
              {diff}
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        <span
          className="dice-outcome-chip"
          style={{ color: accent, borderColor: accent }}
        >
          {blind ? '暗投' : label}
        </span>
        {tierNote && !blind && (
          <span className="chip" title="纯按骰值对技能值达成的等级，与本次要求的难度分开看">
            达成 {tierNote}
          </span>
        )}
        {chips}
      </div>

      {ts && (
        <span
          className="self-end flex-shrink-0"
          style={{ fontSize: '0.6rem', opacity: 0.5, color: 'var(--color-text-secondary)' }}
        >
          {ts}
        </span>
      )}
    </div>
  )
}
