/**
 * 嵌入布局的「我方 / 敌方」一列参战方卡。
 *
 * 只剩嵌入布局在用：沉浸布局已经改成「先攻序竖列 + 焦点面板」，敌我分列那套在那边
 * 是重复的——顺序在两列卡里根本看不出来（敌我各排各的）。这里保留，是因为聊天流里
 * 那张小卡片只有一条窄列，堆叠形态才对。
 */
import { CombatantCard } from './CombatantCard'
import type { Combatant, HpDiff } from './types'

export function SideColumn({ label, list, myCharId, turn, diffs, alignRight = false }: {
  label: string
  list: Combatant[]
  myCharId: string | null
  turn: string | null
  diffs: Record<string, HpDiff>
  alignRight?: boolean
}) {
  return (
    <>
      <div className={`combat-label${alignRight ? ' combat-label--right' : ''}`}>{label}</div>
      {list.length === 0
        ? <div className={`combat-side-empty${alignRight ? ' combat-label--right' : ''}`}>无</div>
        : (
          <div className="combat-side-list">
            {list.map((c) => (
              <CombatantCard
                key={c.id} c={c}
                mine={!!(myCharId && c.id === myCharId)}
                active={c.id === turn}
                diff={diffs[c.id]}
              />
            ))}
          </div>
        )}
    </>
  )
}
