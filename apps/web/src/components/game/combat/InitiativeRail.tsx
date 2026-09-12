/**
 * 先攻序（竖排紧凑行）。
 *
 * 与 InitiativeTrack（横排芯片，嵌入布局在用）的区别不只是方向：**序列本来就是时间顺序，
 * 竖着读天然对**，而且一行放得下名字、HP 和「下一个」——横排芯片里这些一样都塞不进去，
 * 于是那条轨退化成了纯装饰，状态还得另看两列参战方卡。
 *
 * 刻意不复用 CombatantCard：那张卡带血条、武器、条件徽标，约 95px 高，四个人就吃掉
 * 四百像素——摆在侧列里只会把棋盘挤瘪。这里只留「谁、还剩多少、下一个是谁」，
 * 详情交给右边的焦点面板；点一行即把焦点切过去。
 */
import type { Combatant } from './types'
import { isOut, pctOf, sideColor } from './meta'

export function InitiativeRail({ order, turn, myCharId, focusId, onFocus }: {
  order: Combatant[]
  turn: string | null
  myCharId: string | null
  focusId: string
  onFocus: (id: string) => void
}) {
  const turnIdx = order.findIndex((c) => c.id === turn)
  // 「下一个」＝当前之后第一个未出局者（环形）。玩家最常问的一句话，标出来省得自己数。
  let nextIdx = -1
  if (turnIdx >= 0) {
    for (let k = 1; k <= order.length; k++) {
      const i = (turnIdx + k) % order.length
      if (!isOut(order[i])) { nextIdx = i; break }
    }
  }

  return (
    <div className="combat-rail">
      <div className="combat-label">先攻序</div>
      <div className="combat-rail-list chat-scroll">
        {order.map((c, i) => {
          const out = isOut(c)
          const active = c.id === turn
          const passed = turnIdx >= 0 && i < turnIdx && !active   // 本轮已走过者淡化
          const cls = ['combat-rail-row',
            active ? 'combat-rail-row--active' : '',
            focusId === c.id && !active ? 'combat-rail-row--focus' : '',
            out ? 'combat-rail-row--out' : passed ? 'combat-rail-row--passed' : '',
            myCharId && c.id === myCharId ? 'combat-rail-row--mine' : ''].join(' ')
          return (
            <button key={c.id} className={cls} onClick={() => onFocus(c.id)} title={`${c.name} · ${c.hp}/${c.max_hp}`}>
              <span className="combat-rail-dot" style={{ background: sideColor(c) }} />
              <span className="combat-rail-name">{c.name}</span>
              {i === nextIdx && <span className="combat-rail-next">下一个</span>}
              <span className="combat-rail-hp">{c.hp}/{c.max_hp}</span>
              {/* 血槽压在行底：不占行高，扫一眼就知道谁快没了 */}
              <span className="combat-rail-bar" style={{ width: `${pctOf(c)}%`, background: sideColor(c) }} />
            </button>
          )
        })}
      </div>
    </div>
  )
}
