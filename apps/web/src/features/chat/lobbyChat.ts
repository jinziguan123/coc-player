// 大厅聊天记录：按事件序号合并、去重，断线重连后用 after_seq 补漏。
import { useCallback, useRef, useState } from 'react'
import { api } from '@/api/client'

/** 大厅聊天最多留这么多条：房间挂一晚上，DOM 不能跟着无限涨。 */
export const MAX_CHAT_LINES = 200
/** 一次补漏最多翻几页，防止极端情况下无限请求。 */
const MAX_CATCHUP_PAGES = 5

export interface ChatLine { id: string; seq: number; name: string; content: string }

interface EventRow { id: string; sequence_num: number; actor_name: string; content: string }

/** 把新到的条目并进已有记录：按 id 去重、按序号排序、只留最后 max 条。 */
export function mergeChat(prev: ChatLine[], incoming: ChatLine[], max = MAX_CHAT_LINES): ChatLine[] {
  const fresh = incoming.filter((x) => !prev.some((p) => p.id === x.id))
  if (fresh.length === 0) return prev
  return [...prev, ...fresh].sort((a, b) => a.seq - b.seq).slice(-max)
}

const toLine = (e: EventRow): ChatLine => ({
  id: e.id, seq: e.sequence_num, name: e.actor_name || '玩家', content: e.content,
})

/**
 * 大厅聊天的数据层。用法：先连 SSE，在每次（重）连上收到 `ready` 时调 `catchUp()`，
 * 收到 `ooc` 时调 `onOoc()`。先连后拉 + 按 id 去重，开连接与拉历史之间不会漏消息。
 */
export function useLobbyChat(sessionId: string | undefined) {
  const [lines, setLines] = useState<ChatLine[]>([])
  const [loaded, setLoaded] = useState(false)
  /** 已经确定拿全的最大序号：只由补漏结果推进，实时消息可能乱序，不拿来推进。 */
  const watermark = useRef<number | null>(null)

  const catchUp = useCallback(async () => {
    if (!sessionId) return
    const base = `/sessions/${sessionId}/events?types=ooc&limit=${MAX_CHAT_LINES}`
    try {
      if (watermark.current == null) {
        const r = await api.get<{ events: EventRow[] }>(base)
        const got = r.events.map(toLine)
        setLines((prev) => mergeChat(prev, got))
        watermark.current = got.length ? got[got.length - 1].seq : 0
      } else {
        for (let page = 0; page < MAX_CATCHUP_PAGES; page++) {
          const r: { events: EventRow[]; has_more: boolean } = await api.get(
            `${base}&after_seq=${watermark.current}`)
          const got: ChatLine[] = r.events.map(toLine)
          if (got.length) {
            setLines((prev) => mergeChat(prev, got))
            watermark.current = got[got.length - 1].seq
          }
          if (!r.has_more) break
        }
      }
      setLoaded(true)
    } catch { /* 拉不到就等下一次重连再补 */ }
  }, [sessionId])

  const onOoc = useCallback((c: { id?: string | null; seq?: number | null; actor_name?: string | null; content?: string }) => {
    if (!c.id) return
    const line: ChatLine = {
      id: c.id, seq: c.seq ?? Number.MAX_SAFE_INTEGER, name: c.actor_name || '玩家', content: c.content || '',
    }
    setLines((prev) => mergeChat(prev, [line]))
  }, [])

  return { lines, loaded, catchUp, onOoc }
}
