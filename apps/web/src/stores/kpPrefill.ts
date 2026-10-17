/**
 * 监控列 → 工作台 的预填通道。
 *
 * 值班台把「要对谁做什么」贴在调查员卡上（检定 / SAN / 伤害 / 移动），点了要把动作与
 * 目标一起填进写作台。原型里监控列与操作台在同一条组件树里、一个回调就够了；正式页里
 * 监控列在左侧栏、写作台在底部坞，分属两棵组件树，改用一个微型全局 store 传递请求，
 * HumanKpPanel 收到后走 adoptToTools（填入表单、由 KP 过目后再发——裁定权仍在 KP 手里）。
 */
import { create } from 'zustand'

import type { KpAction } from '@/components/game/kpActions'

export interface KpPrefillRequest {
  action: KpAction
  fields: Record<string, string>
  nonce: string
}

interface KpPrefillState {
  request: KpPrefillRequest | null
  ask: (action: KpAction, fields: Record<string, string>) => void
  consume: () => void
  /** 待办台（KpDesk）的刷新信号：玩家那一侧投了骰、申请了检定，房间流在页面上，
   *  待办台在写作台里，同样是两棵组件树——借这个 store 把「该重拉了」递过去。 */
  deskTick: number
  bumpDesk: () => void
}

export const useKpPrefill = create<KpPrefillState>((set) => ({
  request: null,
  ask: (action, fields) => set({
    request: { action, fields, nonce: `${Date.now()}-${Math.random().toString(36).slice(2)}` },
  }),
  consume: () => set({ request: null }),
  deskTick: 0,
  bumpDesk: () => set((s) => ({ deskTick: s.deskTick + 1 })),
}))
