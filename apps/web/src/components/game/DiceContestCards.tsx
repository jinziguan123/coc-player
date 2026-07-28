/**
 * 对抗判定卡与连射卡：战斗里两类需要并排对照的结算展示。
 *
 * 从 GameSessionPage 搬出来——它们与页面状态无关，只吃一份数据渲染，
 * 留在两千行的页面文件里只是让人更难找到。
 */

import { GiCrossedSwords, GiLaurelCrown, GiRollingDices } from 'react-icons/gi'

import { diceAccent, outcomeLabel } from './CheckResultCard'
import type { OpposedData, OpposedSide } from './opposedDice'

/** 对抗判定卡：攻守两方并排 + 中央 VS + 高亮胜方（参考博得之门3的对抗结算呈现）。
 *  远程无守方检定时降级为单侧命中卡。 */
export function OpposedCard({ data, fresh, ts }: { data: OpposedData; fresh: boolean; ts?: string }) {
  const resultAccent = data.result === '命中' || data.result === '反击得手'
    ? 'var(--color-danger)'                       // 有人吃伤害 → 血色
    : data.result === '被闪开/防住'
      ? 'var(--color-success)'                     // 守方全身而退 → 绿
      : 'var(--color-text-secondary)'              // 未命中（无守方）→ 中性

  const Side = ({ s, won }: { s: OpposedSide; won: boolean }) => {
    const accent = diceAccent(s.outcome)
    return (
      <div className="flex flex-col items-center px-3 py-1.5 rounded-md transition-all"
        style={{
          minWidth: '5.5rem',
          background: won ? 'color-mix(in srgb, var(--color-bg-tertiary) 60%, transparent)' : 'transparent',
          border: won ? `1px solid ${accent}` : '1px solid transparent',
          boxShadow: won ? `0 0 10px -2px ${accent}` : 'none',
          opacity: won || data.winner === null ? 1 : 0.6,
        }}>
        <div className="flex items-center gap-1 max-w-[7rem]">
          {won && <GiLaurelCrown style={{ color: accent, fontSize: '0.8rem', flexShrink: 0 }} />}
          <span className="text-xs font-semibold truncate" style={{ color: 'var(--color-text-primary)' }}>{s.name}</span>
        </div>
        {/* 与聊天流结果卡、战斗结算回显共用同一套读数样式 */}
        <div className="dice-readout-roll my-0.5" style={{ color: accent }}>{s.roll}</div>
        <div style={{ fontSize: '0.6rem', color: 'var(--color-text-secondary)' }}>{s.skill} / {s.target}</div>
        <span className="dice-outcome-chip mt-0.5" style={{ color: accent, borderColor: accent }}>{outcomeLabel(s.outcome)}</span>
      </div>
    )
  }

  return (
    <div className="chat-msg py-1">
      <div className={`dice-card rounded-md px-3 py-2 ${fresh ? 'dice-enter' : ''}`}
        style={{ borderLeft: `3px solid ${resultAccent}`, width: 'fit-content', maxWidth: '100%' }}>
        <div className="flex items-center gap-1.5 mb-1" style={{ color: 'var(--color-text-secondary)', fontSize: '0.65rem' }}>
          <GiCrossedSwords style={{ fontSize: '0.8rem' }} />
          <span>对抗判定</span>
        </div>
        <div className="flex items-stretch gap-1">
          <Side s={data.attacker} won={data.winner === 'attacker'} />
          {data.defender && (
            <>
              <div className="flex items-center px-1">
                <span className="font-bold italic" style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', opacity: 0.7 }}>VS</span>
              </div>
              <Side s={data.defender} won={data.winner === 'defender'} />
            </>
          )}
        </div>
        <div className="text-center mt-1 font-semibold" style={{ fontSize: '0.8rem', color: resultAccent }}>
          {data.result}
          {ts && <span className="ml-2" style={{ fontSize: '0.6rem', opacity: 0.5, color: 'var(--color-text-secondary)' }}>{ts}</span>}
        </div>
      </div>
    </div>
  )
}

interface BurstShot {
  target: string
  roll?: number
  target_val?: number
  outcome?: string
  hit: boolean
  penalty: number
  damage?: number | null
  flags?: string[]
  gone?: boolean
}
export interface BurstData {
  weapon: string
  shots: BurstShot[]
}

export function BurstCard({ data, fresh, ts }: { data: BurstData; fresh: boolean; ts?: string }) {
  const hits = data.shots.filter((s) => s.hit).length
  const totalDmg = data.shots.reduce((sum, s) => sum + (s.damage || 0), 0)
  return (
    <div className="chat-msg py-1">
      <div className={`dice-card rounded-md px-3 py-2 ${fresh ? 'dice-enter' : ''}`}
        style={{ borderLeft: '3px solid var(--color-danger)', width: 'fit-content', maxWidth: '100%', minWidth: '15rem' }}>
        <div className="flex items-center gap-1.5 mb-1" style={{ color: 'var(--color-text-secondary)', fontSize: '0.65rem' }}>
          <GiRollingDices style={{ fontSize: '0.85rem' }} />
          <span>连射 · {data.weapon}</span>
          <span className="ml-auto" style={{ color: 'var(--color-text-primary)' }}>
            {data.shots.length}发 · 命中{hits}{totalDmg > 0 ? ` · 合计${totalDmg}伤` : ''}
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          {data.shots.map((s, i) => (
            <div key={i} className="flex items-center gap-2 text-xs" style={{ opacity: s.hit ? 1 : 0.6 }}>
              <span className="flex-shrink-0" style={{ color: 'var(--color-text-secondary)', width: '2.6rem' }}>第{i + 1}发</span>
              <span className="truncate" style={{ color: 'var(--color-text-primary)', minWidth: '4rem' }}>{s.target}</span>
              {s.gone ? (
                <span style={{ color: 'var(--color-text-secondary)' }}>目标已倒下</span>
              ) : (
                <>
                  <span className="font-mono" style={{ color: diceAccent(s.outcome || '') }}>{s.roll}/{s.target_val}</span>
                  <span className="font-semibold" style={{ color: s.hit ? 'var(--color-danger)' : 'var(--color-text-secondary)' }}>
                    {s.hit ? '命中' : '未命中'}
                  </span>
                  {s.hit && s.damage != null && <span style={{ color: 'var(--color-danger)' }}>{s.damage}伤</span>}
                  {(s.flags || []).includes('贯穿') && <span className="font-semibold" style={{ color: 'var(--color-dice-gold)' }}>贯穿!</span>}
                  {s.penalty > 0 && <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.6rem' }}>换目标 -{s.penalty}</span>}
                </>
              )}
            </div>
          ))}
        </div>
        {ts && <div className="text-right mt-1" style={{ fontSize: '0.6rem', opacity: 0.5, color: 'var(--color-text-secondary)' }}>{ts}</div>}
      </div>
    </div>
  )
}
