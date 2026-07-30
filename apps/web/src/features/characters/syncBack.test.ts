import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, localApi } from '@/api/client'
import { syncCharactersBackFromHost } from './syncBack'

let serverUrl = 'http://127.0.0.1:54321'
vi.mock('@/api/client', () => ({
  getServerUrl: () => serverUrl,
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

const hostGet = vi.mocked(api.get)
const localGet = vi.mocked(localApi.get)
const localPut = vi.mocked(localApi.put)

/** 房主库里的参战副本：本局跑完，HP 掉了、技能涨了。 */
const copy = {
  id: 'copy-1',
  name: '许闻舟',
  module_id: 'mod-1',
  origin_character_id: 'origin-1',
  rule_system: 'coc',
  base_attributes: { LUCK: 55 },
  skills: { 侦查: 70 },
  system_data: { hitPoints: { current: 4, max: 12 } },
  backstory: '本局之后补的一段',
  status: '重伤',
}

/** 客人本机的原件：还是入座前的样子。 */
const origin = {
  id: 'origin-1',
  name: '许闻舟',
  module_id: null,
  rule_system: 'coc',
  base_attributes: { LUCK: 55 },
  skills: { 侦查: 60 },
  system_data: { hitPoints: { current: 12, max: 12 } },
  backstory: '入座前',
  status: 'active',
}

describe('参战结果写回本机角色卡', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    serverUrl = 'http://127.0.0.1:54321'
    hostGet.mockResolvedValue([copy])
    localGet.mockResolvedValue(origin)
    localPut.mockResolvedValue({})
  })

  it('把本局的成长与损伤写回原件', async () => {
    const result = await syncCharactersBackFromHost()

    expect(result.synced).toEqual(['许闻舟'])
    // 写的是**本机原件**（走 localApi），不是房主那份
    const [path, body] = localPut.mock.calls[0]
    expect(path).toBe('/characters/origin-1')
    expect(body).toMatchObject({
      skills: { 侦查: 70 },
      status: '重伤',
      backstory: '本局之后补的一段',
    })
  })

  it('覆盖前留一份快照，改错了能找回来', async () => {
    await syncCharactersBackFromHost()

    const body = localPut.mock.calls[0][1] as { system_data: Record<string, never> }
    const snapshot = body.system_data.pre_sync_snapshot as unknown as {
      skills: Record<string, number>
      status: string
    }
    expect(snapshot.skills).toEqual({ 侦查: 60 })
    expect(snapshot.status).toBe('active')
    // 本局的新状态同时写进去了
    expect(body.system_data).toMatchObject({ hitPoints: { current: 4, max: 12 } })
  })

  it('快照不嵌套上一次的快照，否则每同步一次体积翻倍', async () => {
    localGet.mockResolvedValue({
      ...origin,
      system_data: { hitPoints: { current: 12 }, pre_sync_snapshot: { 陈旧: true } },
    })
    await syncCharactersBackFromHost()

    const body = localPut.mock.calls[0][1] as { system_data: Record<string, never> }
    const snapshot = body.system_data.pre_sync_snapshot as unknown as {
      system_data: Record<string, unknown>
    }
    expect(snapshot.system_data).not.toHaveProperty('pre_sync_snapshot')
  })

  it('本机模式下什么都不做——不存在参战副本', async () => {
    serverUrl = ''
    const result = await syncCharactersBackFromHost()

    expect(result.synced).toEqual([])
    expect(hostGet).not.toHaveBeenCalled()
    expect(localPut).not.toHaveBeenCalled()
  })

  it('没有血缘的卡跳过——那是直接在房主机器上建的，本机没有原件', async () => {
    hostGet.mockResolvedValue([{ ...copy, origin_character_id: null }])
    const result = await syncCharactersBackFromHost()

    expect(result.synced).toEqual([])
    expect(localPut).not.toHaveBeenCalled()
  })

  it('房主已退出时安静返回，等下次进房间再拉', async () => {
    // 拉取是幂等全量覆盖，所以漏一次没有后果——这正是不必「必须在结束那刻同步」的原因
    hostGet.mockRejectedValue(new Error('connection refused'))
    const result = await syncCharactersBackFromHost()

    expect(result).toEqual({ synced: [], failed: 0 })
    expect(localPut).not.toHaveBeenCalled()
  })

  it('原件已被删掉时记为失败，但不影响其他角色', async () => {
    hostGet.mockResolvedValue([copy, { ...copy, id: 'copy-2', name: '另一张', origin_character_id: 'origin-2' }])
    localGet.mockImplementation(async (path: string) =>
      path.endsWith('origin-1') ? Promise.reject(new Error('404')) : origin,
    )

    const result = await syncCharactersBackFromHost()
    expect(result.failed).toBe(1)
    expect(result.synced).toEqual(['另一张'])
  })
})
