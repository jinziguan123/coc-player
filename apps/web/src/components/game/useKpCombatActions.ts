import { useCallback, useState } from 'react'
import { toast } from 'sonner'

import type { PartyVital } from '@/features/game-session/usePartyVitals'
import { useKpPrefill } from '@/stores/kpPrefill'

import type { CombatState } from './combat/types'
import { replyDraft } from './kpLedgerModel'

interface KpCombatActionsOptions {
  combat: CombatState | null
  party: PartyVital[]
  setKpFullDesk: (value: boolean) => void
  setKpCollapsed: (value: boolean) => void
  /** 让页面重拉会话与角色体征 */
  onRefresh: () => void
}

/**
 * 观局台账页与底条之间的动作桥。
 *
 * 账页在右页、底条横贯全页、写作台在底部坞，三者分属不同组件树；把这几个跨树的
 * 动作（发检定 / 场外回复 / 定位 / 刷新）收在这里，GameSessionPage 只留接线，
 * 也避免主页面继续长高（见 tests/fileSize）。
 */
export function useKpCombatActions({
  combat, party, setKpFullDesk, setKpCollapsed, onRefresh,
}: KpCombatActionsOptions) {
  const [draft, setDraft] = useState('')
  const [focusToken, setFocusToken] = useState(0)
  const [locateRequest, setLocateRequest] = useState<{ name: string; nonce: number } | null>(null)

  const openCheck = useCallback((actorName: string, content: string) => {
    const member = party.find((item) => item.name === actorName)
    const combatant = combat?.order.find((item) => item.name === actorName)
    const charId = member?.id ?? (combatant && combatant.side !== 'enemy' ? combatant.id : null)
    if (!charId) {
      toast.error('这名角色不在调查员席上，无法直接发起检定')
      return
    }
    useKpPrefill.getState().ask('dice_check', {
      char: 'character:' + charId,
      reason: content.slice(0, 120),
    })
    setKpCollapsed(false)
    setKpFullDesk(true)
    toast.success('已填入检定表单，选好技能后发布')
  }, [combat, party, setKpCollapsed, setKpFullDesk])

  const reply = useCallback((actorName: string) => {
    setDraft(replyDraft(actorName))
    setFocusToken((tick) => tick + 1)
    setKpCollapsed(false)
    setKpFullDesk(false)
    toast.success('已把场外回复填进底条，补完后回车发布')
  }, [setKpCollapsed, setKpFullDesk])

  const refresh = useCallback(() => {
    onRefresh()
    useKpPrefill.getState().bumpDesk()
  }, [onRefresh])

  const locate = useCallback((name: string) => {
    setLocateRequest({ name, nonce: Date.now() })
  }, [])

  return { draft, setDraft, focusToken, openCheck, reply, refresh, locate, locateRequest }
}
