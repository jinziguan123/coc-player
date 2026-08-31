import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { SessionSummary } from '@/features/game-setup/types'

/**
 * 首页要的那点库存：你手上有几位调查员、几个本子、哪几桌还开着。
 *
 * 拉取收在一处而不是各组件自取：卷宗抬头要计数、续玩卡要会话、新手介绍要靠「有没有
 * 调查员」决定摊不摊开——三处同一份数据，各拉一次就是三倍请求。
 */
export interface HomeInventory {
  characters: number
  modules: number
  /** 还开着的桌（进行中/暂停/大厅中），按后端返回的顺序 */
  openSessions: SessionSummary[]
}

/** 还开着 = 能点进去接着玩的。已收场的局不算，它们属于战报而不是工作台。 */
export function isOpen(status: string): boolean {
  return status === 'active' || status === 'paused' || status === 'setup'
}

/** 大厅中的局还没开打，该回房间等人；其余直接进桌。 */
export function sessionHref(session: SessionSummary): string {
  return session.status === 'setup' ? `/room/${session.id}` : `/game/${session.id}`
}

export function useHomeInventory(): HomeInventory | null {
  const [inventory, setInventory] = useState<HomeInventory | null>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [characters, modules, sessions] = await Promise.all([
          api.get<unknown[]>('/characters'),
          api.get<unknown[]>('/modules'),
          api.get<SessionSummary[]>('/sessions'),
        ])
        if (!alive) return
        setInventory({
          characters: characters.length,
          modules: modules.length,
          openSessions: sessions.filter((s) => isOpen(s.status)),
        })
      } catch {
        // 取不到就当没有：首页的入口与介绍照常可用，不为一行库存挡住整页
        if (alive) setInventory(null)
      }
    })()
    return () => { alive = false }
  }, [])

  return inventory
}
