/**
 * 原型页的数据源：全部只读（GET），一行写操作都没有。
 *
 * 原型页的存在意义是「先看布局对不对，再动主代码」，所以它宁可少一点真实度，
 * 也不能有任何可能改动正在跑的团的路径。所有按钮在页面里一律走 inert 提示。
 */
import { useCallback, useEffect, useState } from 'react'

import { api } from '@/api/client'
import type { CombatState } from '@/components/game/CombatStage'
import { seatKind, type SeatKind } from '@/components/game/SeatIcon'

interface Vital { current: number; max: number }

export interface LabActor {
  id: string
  name: string
  kind: SeatKind
  occupation: string
  status: string
  online: boolean
  hp?: Vital
  san?: Vital
  mp?: Vital
}

export interface LabEvent {
  id: string
  type: string
  content: string
  actor_name?: string
}

export interface LabPlace {
  id: string
  name: string
  current: boolean
  visited: boolean
  known?: boolean
  clues?: { id: string; name: string; status: string }[]
}

export interface LabSession {
  id: string
  module_title?: string
  current_scene_id: string | null
  kp_mode?: string
}

interface SeatPayload {
  character_id: string | null
  character_name?: string | null
  role: string
  is_host?: boolean
  is_mine?: boolean
  is_online?: boolean
}

interface CharacterPayload {
  id: string
  name: string
  status: string
  system_data: Record<string, unknown>
}

interface SyncPayload {
  systems: {
    combat?: { active?: boolean } & Record<string, unknown>
    turn?: { confirmed_ids: string[]; total: number; ready: boolean } | null
  }
}

export interface LabData {
  session: LabSession | null
  actors: LabActor[]
  events: LabEvent[]
  combat: CombatState | null
  turn: { confirmed_ids: string[]; total: number; ready: boolean } | null
  places: LabPlace[]
  sceneName: string
  loading: boolean
  error: string
  reload: () => void
}

/** 把一局的席位、角色数值、事件流、战斗态一次性拉齐。 */
export function useLabData(sessionId?: string): LabData {
  const [session, setSession] = useState<LabSession | null>(null)
  const [actors, setActors] = useState<LabActor[]>([])
  const [events, setEvents] = useState<LabEvent[]>([])
  const [combat, setCombat] = useState<CombatState | null>(null)
  const [turn, setTurn] = useState<LabData['turn']>(null)
  const [places, setPlaces] = useState<LabPlace[]>([])
  const [sceneName, setSceneName] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    if (!sessionId) { setLoading(false); return }
    let stale = false            // 与主页面同一条规矩：切存档时作废在途请求
    setLoading(true); setError('')

    const load = async () => {
      const s = await api.get<LabSession & { participants?: SeatPayload[] }>(`/sessions/${sessionId}`)
      if (stale) return
      setSession(s)

      const seats = (s.participants || []).filter((p) => p.character_id)
      const chars = await Promise.all(seats.map((p) =>
        api.get<CharacterPayload>(`/characters/${p.character_id}`).catch(() => null),
      ))
      if (stale) return
      setActors(seats.map((p, i) => {
        const c = chars[i]
        const sd = (c?.system_data || {}) as Record<string, unknown>
        return {
          id: p.character_id as string,
          name: c?.name || p.character_name || '未命名',
          kind: seatKind(p),
          occupation: String(sd.occupation || ''),
          status: c?.status || 'active',
          online: !!p.is_online,
          hp: sd.hitPoints as Vital | undefined,
          san: sd.sanity as Vital | undefined,
          mp: sd.magicPoints as Vital | undefined,
        }
      }))

      const [ev, sync, locs] = await Promise.all([
        api.get<{ events: { id: string; event_type: string; content: string; actor_name: string }[] }>(
          `/sessions/${sessionId}/events`).catch(() => ({ events: [] })),
        api.get<SyncPayload>(`/sessions/${sessionId}/sync`).catch(() => null),
        api.get<{ locations: LabPlace[] }>(
          `/sessions/${sessionId}/locations`).catch(() => ({ locations: [] })),
      ])
      if (stale) return
      setEvents(ev.events.map((e) => ({
        id: e.id, type: e.event_type, content: e.content, actor_name: e.actor_name,
      })))
      const cs = sync?.systems.combat
      setCombat(cs?.active ? (cs as unknown as CombatState) : null)
      setTurn(sync?.systems.turn ?? null)
      setPlaces(locs.locations || [])
      setSceneName(locs.locations.find((l) => l.current)?.name || '')
    }

    load()
      .catch((e: unknown) => { if (!stale) setError(e instanceof Error ? e.message : '读取失败') })
      .finally(() => { if (!stale) setLoading(false) })
    return () => { stale = true }
  }, [sessionId, tick])

  return { session, actors, events, combat, turn, places, sceneName, loading, error, reload }
}
