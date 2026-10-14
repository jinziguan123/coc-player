/**
 * 聊天流里的骰子消息：对抗卡 / 连射卡 / 检定读数卡 / 回落的散文行。
 *
 * 从 GameSessionPage 的按类型渲染分支里拆出——这一支只读消息本身与「是否新到达」，
 * 不依赖页面其他状态。3D 投掷期间隐藏结果卡的判断仍留在页面（它要看动画队列）。
 */
import { GiRollingDices } from 'react-icons/gi'
import { fmtTime } from '@/features/game-session/derive'
import type { ChatMessage } from '@/stores/sessionStore'
import type { DiceSpec } from './DiceRoller'
import { buildCheckCaption } from './diceNotation'
import { normalizeOpposedData } from './opposedDice'
import { BurstCard, OpposedCard, type BurstData } from './DiceContestCards'
import { CheckResultCard, hasCheckReadout, diceAccent, type CheckResultMeta } from './CheckResultCard'

export function DiceMessage({ msg, fresh }: { msg: ChatMessage; fresh: boolean }) {
  // 对抗卡：命中/反击/闪避这类攻守对抗 → 两边并排 + VS + 高亮胜方（参考 BG3 对抗判定）
  const opposedData = normalizeOpposedData(msg.metadata)
  if (opposedData) {
    return <OpposedCard data={opposedData}
             fresh={fresh} ts={fmtTime(msg.ts)} />
  }
  // 连射卡：一轮多枪逐发结果
  if (msg.metadata?.combat_burst) {
    return <BurstCard data={msg.metadata as unknown as BurstData}
             fresh={fresh} ts={fmtTime(msg.ts)} />
  }
  // 暗投/暗骰：结果对玩家隐藏 → 用中性灰、不按成败着色
  const blind = !!msg.metadata?.blind
  const accent = blind
    ? 'var(--color-text-secondary)'
    : diceAccent(String(msg.metadata?.outcome ?? ''))
  // 去掉历史数据里可能残留的旧 🎲 前缀，统一用矢量骰子图标
  const diceText = msg.content.replace(/^🎲\s*/, '')
  // 伤害骰注记（贯穿/燃烧/晕）：贯穿走金色高亮，其余血色
  const diceFlags = ((msg.metadata?.dice as { flags?: string[] } | undefined)?.flags) || []
  // 奖励/惩罚骰标注：本次 check 含额外十位骰时标出（暗投不揭示，避免泄露隐藏结果）。
  const checkCap = (!blind && msg.metadata?.dice)
    ? buildCheckCaption(msg.metadata.dice as DiceSpec) : null
  // 入场动效（仅新到达的骰子卡）：大成功金光脉冲、大失败血红震颤，其余普通弹入；一次不循环。
  const oc = String(msg.metadata?.outcome ?? '')
  let diceAnim = ''
  if (fresh) {
    if (!blind && (oc.includes('critical') || oc.includes('大成功'))) diceAnim = 'dice-critical'
    else if (!blind && (oc.includes('fumble') || oc.includes('大失败'))) diceAnim = 'dice-fumble'
    else diceAnim = 'dice-enter'
  }
  // 奖惩骰注记与伤害标志（贯穿/燃烧/晕）——两种卡型共用
  const noteChips = (
    <>
      {checkCap && (
        // 理由直接写进标签，不藏在 tooltip 里：奖惩骰实打实地改了成败概率，
        // 「凭什么」得和「有」一样显眼——触屏上根本没有 hover 这一说。
        <span className="chip font-semibold"
          title={`${checkCap.rule}；${checkCap.breakdown} → 结果 ${checkCap.result}`}
          style={{
            color: checkCap.kind === 'bonus' ? 'var(--color-dice-gold)' : 'var(--color-dice-fumble)',
            borderColor: checkCap.kind === 'bonus' ? 'var(--color-dice-gold)' : 'var(--color-dice-fumble)',
          }}>
          {checkCap.title}
          {checkCap.reasons.length > 0 && (
            <span className="font-normal" style={{ opacity: 0.85 }}>
              {' · '}{checkCap.reasons.join('、')}
            </span>
          )}
        </span>
      )}
      {diceFlags.map((f) => {
        const gold = f === '贯穿'
        const col = gold ? 'var(--color-dice-gold)' : 'var(--color-danger)'
        return (
          <span key={f} className="chip font-semibold" style={{ color: col, borderColor: col }}>
            {gold ? '贯穿!' : f}
          </span>
        )
      })}
    </>
  )
  // 元数据齐备（技能检定）→ 结构化读数卡；否则（追逐/自定义骰/旧事件）回落散文行。
  const checkMeta = msg.metadata as CheckResultMeta | undefined
  if (hasCheckReadout(checkMeta)) {
    return (
      <div className="chat-msg py-1">
        <CheckResultCard
          meta={checkMeta as CheckResultMeta}
          blind={blind}
          animClass={diceAnim}
          chips={noteChips}
          ts={fmtTime(msg.ts)}
        />
      </div>
    )
  }
  return (
    <div className="chat-msg py-1">
      <div className={`dice-card rounded-md px-3 py-2 text-sm flex items-start gap-2 ${diceAnim}`}
        style={{ borderLeft: `3px solid ${accent}`, width: 'fit-content', maxWidth: '100%' }}>
        <GiRollingDices style={{ color: accent, fontSize: '1.1rem', flexShrink: 0, marginTop: '0.1rem' }} />
        <span className="whitespace-pre-wrap">{diceText}</span>
        {noteChips}
        {fmtTime(msg.ts) && <span className="msg-ts self-end" style={{ flexShrink: 0 }}>{fmtTime(msg.ts)}</span>}
      </div>
    </div>
  )
}
