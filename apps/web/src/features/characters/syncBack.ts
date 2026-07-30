import { api, getServerUrl, localApi } from '@/api/client'
import type { Character } from './api'

/**
 * 把参战结果写回自己的角色卡。
 *
 * 联机时客人的卡会在房主机器上留一份**参战副本**（房主的规则引擎要读写它才跑得动，
 * 见 server/app/api/characters.py 的归属过滤说明）。本局的 HP/SAN/成长/物品只落在
 * 那份副本上，不同步回来的话，跑完团自己库里的卡还是入座前的样子。
 *
 * 设计上刻意是**客人拉**而不是房主推：
 * - 客人的库只有他自己能写（ADR-007：素材库写操作仅限本机）；
 * - 房主可能先退出，推不过来；
 * - 拉取是**幂等全量覆盖**，拉几次结果一样 —— 所以掉线不可怕，下次连上补拉即可，
 *   不需要「必须在本局结束那一刻同步成功」。
 *
 * 没有并发写冲突：游戏期间只有房主那一侧在写。真正需要防的只有一种情况——客人
 * 在跑团期间又在本机改过原件，那时以本局结果为准，但覆盖前留一份快照。
 */

/** 覆盖前留在原件 system_data 里的快照键。只保留最近一次。 */
const SNAPSHOT_KEY = 'pre_sync_snapshot'

export interface SyncBackResult {
    /** 实际写回的角色名，用于提示。 */
  synced: string[]
  failed: number
}

/**
 * 从当前连着的房主处拉取属于自己的参战副本，写回本机原件。
 *
 * 未连接房主（本机模式）时直接返回空结果——本机自己玩不存在副本。
 */
export async function syncCharactersBackFromHost(): Promise<SyncBackResult> {
  const result: SyncBackResult = { synced: [], failed: 0 }
  if (!getServerUrl()) return result

  let copies: Character[]
  try {
    // mine=true 只返回属于当前 token 的卡，即「我的那几份副本」。
    copies = await api.get<Character[]>('/characters?mine=true')
  } catch {
    // 房主已退出或网络断了：这次拉不到，下次进房间再拉。
    return result
  }

  for (const copy of copies) {
    // 没有血缘的卡是在房主机器上直接建的（不是从本机带过去的），本机没有原件可写。
    if (!copy.origin_character_id) continue
    try {
      await writeBackOne(copy)
      result.synced.push(copy.name)
    } catch {
      result.failed += 1
    }
  }
  return result
}

async function writeBackOne(copy: Character): Promise<void> {
  const originId = copy.origin_character_id as string
  // 原件必须还在本机——被删掉了就没什么可同步的，静默跳过而不是报错。
  const origin = await localApi.get<Character>(`/characters/${originId}`)

  const incoming = (copy.system_data || {}) as Record<string, unknown>
  const current = (origin.system_data || {}) as Record<string, unknown>
  // 快照里不再嵌套上一次的快照，否则每同步一次体积翻倍。
  const { [SNAPSHOT_KEY]: _dropped, ...currentWithoutSnapshot } = current

  await localApi.put(`/characters/${originId}`, {
    base_attributes: copy.base_attributes,
    skills: copy.skills,
    status: copy.status,
    backstory: copy.backstory,
    system_data: {
      ...incoming,
      [SNAPSHOT_KEY]: {
        at: new Date().toISOString(),
        base_attributes: origin.base_attributes,
        skills: origin.skills,
        status: origin.status,
        system_data: currentWithoutSnapshot,
      },
    },
  })
}
