import { GiCrossedSwords, GiDramaMasks, GiHourglass, GiSkullCrossedBones } from 'react-icons/gi'

import type { PartyVital } from '@/features/game-session/usePartyVitals'

import { STATUS_META, conditionBadges, isOut } from './combat/meta'
import type { Combatant, CombatState, CombatStatus } from './combat/types'

interface KpCombatRosterProps {
  combat: CombatState
  party: PartyVital[]
  /** 账页待办区的条目数（待投检定 + 玩家申请） */
  pendingChecks: number
  sanDanger: boolean
  onLocate: (actorName: string) => void
  onPickFigure?: (actor: Combatant) => void
}

function pct(current: number, max: number): number {
  if (max <= 0) return 0
  return Math.max(0, Math.min(100, (current / max) * 100))
}

/**
 * 观局台左缘：先攻梯兼队况。
 *
 * 和玩家侧的 InitiativeRail 不同：KP 这里要同时看到「谁在场上、谁快没了、谁在等」，
 * 所以顺序、HP、SAN、状态、当前/下一个并成一列；敌人也入列。点击行把右页账页滚到
 * 这个人最近的一条发言——两页共用一个焦点，而不是让 KP 自己找。
 */
export function KpCombatRoster({ combat, party, pendingChecks, sanDanger, onLocate, onPickFigure }: KpCombatRosterProps) {
  const turnIdx = combat.order.findIndex((item) => item.id === combat.turn)
  const active = turnIdx >= 0 ? combat.order[turnIdx] : null
  let nextId: string | null = null
  if (turnIdx >= 0) {
    for (let step = 1; step <= combat.order.length; step++) {
      const candidate = combat.order[(turnIdx + step) % combat.order.length]
      if (!isOut(candidate)) { nextId = candidate.id; break }
    }
  }

  return (
    <aside className="kp-roster" aria-label="先攻与队况">
      <header className="kp-roster-head">
        <div className="kp-roster-head-main">
          <span className="kp-roster-kicker">先攻 · 第 {combat.round} 轮</span>
          <span className="kp-roster-turn">
            <GiHourglass size={11} aria-hidden />
            {active ? <>轮到 <b>{active.name}</b></> : '本轮尚未开始'}
          </span>
        </div>
        <span className="kp-roster-count">{combat.order.length} 席</span>
      </header>

      <ol className="kp-roster-list">
        {combat.order.map((actor, index) => {
          const member = party.find((item) => item.id === actor.id || item.name === actor.name)
          const isActive = actor.id === combat.turn
          const isNext = actor.id === nextId && !isActive
          const passed = turnIdx >= 0 && index < turnIdx && !isActive
          const out = isOut(actor)
          const status = actor.status !== 'ok'
            ? STATUS_META[actor.status as Exclude<CombatStatus, 'ok'>]
            : null
          const badges = conditionBadges(actor)
          const sideLabel = actor.side === 'enemy' ? '敌方' : actor.side === 'ally' ? '友方' : member?.isAi ? 'AI' : actor.is_human ? '玩家' : 'NPC'
          return (
            <li key={actor.id} className="kp-roster-item">
              <button
                type="button"
                className="kp-roster-row"
                data-side={actor.side}
                data-state={isActive ? 'active' : isNext ? 'next' : out ? 'out' : passed ? 'passed' : 'idle'}
                onClick={() => onLocate(actor.name)}
                title={'定位 ' + actor.name + ' 在账页里的发言'}
              >
                <span className="kp-roster-no">{index + 1}</span>
                <span className="kp-roster-main">
                  <span className="kp-roster-name-row">
                    <b className="kp-roster-name">{actor.name}</b>
                    <span className={'kp-roster-tag kp-roster-tag--' + actor.side}>{sideLabel}</span>
                    {status && <span className="kp-roster-tag kp-roster-tag--danger">{status.label}</span>}
                    {isActive && <span className="kp-roster-now">当前</span>}
                    {isNext && <span className="kp-roster-next">下一个</span>}
                  </span>
                  <span className="kp-roster-vitals">
                    <span className="kp-roster-vital kp-roster-vital--hp">
                      <span className="kp-roster-vital-label"><em>HP</em><b>{actor.hp}/{actor.max_hp}</b></span>
                      <i style={{ width: pct(actor.hp, actor.max_hp) + '%' }} />
                    </span>
                    {member?.san && (
                      <span className="kp-roster-vital kp-roster-vital--san">
                        <span className="kp-roster-vital-label"><em>SAN</em><b>{member.san.current}/{member.san.max}</b></span>
                        <i style={{ width: pct(member.san.current, member.san.max) + '%' }} />
                      </span>
                    )}
                    {!member?.san && (
                      <span className="kp-roster-vital-note">
                        {actor.side === 'enemy' ? '护甲 ' + (actor.armor ?? 0) : '跟随回合'}
                      </span>
                    )}
                  </span>
                  {badges.length > 0 && (
                    <span className="kp-roster-conditions">
                      {badges.map((badge) => (
                        <span key={badge.key} className="kp-roster-condition" title={badge.label}>
                          <badge.Icon size={9} aria-hidden />
                          {badge.label}
                        </span>
                      ))}
                    </span>
                  )}
                </span>
              </button>
              {onPickFigure && (
                <button
                  type="button"
                  className="kp-roster-figure"
                  onClick={() => onPickFigure(actor)}
                  title={'换 ' + actor.name + ' 的战棋轮廓'}
                  aria-label={'换 ' + actor.name + ' 的战棋轮廓'}
                >
                  <GiDramaMasks size={11} aria-hidden />
                </button>
              )}
            </li>
          )
        })}
      </ol>

      <div className="kp-roster-signals" aria-label="本轮信号">
        <div className={pendingChecks > 0 ? 'kp-roster-signal kp-roster-signal--alert' : 'kp-roster-signal'}>
          <GiCrossedSwords size={11} aria-hidden />
          {pendingChecks > 0 ? pendingChecks + ' 条待办在账页' : '没有待处理的检定'}
        </div>
        {combat.awaiting_kp && (
          <div className="kp-roster-signal kp-roster-signal--alert">
            <GiSkullCrossedBones size={11} aria-hidden />
            敌方回合等你下令
          </div>
        )}
        {sanDanger && (
          <div className="kp-roster-signal kp-roster-signal--alert">
            <span className="kp-roster-dot" />
            有人理智濒临崩溃
          </div>
        )}
      </div>
      <p className="kp-roster-hint">点名字把右页账页滚到他的发言；血的读数来自本场战斗态与队伍体征。</p>
    </aside>
  )
}
