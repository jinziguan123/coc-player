import { useState } from 'react'
import { toast } from 'sonner'

import { api } from '@/api/client'
import type { ChatMessage } from '@/stores/sessionStore'
import type { PartyVital } from '@/features/game-session/usePartyVitals'

import {
  CombatStage,
  type CombatLogEntry,
  type CombatResultView,
  type CombatState,
  type PendingReaction,
} from './CombatStage'
import { KpCombatLedger } from './KpCombatLedger'
import { FigurePickerPopover } from './combat/FigurePickerPopover'
import type { FigureKey } from './combat/figures'
import type { Combatant } from './combat/types'
import { KpCombatRoster } from './KpCombatRoster'

interface KpCombatSpreadProps {
  sessionId: string
  combat: CombatState
  messages: ChatMessage[]
  combatLogSince: number | null
  party: PartyVital[]
  myCharId: string | null
  pendingReaction?: PendingReaction | null
  log: CombatLogEntry[]
  result?: CombatResultView | null
  myWeapons?: { name: string; dam?: string }[]
  battleMap?: { url: string; level: string } | null
  turnState?: Record<string, unknown> | null
  sanDanger: boolean
  onCheck: (actorName: string, content: string) => void
  onReply: (actorName: string) => void
  onOpenWriter: () => void
  onChanged: () => void
  onLocateActor: (name: string) => void
  locate?: { name: string; nonce: number } | null
  onToggleLayout: () => void
}

function pendingCheckCount(turnState?: Record<string, unknown> | null): number {
  const pending = turnState?.pending_checks
  return pending && typeof pending === 'object' ? Object.keys(pending as Record<string, unknown>).length : 0
}

/**
 * 真人 KP 的战斗观局台：左先攻梯 · 中棋盘 · 中缝轮次 · 右桌边账页。
 *
 * 主页面只负责接线（数据、动作、开关），对开双页的摆法收在这里，避免主页面继续变高。
 * 棋盘仍是原 CombatStage 的 immersive 布局，只由 CSS 去掉内部先攻列与战斗日志抽屉。
 */
export function KpCombatSpread({
  sessionId, combat, messages, combatLogSince, party, myCharId,
  pendingReaction, log, result, myWeapons, battleMap, turnState, sanDanger,
  onCheck, onReply, onOpenWriter, onChanged, onLocateActor, locate, onToggleLayout,
}: KpCombatSpreadProps) {
  const [figureFor, setFigureFor] = useState<Combatant | null>(null)

  const setToken = async (target: Combatant, figure: FigureKey) => {
    try {
      await api.post('/sessions/' + sessionId + '/kp/action', {
        action: 'set_token', payload: { combatant_id: target.id, figure },
      })
      toast.success('战棋轮廓已同步全桌')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '换轮廓失败')
    }
  }

  return (
    <div className="kp-spread flex-1 min-w-0 min-h-0">
      <KpCombatRoster
        combat={combat}
        party={party}
        pendingChecks={pendingCheckCount(turnState)}
        sanDanger={sanDanger}
        onLocate={onLocateActor}
        onPickFigure={setFigureFor}
      />
      <div className="kp-spread-board battle-stage-pane flex-1 flex flex-col min-w-0 min-h-0">
        <CombatStage
          combat={combat}
          myCharId={myCharId}
          sessionId={sessionId}
          pendingReaction={pendingReaction}
          log={log}
          result={result}
          myWeapons={myWeapons}
          battleMap={battleMap}
          canSetToken
          layout="immersive"
          onToggleLayout={onToggleLayout}
        />
      </div>
      {/* 轮次尺目前只标注本场当前轮：聊天消息侧还没有逐条的 round 元数据，
          先不做假的翻页；等战斗日志带上 round 再升级成可回看的轮次尺。 */}
      {figureFor && (
        <div className="kp-figure-pop">
          <FigurePickerPopover
            c={figureFor}
            onClose={() => setFigureFor(null)}
            onPick={(key) => { void setToken(figureFor, key); setFigureFor(null) }}
          />
        </div>
      )}
      <div className="kp-spread-gutter" aria-hidden="true">
        <span className="kp-spread-gutter-label">本 场 记 录</span>
        <span className="kp-spread-gutter-no">{'R' + String(combat.round).padStart(2, '0')}</span>
        <span className="kp-spread-gutter-sub">{'第 ' + combat.round + ' 轮'}</span>
        <span className="kp-spread-gutter-rule" />
        <span className="kp-spread-gutter-hint">桌边</span>
      </div>
      <KpCombatLedger
        sessionId={sessionId}
        messages={messages}
        combat={combat}
        combatLogSince={combatLogSince}
        party={party}
        onCheck={onCheck}
        onReply={onReply}
        onOpenWriter={onOpenWriter}
        onChanged={onChanged}
        locate={locate}
      />
    </div>
  )
}
