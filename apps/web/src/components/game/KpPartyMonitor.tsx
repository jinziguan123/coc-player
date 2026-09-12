/**
 * 监控列：全队生命体征 + 就近的干预入口。
 *
 * 「监控视角」在这里落成三条具体规矩：
 *  1. **状态常驻可见**——HP/SAN/MP 不进标签页、不进弹窗，KP 扫一眼就在；
 *  2. **异常自己浮上来**——SAN 见底时卡片本身变样，不指望 KP 逐个去读数字；
 *  3. **干预贴着状态放**——要对谁发检定，手就落在他那张卡上，省掉「先在表单里选人」。
 *
 * 第 3 条是这个组件真正的价值：点一下就把动作与目标一起填进工作台，KP 过目后再发出去
 * （沿用「采纳建议」那条路径，不是直接广播——裁定权仍在 KP 手里）。
 */
import { GameVitalBar } from '@/components/character/GameVitalBar'

import { SeatIcon } from './SeatIcon'
import type { KpAction } from './kpActions'
import type { PartyVital } from '@/features/game-session/usePartyVitals'

const OUT_STATUS = new Set(['dead', 'dying', 'unconscious', 'fled'])

/** 行内动作：点了不是立刻发出，而是把动作与目标预填进工作台。 */
const ACTS: { key: string; label: string; fill: (p: PartyVital) => [KpAction, Record<string, string>] }[] = [
  { key: 'check', label: '检定', fill: (p) => ['dice_check', { char: `character:${p.id}` }] },
  { key: 'san', label: 'SAN', fill: (p) => ['san_check', { targets: p.name, loss: '1/1d6' }] },
  { key: 'hp', label: '伤害', fill: (p) => ['hp_change', { target: p.name, delta: '-' }] },
  { key: 'move', label: '移动', fill: (p) => ['move_actor', { character_id: p.id }] },
]

/** SAN 占比决定卡片的「不对劲」程度。阈值与 GameVitalBar 的告警线（30%）对齐，
 *  中间再加一档 50%——心智动摇和濒临崩溃对 KP 是两件事，该看得出区别。 */
function strainClass(p: PartyVital): string {
  if (OUT_STATUS.has(p.status)) return 'kp-mon--out'
  const san = p.san
  if (!san || san.max <= 0) return ''
  const pct = (san.current / san.max) * 100
  if (pct < 30) return 'kp-mon--breaking'
  if (pct < 50) return 'kp-mon--strained'
  return ''
}

export function KpPartyMonitor({ party, onPrefill }: {
  party: PartyVital[]
  onPrefill: (action: KpAction, fields: Record<string, string>) => void
}) {
  if (!party.length) return null
  return (
    <div className="kp-monitor">
      <div className="kp-monitor-label">调查员</div>
      {party.map((p) => (
        <div key={p.id} className={`kp-mon ${strainClass(p)}`}>
          <div className="kp-mon-head">
            <span className="kp-mon-sigil">{p.name.slice(0, 1)}</span>
            <span className="min-w-0 flex-1">
              <span className="kp-mon-name block truncate">{p.name}</span>
              {p.occupation && <span className="kp-mon-occ">{p.occupation}</span>}
            </span>
            <SeatIcon kind={p.isAi ? 'ai' : 'human'} size={12} />
          </div>
          {p.hp && <GameVitalBar type="hp" current={p.hp.current} max={p.hp.max} />}
          {p.san && <GameVitalBar type="san" current={p.san.current} max={p.san.max} />}
          {p.mp && <GameVitalBar type="mp" current={p.mp.current} max={p.mp.max} />}
          <div className="kp-mon-acts">
            {ACTS.map((a) => (
              <button
                key={a.key}
                type="button"
                className="kp-mon-act"
                onClick={() => { const [action, fields] = a.fill(p); onPrefill(action, fields) }}
                title={`对 ${p.name} ${a.label}（填进工作台，确认后再发布）`}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
