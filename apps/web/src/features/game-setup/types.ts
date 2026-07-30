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
}

export interface ModuleFilters {
  query: string
  playerMin: string
  playerMax: string
  era: string
  difficulty: string
  region: string
}
