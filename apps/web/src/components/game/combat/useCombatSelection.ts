/**
 * 战斗里的「选中」只有一份。
 *
 * 焦点面板显示谁、棋盘准星瞄谁、目标下拉选谁——玩家心里这是一件事，代码里却曾分成三个
 * 写入点：选目标只写 targetId，面板便停在上一张脸上（点第二个敌人，准星和下拉都换了，
 * 右边显示的仍是第一个）。收成一个 hook，谁改都改同一处。
 */
import { useEffect, useState } from 'react'
import { ACTIONS, type ActionKey } from './meta'
import type { Combatant } from './types'

export function useCombatSelection(
  order: Combatant[],
  turn: string | null,
  enemies: Combatant[],
  woundedAllies: Combatant[],
) {
  const [action, setAction] = useState<ActionKey>('attack')
  const [targetId, setTargetId] = useState('')
  const [pick, setPick] = useState('')   // 显式选中的单位；空＝跟随当前行动者
  const active = order.find((c) => c.id === turn) || null

  useEffect(() => { setPick('') }, [turn])   // 回合一换，焦点回到当前行动者

  const focus = order.find((c) => c.id === pick) || active
  const focusId = focus?.id ?? ''

  // 当前动作的目标候选（按 target 类型）。
  const candidatesOf = (k: ActionKey): Combatant[] =>
    ACTIONS[k].target === 'enemy' ? enemies : ACTIONS[k].target === 'ally' ? woundedAllies : []
  const candidates = candidatesOf(action)
  // 目标失效（阵亡 / 换了候选）时回落到第一个候选——下拉里显示的永远是这一条。
  const effectiveTarget = candidates.some((c) => c.id === targetId) ? targetId : (candidates[0]?.id ?? '')

  /** 点先攻序或棋子：切焦点看人，同一个人再点一次收回；不动目标。 */
  const setFocusId = (id: string) => setPick(id === focusId ? '' : id)
  /** 选目标＝选焦点：面板、准星、下拉从此不会各说各话。 */
  const selectTarget = (id: string) => { setTargetId(id); setPick(id) }
  /** 点棋盘上的敌人：切到攻击动作，目标与焦点一起挪过去。 */
  const attackTarget = (id: string) => { setAction('attack'); selectTarget(id) }
  /** 换动作会换一整套候选：目标随之改变时焦点也得跟过去（攻击→急救即是一例）。 */
  const pickAction = (k: ActionKey) => {
    setAction(k)
    const list = candidatesOf(k)
    if (ACTIONS[k].target === 'none' || !list.length) return
    setPick(list.some((c) => c.id === targetId) ? targetId : list[0].id)
  }

  return { action, candidates, effectiveTarget, focus, focusId, setFocusId, selectTarget, attackTarget, pickAction }
}
