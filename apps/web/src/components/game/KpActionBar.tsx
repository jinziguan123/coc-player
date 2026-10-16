import { m } from 'motion/react'
import { MoreMenu } from '@/components/ui/more-menu'
import { spring } from '@/lib/motion'
import { ACTION_CHIPS, ACTION_GROUPS, ACTION_LABELS, ACTION_MORE, situationActions, type KpAction } from './kpActions'

const CHIP_BY_ID = new Map(ACTION_CHIPS.map((c) => [c.id, c]))

/**
 * 真人 KP 写作台的动作条：说 / 骰 / 场景 / 局势四组直达排成一行，其余收进「更多」。
 *
 * 当前动作的黄铜底是同一个 layoutId，在按钮之间**滑过去**而不是这里灭那里亮。
 * 从「更多」里选中的动作会临时出现在条尾（带同一块黄铜底）——KP 得看得见自己正在用哪个。
 */
export function KpActionBar({ action, onPick, inCombat = false }: {
  action: KpAction
  onPick: (id: KpAction) => void
  /** 战斗中：「局势」组给「结束战斗」而不是「开战」 */
  inCombat?: boolean
}) {
  const chip = (id: KpAction) => {
    const c = CHIP_BY_ID.get(id)
    if (!c) return null
    const Icon = c.icon
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
        <span>{c.label}</span>
      </button>
    )
  }
  const fromMore = ACTION_MORE.includes(action)

  return (
    <div className="kp-action-bar" role="toolbar" aria-label="KP 动作">
      {ACTION_GROUPS.map((group) => (
        <div key={group.label} className="kp-action-group" role="group" aria-label={group.label}>
          <span className="kp-action-group-label" aria-hidden>{group.label}</span>
          <div className="kp-action-group-chips">
            {situationActions(group.ids, inCombat).map(chip)}
          </div>
        </div>
      ))}
      {fromMore && <div className="kp-action-group-chips">{chip(action)}</div>}
      <MoreMenu
        label="更多 KP 动作"
        triggerClassName="kp-action-more"
        trigger={<>更多 · {ACTION_MORE.length}</>}
        items={ACTION_MORE.map((id) => {
          const c = CHIP_BY_ID.get(id)!
          const Icon = c.icon
          return { label: ACTION_LABELS[id], icon: <Icon size={14} />, onClick: () => onPick(id) }
        })}
      />
    </div>
  )
}
