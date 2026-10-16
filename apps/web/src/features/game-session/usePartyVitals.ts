/**
 * 全队的生命体征。
 *
 * 真人 KP 此前看不到这些：右侧角色卡只显示**一个**角色，要看第二个人得点队伍条切过去，
 * 而「谁快死了、谁理智见底」恰恰是 KP 每一分钟都要知道的事——它决定下一段往哪推。
 * 所以按席位一次拉齐，常驻在控制台上。
 *
 * 与 useSessionResources 的角色卡取数同一条规矩：切存档/换人时作废在途请求，
 * 免得上一局的血量落到这一局的名字上。
 */
import { useEffect, useState } from 'react'

import { api } from '@/api/client'

interface Vital { current: number; max: number }

export interface PartyVital {
  id: string
  name: string
  /** ai 席的队友与真人席的调查员在 KP 眼里同样要盯，只是图标不同 */
  isAi: boolean
  occupation: string
  status: string
  hp?: Vital
  san?: Vital
  mp?: Vital
  /** 原始 system_data：监控卡据此画持续状态徽标（流血等），与角色卡同一份 */
  systemData?: Record<string, unknown>
}

interface SeatLike {
  character_id: string | null
  character_name?: string | null
  role: string
}

interface CharacterPayload {
  id: string
  name: string
  status: string
  system_data: Record<string, unknown>
}

export function usePartyVitals(seats: SeatLike[] | undefined, refreshTick: number): PartyVital[] {
  const [party, setParty] = useState<PartyVital[]>([])
  // 席位数组每次渲染都是新引用，按「参战的角色 id 串」取依赖，否则每帧重拉一轮。
  const key = (seats || []).map((s) => s.character_id || '').join(',')

  useEffect(() => {
    const seated = (seats || []).filter((s) => s.character_id)
    if (!seated.length) { setParty([]); return }
    let stale = false
    Promise.all(seated.map((s) =>
      api.get<CharacterPayload>(`/characters/${s.character_id}`).catch(() => null),
    )).then((chars) => {
      if (stale) return
      setParty(seated.map((s, i) => {
        const c = chars[i]
        const sd = (c?.system_data || {}) as Record<string, unknown>
        return {
          id: s.character_id as string,
          name: c?.name || s.character_name || '未命名',
          isAi: s.role === 'ai',
          occupation: String(sd.occupation || ''),
          status: c?.status || 'active',
          hp: sd.hitPoints as Vital | undefined,
          san: sd.sanity as Vital | undefined,
          mp: sd.magicPoints as Vital | undefined,
          systemData: sd,
        }
      }))
    })
    return () => { stale = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- seats 按 key 取依赖，见上
  }, [key, refreshTick])

  return party
}
