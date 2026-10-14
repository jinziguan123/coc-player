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

/** 这一桌最后一次有人交互的时间：后端给的 last_active_at，旧后端退回 updated_at */
export function lastActive(s: SessionSummary): string {
  return s.last_active_at ?? s.updated_at ?? ''
}

/**
 * 拱门里摆哪一桌：最后一次有人交互（发消息、掷骰…）的那桌。已开打的（进行中/暂停）优先于
 * 还在大厅等人的——「回到桌上」比「回大厅」更像是你此刻想做的事。
 *
 * 不按 updated_at 排：战斗态回写之类的后台写入会刷新它，只发消息时它又不动。
 */
export function pickFeatured(sessions: SessionSummary[]): SessionSummary | null {
  if (sessions.length === 0) return null
  const rank = (s: SessionSummary) => (s.status === 'setup' ? 1 : 0)
  return [...sessions].sort(
    (a, b) => rank(a) - rank(b) || lastActive(b).localeCompare(lastActive(a)),
  )[0]
}
