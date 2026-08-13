import { api, localApi, uploadFileLocal } from '@/api/client'

export interface Character {
  id: string
  name: string
  module_id: string | null
  /** 有值即为参战副本，指回客人自己库里的原件；见 syncBack.ts。 */
  origin_character_id?: string | null
  rule_system: string
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
  /** 头像图片 URL；为空是**正常状态**，前端回落姓名首字纹章（见 CharacterPortrait）。 */
  avatar_url?: string | null
  /** 模组经历：一局落幕后由后端归档，前端只读。 */
  experiences?: CharacterExperience[]
}

/** 一条模组经历：story 是给人读的第三人称小传，其余是给档案卡计数/排序的元数据。 */
export interface CharacterExperience {
  session_id: string
  module_id: string
  module_title: string
  ending_name: string
  at: string
  survived: boolean
  final_status: string
  story: string
}

export interface GenerateCharacterRequest {
  module_id: string
  hint: string
}

export function listCharacters() {
  return api.get<Character[]>('/characters')
}

/** 当前可用的角色卡（没被别的会话占用）。不再按 is_player 分池——
    一张卡给真人演还是给 AI 演，是**席位**的事，不是卡的属性。 */
export function listAvailableCharacters() {
  return api.get<Character[]>('/characters?available=true')
}

export function generateCharacter<T = Record<string, unknown>>(
  request: GenerateCharacterRequest,
) {
  return localApi.post<T>('/characters/ai-generate', request)
}

export function createCharacter<T = Character>(payload: unknown) {
  return localApi.post<T>('/characters', payload)
}

export function removeCharacter(characterId: string) {
  return localApi.delete(`/characters/${characterId}`)
}


// ── 头像 ──────────────────────────────────────────────────────────────
// 上传与 AI 生成走同一条落盘与回写路径，产出的头像在系统里毫无区别；
// 都固定走本机（角色卡是本机资产，后端挂 require_local_client）。

export function uploadCharacterAvatar(characterId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return uploadFileLocal<Character>(`/characters/${characterId}/avatar`, form)
}

export function generateCharacterAvatar(characterId: string) {
  return localApi.post<Character>(`/characters/${characterId}/avatar/generate`)
}

export function clearCharacterAvatar(characterId: string) {
  return localApi.delete<Character>(`/characters/${characterId}/avatar`)
}
