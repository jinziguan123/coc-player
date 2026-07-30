import { api, localApi } from '@/api/client'

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
}

export interface GenerateCharacterRequest {
  module_id: string
  hint: string
  is_player?: boolean
}

export function listCharacters() {
  return api.get<Character[]>('/characters')
}

export function listAvailableCharacters(isPlayer: boolean) {
  return api.get<Character[]>(
    `/characters?available=true&is_player=${isPlayer ? 'true' : 'false'}`,
  )
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
