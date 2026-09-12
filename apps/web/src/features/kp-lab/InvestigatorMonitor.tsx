/**
 * 监控列：调查员的生命体征 + 就近的干预入口。
 *
 * 「监控视角」在这里落成三条具体规矩：
 *  1. 状态常驻可见——HP/SAN/MP 不进标签页、不进弹窗，扫一眼就在；
 *  2. 异常自己浮上来——SAN 见底时卡片本身变样，不指望 KP 逐个去看数字；
 *  3. 干预手段贴着状态放——要对谁发检定，手就落在他那张卡上，省掉「先选人」。
 */
import { GameVitalBar } from '@/components/character/GameVitalBar'
import { SeatIcon } from '@/components/game/SeatIcon'

import type { LabActor } from './useLabData'

const OUT_STATUS = new Set(['dead', 'dying', 'unconscious', 'fled'])

/** 席位颜色：与战场令牌同源，同一个人在两处是同一个色。 */
function sigilColor(a: LabActor): string {
  if (OUT_STATUS.has(a.status)) return 'var(--color-text-secondary)'
  return a.kind === 'ai' ? '#8fa7b5' : 'var(--color-accent)'
}

/** SAN 占比决定卡片的「不对劲」程度。阈值与 GameVitalBar 的告警线（30%）对齐，
 *  中间再加一档 50%——心智动摇和濒临崩溃对 KP 是两件事，该看得出区别。 */
function strainClass(a: LabActor): string {
  if (OUT_STATUS.has(a.status)) return 'kl-mon--out'
  const san = a.san
  if (!san || san.max <= 0) return ''
  const pct = (san.current / san.max) * 100
  if (pct < 30) return 'kl-mon--breaking'
  if (pct < 50) return 'kl-mon--strained'
  return ''
}

export interface ActorAction {
  key: 'check' | 'san' | 'damage' | 'move'
  label: string
}

const ACTOR_ACTIONS: ActorAction[] = [
  { key: 'check', label: '检定' },
  { key: 'san', label: 'SAN' },
  { key: 'damage', label: '伤害' },
  { key: 'move', label: '移动' },
]

export function InvestigatorMonitor({ actors, onAct }: {
  actors: LabActor[]
  onAct: (actor: LabActor, action: ActorAction) => void
}) {
  if (!actors.length) {
    return <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>这一局还没有调查员入座。</p>
  }
  return (
    <div className="flex flex-col gap-1.5">
      {actors.map((a) => (
        <div key={a.id} className={`kl-mon ${strainClass(a)}`}>
          <div className="kl-mon-head">
            <span className="kl-mon-sigil" style={{ color: sigilColor(a) }}>{a.name.slice(0, 1)}</span>
            <span className="min-w-0 flex-1">
              <span className="kl-mon-name block truncate">{a.name}</span>
              {a.occupation && <span className="kl-mon-occ">{a.occupation}</span>}
            </span>
            <SeatIcon kind={a.kind} size={13} />
          </div>

          {a.hp && <GameVitalBar type="hp" current={a.hp.current} max={a.hp.max} />}
          {a.san && <GameVitalBar type="san" current={a.san.current} max={a.san.max} />}
          {a.mp && <GameVitalBar type="mp" current={a.mp.current} max={a.mp.max} />}

          <div className="kl-mon-acts">
            {ACTOR_ACTIONS.map((act) => (
              <button key={act.key} className="kl-act" onClick={() => onAct(a, act)}>
                {act.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
