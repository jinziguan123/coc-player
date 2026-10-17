import { useEffect, useState } from 'react'

import { api } from '@/api/client'

export interface KpDeskPendingCheck {
  id: string
  kind: string
  skill: string
  difficulty: string
  actor_name: string
  reason: string
  group: boolean
}

export interface KpDeskCheckRequest {
  id: string
  actor_id: string
  actor_name: string
  skill: string
  intent: string
}

export interface KpDeskData {
  pending_checks: KpDeskPendingCheck[]
  check_requests: KpDeskCheckRequest[]
  ledger: unknown[]
}

/**
 * 真人 KP 待办台的取数口。
 *
 * KpDesk（写作台里的待办）与 KpCombatLedger（战斗账页）都需要这一份数据；
 * 之前只在 KpDesk 里内联，观局台要再用一份就会有两套容错与轮询口径。
 * tick 由调用方拼（面板动作、房间事件、本地重试各加一个），只用来触发重拉。
 */
export function useKpDesk(sessionId: string, tick: number): KpDeskData | null {
  const [desk, setDesk] = useState<KpDeskData | null>(null)
  useEffect(() => {
    let alive = true
    void api.get<KpDeskData>('/sessions/' + sessionId + '/kp/desk')
      .then((data) => { if (alive) setDesk(data) })
      .catch(() => { /* 待办台拉不到不挡观局台 */ })
    return () => { alive = false }
  }, [sessionId, tick])
  return desk
}
