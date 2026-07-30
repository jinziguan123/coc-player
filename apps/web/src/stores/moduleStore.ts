import { create } from 'zustand'
import { api, uploadFile } from '../api/client'

/** 见 server/app/services/module_service.py 的 normalize_character_guidance。 */
export interface CharacterGuidance {
  summary?: string
  recommended?: string[]
  avoid?: string[]
  notes?: string[]
}

/** 是否有内容可展示。四个字段各自独立——历史模组可能只补生成了一部分。 */
export function hasGuidance(g?: CharacterGuidance | null): boolean {
  if (!g) return false
  return !!(g.summary?.trim() || g.recommended?.length || g.avoid?.length || g.notes?.length)
}

export interface Module {
  id: string
  title: string
  rule_system: string
  description: string
  world_setting: Record<string, unknown>
  /** 车卡建议：玩家针对本模组建角色时的取向与限制（可能为空，历史模组未生成过）。 */
  character_guidance?: CharacterGuidance
  scenes: Array<Record<string, unknown>>
  npcs: Array<Record<string, unknown>>
  clues: Array<Record<string, unknown>>
  /** 原文 RAG 索引状态：''=未建 / indexing / ready / failed */
  rag_status?: string
}

interface ModuleStore {
  modules: Module[]
  currentModule: Module | null
  loading: boolean
  fetchModules: () => Promise<void>
  /** 提交上传并启动后台解析任务，立即返回 job_id（进度经 /modules/upload/status/{job_id} 轮询） */
  startUpload: (files: File[], ruleSystem: string) => Promise<string>
  selectModule: (module: Module) => void
}

export const useModuleStore = create<ModuleStore>((set) => ({
  modules: [],
  currentModule: null,
  loading: false,

  fetchModules: async () => {
    set({ loading: true })
    const modules = await api.get<Module[]>('/modules')
    set({ modules, loading: false })
  },

  startUpload: async (files, ruleSystem) => {
    const form = new FormData()
    for (const f of files) form.append('files', f)
    const res = await uploadFile<{ job_id: string }>(`/modules/upload?rule_system=${ruleSystem}`, form)
    return res.job_id
  },

  selectModule: (module) => set({ currentModule: module }),
}))
