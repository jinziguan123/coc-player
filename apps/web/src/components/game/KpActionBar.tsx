import { m } from 'motion/react'
import { spring } from '@/lib/motion'
import { ACTION_CHIPS, ACTION_GROUPS, ACTION_LABELS, type KpAction } from './kpActions'

const CHIP_BY_ID = new Map(ACTION_CHIPS.map((c) => [c.id, c]))

/**
 * 真人 KP 写作台的动作条：四组（说 / 骰 / 场景 / 战斗），全部直达、一个不藏。
 *
 * 当前动作的黄铜底是同一个 layoutId，在按钮之间**滑过去**而不是这里灭那里亮——
 * KP 的眼睛能跟着它走，知道自己刚切到了哪儿。
 */
export function KpActionBar({ action, onPick }: { action: KpAction; onPick: (id: KpAction) => void }) {
  return (
    <div className="kp-action-bar" role="toolbar" aria-label="KP 动作">
      {ACTION_GROUPS.map((group) => (
        <div key={group.label} className="kp-action-group" role="group" aria-label={group.label}>
          <span className="kp-action-group-label" aria-hidden>{group.label}</span>
          <div className="kp-action-group-chips">
            {group.ids.map((id) => {
              const chip = CHIP_BY_ID.get(id)
              if (!chip) return null
              const Icon = chip.icon
              const on = action === id
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => onPick(id)}
                  aria-pressed={on}
                  title={ACTION_LABELS[id]}
                  className={`kp-quick-action ${on ? 'kp-quick-action--on' : ''}`}
                >
                  {on && <m.span layoutId="kp-action-active" className="kp-quick-action-bg" transition={spring.snappy} />}
                  <Icon size={14} />
                  <span>{chip.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
