/**
 * 焦点单位面板：场上被选中（缺省＝当前行动者）那一位的详情。
 *
 * 上一版把这块位置直接塞了玩家的行动栏，结果 KP 看到的是一片空白——KP 没有角色，
 * 行动栏对他永远不成立。而这块面板本该是**对谁都有内容**的：它回答「这个单位是什么状态」，
 * 至于「我能对他做什么」，那是它下面那一段的事，有就显示、没有就不显示。
 *
 * 焦点从先攻序或棋盘上点选切换，所以它也是「点谁看谁」的落点——参战方卡此前只能
 * 干看着，点了没有任何反应。
 */
import { GiCrossedSwords } from 'react-icons/gi'

import type { Combatant, HpDiff } from './types'
import { STATUS_META, conditionBadges, isOut, pctOf, sideColor } from './meta'
import { figureOf } from './figures'

export function FocusPanel({ c, isTurn, mine, diff, onChangeFigure, children }: {
  c: Combatant | null
  isTurn: boolean
  mine: boolean
  diff?: HpDiff
  /** KP 专属：给这个单位换战棋轮廓。非 KP 不传即不显示。 */
  onChangeFigure?: (c: Combatant) => void
  /** 「我能做什么」：玩家的行动栏 / 反应提示 / 等待提示，由 CombatStage 给。 */
  children?: React.ReactNode
}) {
  if (!c) {
    return (
      <div className="combat-focus">
        <div className="combat-label">当前</div>
        <p className="combat-focus-empty">场上没有可显示的单位。</p>
        {children}
      </div>
    )
  }
  const out = isOut(c)
  const col = sideColor(c)
  const sm = c.status !== 'ok' ? STATUS_META[c.status] : null
  const conds = conditionBadges(c)
  const Figure = figureOf(c)
  const dmg = diff && diff.delta < 0

  return (
    <div className="combat-focus">
      <div className="combat-label">{isTurn ? '正在行动' : '已选中'}</div>
      <div className={`combat-focus-card${dmg ? ' hp-hit' : ''}`} style={{ ['--side-color' as string]: col }}>
        <div className="combat-focus-head">
          <span className="combat-focus-fig" style={{ color: col }}><Figure size={22} /></span>
          <span className="min-w-0 flex-1">
            <span className="combat-focus-name" style={{ color: col }}>{c.name}</span>
            <span className="combat-focus-sub">
              {isTurn ? '正在行动' : out ? '已出局' : '待命'}
              {sm && ` · ${sm.label}`}
              {mine && ' · 你'}
            </span>
          </span>
        </div>

        <div className="combat-focus-hp">
          <span className="combat-focus-hp-num" style={{ color: col }}>{c.hp}</span>
          <span className="combat-focus-hp-max">/{c.max_hp} HP</span>
          <span className="combat-focus-hp-track">
            <span style={{ width: `${pctOf(c)}%`, background: col }} />
          </span>
        </div>

        <div className="combat-focus-meta">
          {c.weapon && <span><GiCrossedSwords size={11} /> {c.weapon}</span>}
          {!!c.armor && <span>护甲 {c.armor}</span>}
          {c.aim && <span>瞄准中</span>}
          {conds.map(({ key, label, Icon }) => <span key={key}><Icon size={11} /> {label}</span>)}
        </div>

        {onChangeFigure && (
          <button type="button" className="combat-focus-figbtn" onClick={() => onChangeFigure(c)}>
            <Figure size={13} /> 换轮廓
          </button>
        )}
      </div>
      {children}
    </div>
  )
}
