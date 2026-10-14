export interface GameModule {
  id: string
  title: string
  description?: string
  world_setting?: Record<string, unknown> | null
}

export interface SetupCharacter {
  id: string
  name: string
  /** 可空：角色卡不必属于某个模组（客人本地未必有房主用的那个本子）。 */
  module_id: string | null
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
}

export interface SetupSeat {
  role: 'human' | 'ai'
  charId: string
}

export interface SessionSummary {
  id: string
  status: string
  module_title?: string
  character_name?: string
  created_at?: string
  updated_at?: string
  room_code?: string | null
  /** 首页「继续上次的桌」用：只含**当前所在**场景（后端 _scene_brief，不剧透其它场景） */
  scene_title?: string | null
  scene_image?: string | null
  combat_round?: number | null
}

export interface ModuleFilters {
  query: string
  playerMin: string
  playerMax: string
  era: string
  difficulty: string
  region: string
}
