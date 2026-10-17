import type { KnownLocation } from '@/features/game-session/useSessionResources'
import type { CombatLogEntry, CombatState } from './CombatStage'
import { HumanKpPanel } from './HumanKpPanel'
import { KpCombatStrip } from './KpCombatStrip'
import { KpEnemyControl } from './KpEnemyControl'
import { KpSceneBrief } from './KpSceneBrief'

interface KpConsoleDockProps {
  isKp: boolean
  combat: CombatState | null
  sessionId: string
  combatLog: CombatLogEntry[]
  kpCollapsed: boolean
  kpFullDesk: boolean
  desk: boolean
  immersiveOn: boolean
  inChase: boolean
  turnReady: boolean
  locations: KnownLocation[]
  stripText: string
  stripFocusToken: number
  hideStripLog?: boolean
  onStripTextChange: (value: string) => void
  onExpandStrip: () => void
  onCloseFullDesk: () => void
  onCollapseConsole: () => void
}

/**
 * KP 底部坞：敌方控制条 + 战术底条 / 完整写作台。
 *
 * 从 GameSessionPage 的渲染里拆出来：这一段只读状态与回调，不碰页面的数据流。
 * 观局台的账页负责「看」，这里仍是「写」的那一侧；两边通过受控草稿相连。
 */
export function KpConsoleDock({
  isKp, combat, sessionId, combatLog, kpCollapsed, kpFullDesk, desk, immersiveOn,
  inChase, turnReady, locations, stripText, stripFocusToken, hideStripLog = false, onStripTextChange,
  onExpandStrip, onCloseFullDesk, onCollapseConsole,
}: KpConsoleDockProps) {
  if (!isKp) return null
  return (
    <>
      {combat && <KpEnemyControl sessionId={sessionId} combat={combat} />}
      {!kpCollapsed && desk && immersiveOn && !kpFullDesk && (
        <aside className="kp-console-dock kp-console-dock--strip flex-shrink-0">
          <KpCombatStrip
            sessionId={sessionId}
            log={combatLog}
            onExpand={onExpandStrip}
            text={stripText}
            onTextChange={onStripTextChange}
            focusToken={stripFocusToken}
            hideLog={hideStripLog}
          />
        </aside>
      )}
      {!kpCollapsed && !(desk && immersiveOn && !kpFullDesk) && (
        <aside className={'kp-console-dock flex-shrink-0' + (desk ? ' kp-console-dock--lean' : '')}>
          <HumanKpPanel
            sessionId={sessionId}
            turnReady={turnReady}
            variant="sidebar"
            onCollapse={() => (desk && immersiveOn ? onCloseFullDesk() : onCollapseConsole())}
            hideSituation={desk}
            inCombat={!!combat}
            inChase={inChase}
            aside={desk ? undefined : <KpSceneBrief places={locations} />}
          />
        </aside>
      )}
    </>
  )
}
