import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, localApi, setServerUrl } from './client'

/**
 * 204 No Content 不该被当成失败。
 *
 * 这条是踩出来的：`DELETE /api/net/peers/{token}` 按 REST 语义返回 204，而 `requestAt`
 * 一律 `res.json()`——空响应体上抛 SyntaxError。表现最坑人的地方在于两头对不上：后端
 * 日志里是 `204 No Content`，界面上却弹「操作失败」，而记录其实已经删掉了。
 *
 * 在此之前全项目只有那一个 204 端点，其余 DELETE 都回 JSON，所以一直没暴露。
 */
function respond(status: number, body?: string): Response {
  return {
    ok: status < 400,
    status,
    json: async () => {
      if (!body) throw new SyntaxError('Unexpected end of JSON input')
      return JSON.parse(body)
    },
    text: async () => body ?? '',
  } as unknown as Response
}

describe('空响应体', () => {
  beforeEach(() => {
    localStorage.clear()
    setServerUrl('')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('204 视为成功，不因为没有响应体而报错', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(204)))
    await expect(localApi.delete('/net/peers/abc')).resolves.toBeUndefined()
  })

  it('走远程主机的那条路径同样不报错', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(204)))
    await expect(api.delete('/sessions/s1')).resolves.toBeUndefined()
  })

  it('有响应体时照常解析', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(200, '{"ok":true}')))
    await expect(localApi.get('/net/peers')).resolves.toEqual({ ok: true })
  })

  it('失败仍然抛出，且带上后端给的说明', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => respond(403, '{"detail":"只有房主本机可以管理联机名册"}')))
    await expect(localApi.delete('/net/peers/abc')).rejects.toThrow('只有房主本机可以管理联机名册')
  })
})
