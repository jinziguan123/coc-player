// 「正在输入」：收（按席位认人、多人并列、发言即清）与发（节流）。大厅与游戏页共用。
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/api/client'

/** 多久没再收到就当作停了。 */
export const TYPING_TTL_MS = 3000
/** 发送端节流：每这么久最多广播一次。 */
export const TYPING_THROTTLE_MS = 2000

export interface TypingSignal {
  actor_name?: string | null
  metadata?: { seat?: number | null } | null
}

/** 输入者的稳定键：有席位号按席位，没有（旧式本机会话）才退回名字。 */
export function typerKey(c: TypingSignal): string | null {
  const seat = c.metadata?.seat
  if (seat != null) return `seat:${seat}`
  return c.actor_name ? `name:${c.actor_name}` : null
}

/** 「甲 正在输入…」「甲、乙 正在输入…」「甲、乙 等 3 人正在输入…」；没人返回空串。 */
export function typingLabel(names: string[]): string {
  if (names.length === 0) return ''
  if (names.length <= 2) return `${names.join('、')} 正在输入…`
  return `${names.slice(0, 2).join('、')} 等 ${names.length} 人正在输入…`
}

/**
 * 收「正在输入」。`me` 用来排除自己：优先比席位号，没有席位号时比名字。
 * 收到某人的正式发言就立刻清掉他的提示，不用干等 3 秒。
 */
export function useTypers(me: { seat?: number | null; name?: string | null }) {
  const [typers, setTypers] = useState<Array<{ key: string; name: string }>>([])
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const meRef = useRef(me)
  meRef.current = me

  const drop = useCallback((key: string) => {
    const t = timers.current.get(key)
    if (t) clearTimeout(t)
    timers.current.delete(key)
    setTypers((prev) => (prev.some((x) => x.key === key) ? prev.filter((x) => x.key !== key) : prev))
  }, [])

  const onTyping = useCallback((c: TypingSignal) => {
    const key = typerKey(c)
    if (!key || !c.actor_name) return
    const { seat, name } = meRef.current
    if (seat != null ? c.metadata?.seat === seat : c.actor_name === name) return
    const old = timers.current.get(key)
    if (old) clearTimeout(old)
    timers.current.set(key, setTimeout(() => drop(key), TYPING_TTL_MS))
    const typerName = c.actor_name
    setTypers((prev) => {
      const i = prev.findIndex((x) => x.key === key)
      if (i < 0) return [...prev, { key, name: typerName }]
      if (prev[i].name === typerName) return prev
      return prev.map((x, j) => (j === i ? { key, name: typerName } : x))
    })
  }, [drop])

  /** 某人发了言：按名字清掉他的提示（消息事件不带席位号）。 */
  const onSpoke = useCallback((actorName?: string | null) => {
    if (!actorName) return
    setTypers((prev) => {
      const gone = prev.filter((x) => x.name === actorName)
      if (gone.length === 0) return prev
      for (const g of gone) {
        const t = timers.current.get(g.key)
        if (t) clearTimeout(t)
        timers.current.delete(g.key)
      }
      return prev.filter((x) => x.name !== actorName)
    })
  }, [])

  useEffect(() => {
    const map = timers.current
    return () => { map.forEach(clearTimeout); map.clear() }
  }, [])

  return { label: typingLabel(typers.map((t) => t.name)), onTyping, onSpoke }
}

/**
 * 发「正在输入」：节流到每 2 秒一次；只在内容变长（真在打字）时发，删字、清空不算。
 * 返回的函数在输入框 onChange 里调，传入当前全文。
 */
export function useTypingNotifier(sessionId: string | null | undefined, enabled: boolean) {
  const last = useRef(0)
  const prevLen = useRef(0)
  return useCallback((text: string) => {
    const grew = text.length > prevLen.current
    prevLen.current = text.length
    if (!sessionId || !enabled || !grew || !text.trim()) return
    const now = Date.now()
    if (now - last.current < TYPING_THROTTLE_MS) return
    last.current = now
    api.post(`/sessions/${sessionId}/typing`).catch(() => {})
  }, [sessionId, enabled])
}
