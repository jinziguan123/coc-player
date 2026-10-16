import { useState } from 'react'
import { toast } from 'sonner'
import { m } from 'motion/react'
import { GiCrossedSwords, GiRollingDices, GiRun } from 'react-icons/gi'
import { api } from '@/api/client'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { spring } from '@/lib/motion'
import type { ChaseState } from './ChasePanel'

const DIFFICULTY: Array<{ id: 'normal' | 'hard' | 'extreme'; label: string }> = [
  { id: 'normal', label: '普通' },
  { id: 'hard', label: '困难' },
  { id: 'extreme', label: '极难' },
]

/**
 * 真人 KP 值班台·追逐主台：开追逐即接管主台（与战斗的战术台同一套切换）。
 *
 * 一眼要回答的只有一件事：**离甩脱 / 被追上还差几步**。所以主体是一条距离轨——
 * 从「被追上」到「甩脱」逐格排开，逃方棋子站在当前距离上，距离用大字写出。
 * 每轮的操作收成一行：可选一项障碍（谁过、什么检定、难度）→ 推进一轮；
 * 也可以不掷骰直接判结局或中止。逐轮结算在右栏，最新的在上。
 */
export function KpChaseStage({ chase, sessionId, rounds }: {
  chase: ChaseState
  sessionId: string
  /** 逐轮结算行（追逐的 dice 事件内容），旧在前 */
  rounds: string[]
}) {
  const [busy, setBusy] = useState(false)
  const [who, setWho] = useState<'quarry' | 'pursuer'>('quarry')
  const [hzSkill, setHzSkill] = useState('')
  const [difficulty, setDifficulty] = useState<'normal' | 'hard' | 'extreme'>('normal')

  const lo = chase.caught_at
  const hi = chase.escape_at
  const span = Math.max(1, hi - lo)
  const pos = (g: number) => ((Math.min(hi, Math.max(lo, g)) - lo) / span) * 100
  const cells = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  const toEscape = hi - chase.gap
  const toCaught = chase.gap - lo

  const post = async (action: 'chase_round' | 'end_chase', payload: Record<string, unknown>, ok: string) => {
    if (busy) return
    setBusy(true)
    try {
      await api.post(`/sessions/${sessionId}/kp/action`, { action, payload })
      toast.success(ok)
      if (action === 'chase_round') setHzSkill('')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '追逐操作失败')
    } finally {
      setBusy(false)
    }
  }
  const advance = () => post(
    'chase_round',
    hzSkill.trim() ? { hazard: { who, skill: hzSkill.trim(), difficulty } } : {},
    '追逐推进了一轮',
  )

  return (
    <div className="kp-chase">
      <div className="kp-chase-main">
        <header className="kp-chase-head">
          <GiRun aria-hidden />
          <b>追逐 · 第 {chase.round} 轮</b>
          <span>{chase.quarry}（逃）←→ {chase.pursuer}（追）· 比拼「{chase.skill || '运动'}」</span>
          <span className="kp-chase-rule">甩脱 ≥{hi} · 被追上 ≤{lo}</span>
        </header>

        <div className="kp-chase-track" aria-label={`当前距离 ${chase.gap}`}>
          <div className="kp-chase-rail" />
          {cells.map((g) => (
            <div key={g} className="kp-chase-cell" style={{ left: `${pos(g)}%` }}>
              <span className={`kp-chase-node ${g === lo ? 'kp-chase-node--caught' : g === hi ? 'kp-chase-node--escape' : ''} ${g === chase.gap ? 'kp-chase-node--here' : ''}`} />
              <span className="kp-chase-label">{g === lo ? '被追上' : g === hi ? '甩脱' : g}</span>
            </div>
          ))}
          {/* 追方棋子钉在「被追上」那一端；逃方棋子随距离滑动 */}
          <span className="kp-chase-token kp-chase-token--pursuer" style={{ left: `${pos(lo)}%` }} title={chase.pursuer}>
            <GiCrossedSwords aria-hidden />
          </span>
          <m.span
            className="kp-chase-token kp-chase-token--quarry"
            initial={false}
            animate={{ left: `${pos(chase.gap)}%` }}
            transition={spring.pop}
            title={chase.quarry}
          >
            <GiRun aria-hidden />
          </m.span>
        </div>

        <div className="kp-chase-gap">
          <m.b key={chase.gap} initial={{ scale: 1.25, opacity: 0.4 }} animate={{ scale: 1, opacity: 1 }} transition={spring.pop}>
            {chase.gap}
          </m.b>
          <span>当前距离 · 再拉开 {Math.max(0, toEscape)} 即甩脱 · 再被拉近 {Math.max(0, toCaught)} 就被追上</span>
        </div>

        <div className="kp-chase-vs">
          <div><b>{chase.quarry} · 逃</b>{chase.quarry_mov != null && <>MOV {chase.quarry_mov}</>}</div>
          <span>VS</span>
          <div><b>{chase.pursuer} · 追</b>{chase.pursuer_mov != null && <>MOV {chase.pursuer_mov}</>}</div>
        </div>

        <div className="kp-chase-round">
          <label className="kp-chase-field">
            本轮障碍
            <input className="input" value={hzSkill} onChange={(e) => setHzSkill(e.target.value)} placeholder="检定技能，如 攀爬（可不填）" />
          </label>
          <div className="kp-chase-field">
            谁要过
            <span className="kp-chase-seg">
              {(['quarry', 'pursuer'] as const).map((w) => (
                <button key={w} type="button" aria-pressed={who === w} className={who === w ? 'on' : ''} onClick={() => setWho(w)}>
                  {w === 'quarry' ? '逃方' : '追方'}
                </button>
              ))}
            </span>
          </div>
          <div className="kp-chase-field">
            难度
            <span className="kp-chase-seg">
              {DIFFICULTY.map((d) => (
                <button key={d.id} type="button" aria-pressed={difficulty === d.id} className={difficulty === d.id ? 'on' : ''} onClick={() => setDifficulty(d.id)}>
                  {d.label}
                </button>
              ))}
            </span>
          </div>
          <span className="flex-1" />
          <button type="button" className="btn-primary inline-flex items-center gap-1" disabled={busy} onClick={() => void advance()}>
            <GiRollingDices aria-hidden /> 推进一轮
          </button>
          {([['escaped', '判为甩脱'], ['caught', '判为被追上'], ['manual', '中止追逐']] as const).map(([outcome, label]) => (
            <ConfirmDialog
              key={outcome}
              title={label}
              description={outcome === 'manual' ? '不分胜负地结束这场追逐？' : `不再掷骰，直接${label}？`}
              confirmLabel={label}
              onConfirm={() => void post('end_chase', { outcome }, '追逐已结束')}
            >
              {(open) => (
                <button type="button" className={`btn-secondary ${outcome === 'manual' ? 'kp-combat-strip-end' : ''}`} disabled={busy} onClick={open}>
                  {label}
                </button>
              )}
            </ConfirmDialog>
          ))}
        </div>
      </div>

      <aside className="kp-chase-log" aria-label="逐轮记录">
        <div className="kp-desk-label">逐轮记录</div>
        {rounds.length
          ? [...rounds].reverse().map((r, i) => (
            <div key={rounds.length - i} className="kp-chase-log-row"><b>R{rounds.length - i}</b> {r}</div>
          ))
          : <div className="kp-chase-log-row kp-chase-log-row--idle">还没掷过。障碍可以不填，直接「推进一轮」。</div>}
      </aside>
    </div>
  )
}
