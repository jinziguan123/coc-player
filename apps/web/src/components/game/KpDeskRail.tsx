import type { PartyVital } from '@/features/game-session/usePartyVitals'
import { useKpPrefill } from '@/stores/kpPrefill'

import { KpPartyMonitor } from './KpPartyMonitor'

interface KpDeskRailProps {
  party: PartyVital[]
  pendingCount: number
  inCombat: boolean
  turnName: string
  sanDanger: boolean
}

/** 真人 KP 值班台左列：全队体征 + 信号。叙事/追逐/窄屏旧路径使用；观局台由 KpCombatRoster 接替。 */
export function KpDeskRail({ party, pendingCount, inCombat, turnName, sanDanger }: KpDeskRailProps) {
  return (
    <aside className="kp-desk-rail" aria-label="值班台监控">
      <section className="kp-desk-section">
        <KpPartyMonitor
          party={party}
          onPrefill={(action, fields) => useKpPrefill.getState().ask(action, fields)}
        />
      </section>
      <section className="kp-desk-section">
        <div className="kp-desk-label">信号</div>
        <div className="flex flex-col gap-1">
          <div className={pendingCount ? 'kp-signal kp-signal--alert' : 'kp-signal'}>
            <span className="kp-dot" />
            {pendingCount ? pendingCount + ' 人还没交这一回合' : '本回合没有待办'}
          </div>
          <div className={inCombat ? 'kp-signal kp-signal--alert' : 'kp-signal'}>
            <span className="kp-dot" />
            {inCombat ? '战斗中 · 轮到 ' + (turnName || '—') : '未开战'}
          </div>
          {sanDanger && (
            <div className="kp-signal kp-signal--alert">
              <span className="kp-dot" />有人理智濒临崩溃
            </div>
          )}
        </div>
      </section>
    </aside>
  )
}
