/**
 * 局内三份「跟着角色/存档走」的远端数据：角色卡、我的武器栏、已知地点。
 *
 * 抽出来不只是给 GameSessionPage 腾行数——这三处此前是同一个 bug 的三个实例：
 * 请求发出后无人作废，切存档时旧请求晚一步返回，就把新存档已经算对的值覆盖掉。
 * 表现最刺眼的是角色卡：进真人 KP 局时它会钉在上一局自己的角色上，而那个人
 * 在本局根本不存在，且此后没有任何时机能把它纠回来。
 *
 * 所以三条取数统一走「清理函数作废在途请求」这一条规矩，放在一处好一眼看全。
 */
import { useEffect, useState } from 'react'

import { api } from '@/api/client'

interface Character {
  id: string
  name: string
  module_id: string
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
}

export interface KnownLocation {
  id: string; name: string; current: boolean; visited: boolean
  connections?: string[]; party?: string[]
  clues?: { id: string; name: string; status: string }[]
  // 沙盘坐标与地貌。parent＝这一格属于哪个子沙盘（空＝顶层）；各层坐标空间彼此独立，
  // 丢了它，子层的格子会拿着子层坐标画进顶层，两层直接叠在一起。
  map?: { q: number; r: number; biome: string; parent?: string } | null
  known?: boolean                                        // KP 上帝视角下玩家是否已知
  image?: string                                         // 场景配图（氛围底的色调来源）
  sceneId?: string
  nodeKind?: 'scene' | 'terrain'
}

export interface MapNodePayload {
  id: string; q: number; r: number; biome: string; scene_id?: string | null; parent?: string
}

export interface Weapon { name: string; dam?: string }

/** 右侧角色卡当前展示的那张卡。shownCharId 为空即无卡可显示。 */
export function usePanelCharacter(shownCharId: string | null, refreshTick: number) {
  const [panelChar, setPanelChar] = useState<Character | null>(null)
  useEffect(() => {
    if (!shownCharId) { setPanelChar(null); return }
    let stale = false
    api.get<Character>(`/characters/${shownCharId}`)
      .then((c) => { if (!stale) setPanelChar(c) })
      .catch(() => { if (!stale) setPanelChar(null) })
    return () => { stale = true }
  }, [shownCharId, refreshTick])
  return panelChar
}

/** 我的武器栏（战斗武器选择器用）：拳头默认由前端补，其余从角色卡 system_data.weapons 取。 */
export function useMyWeapons(myCharId: string | null, refreshTick: number) {
  const [myWeapons, setMyWeapons] = useState<Weapon[]>([])
  useEffect(() => {
    if (!myCharId) { setMyWeapons([]); return }
    let stale = false
    api.get<Character>(`/characters/${myCharId}`)
      .then((c) => {
        if (stale) return
        const ws = (c.system_data?.weapons as Weapon[] | undefined) || []
        setMyWeapons(ws.filter((w) => w?.name).map((w) => ({ name: String(w.name), dam: w.dam })))
      })
      .catch(() => { if (!stale) setMyWeapons([]) })
    return () => { stale = true }
  }, [myCharId, refreshTick])
  return myWeapons
}

/**
 * 已知地点：前往后 / 生成结束刷新。真人 KP 返回全图（god_view）。
 *
 * 常驻拉取而非只在展开大地图时拉——场景氛围底要靠它拿「**我自己**所在场景」及其配图：
 * 分头行动时各人所在地不同，会话的 current_scene_id 只是房主锚点，照它渲染会让分头的
 * 队友都看到房主那间屋子。存量存档也只有这里取得到图（配图回写在 scene.image，而聊天流
 * 里那条「抵达」插图消息可能不在已加载的分页里）。
 *
 * refreshTick 这个依赖是必需的、不能为了省一次请求删掉：客人自己「前往」时只改
 * party_locations，不动 current_scene_id（那只在主角移动时同步），光靠场景 id 变化
 * 这条副作用不会重跑，客人的背景会一直停在旧场景。抵达叙述结束时 refreshTick 会 +1。
 * 代价可接受：实测取全量事件 + 算地点在 70 条事件的会话上约 1ms，2000 条外推约 30ms，
 * 相对一回合几十秒的 LLM 调用可以忽略。
 */
export function useKnownLocations(input: {
  sessionId?: string
  myCharId: string | null
  sceneId?: string | null
  refreshTick: number
}) {
  const { sessionId, myCharId, sceneId, refreshTick } = input
  const [locations, setLocations] = useState<KnownLocation[]>([])
  const [mapNodes, setMapNodes] = useState<MapNodePayload[]>([])
  const [mapBackdrop, setMapBackdrop] = useState('')   // 沙盘氛围底图（模组里生成的那张俯视图）
  const [canGodView, setCanGodView] = useState(false)  // 真人 KP 席位（服务端判定）

  useEffect(() => {
    if (!sessionId) return
    const q = myCharId ? `?char_id=${myCharId}` : ''
    let stale = false
    api.get<{
      locations: KnownLocation[]; map_nodes?: MapNodePayload[]
      god_view?: boolean; map_backdrop?: string
    }>(`/sessions/${sessionId}/locations${q}`)
      .then((r) => {
        if (stale) return
        setLocations(r.locations || []); setMapNodes(r.map_nodes || [])
        setCanGodView(!!r.god_view); setMapBackdrop(r.map_backdrop || '')
      })
      .catch(() => {
        if (stale) return
        setLocations([]); setMapNodes([]); setCanGodView(false); setMapBackdrop('')
      })
    return () => { stale = true }
  }, [sessionId, myCharId, sceneId, refreshTick])

  return { locations, mapNodes, mapBackdrop, canGodView }
}
