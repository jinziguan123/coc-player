import { describe, expect, it } from 'vitest'

/**
 * ADR-007：管理本机资产的端点只接受回环来源，前端必须用 `localApi` 调它们。
 *
 * 用 `api`（客人模式下会打到房主）不但拿不到自己的数据，还会直接 403。
 * 这类错误只在「客人模式 + 打开那个页面」时才暴露，人工回归很容易漏，
 * 所以按源码扫一遍。
 *
 * 注意这里**只列真正的本机专属端点**。像 `POST /characters`、`/characters/ai-generate`
 * 这种客人入座流程要用的，必须走 `api` 打到房主——它们不在此列。
 */
const LOCAL_ONLY_PATHS = [
  '/settings/ai/profiles',
  '/net',
  '/onboarding/start',
]

/**
 * `/net` 底下的例外：客人**必须**拿它们打到房主。
 *
 * 敲门（`/net/knock`）和查自己排到哪儿了（`/net/me`）是客人对房主说的话——
 * 「我是谁」「你放我进来了吗」。走 localApi 只会问到自己那台机器，永远等不到
 * 房主点头。判定它们的是来源，不是路径前缀：房主本机调同样合法，只会得到
 * 「你就是本机」这个答案。
 */
const GUEST_FACING = ['/net/knock', '/net/me']

// 用 Vite 的 glob 读源码，避免依赖 Node 类型（tsconfig 只带 vite/client）
const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('本机专属端点的前端路由（ADR-007）', () => {
  it.each(LOCAL_ONLY_PATHS)('%s 不得经 api.* 调用（应走 localApi）', (path) => {
    const offenders: string[] = []
    for (const [file, text] of Object.entries(SOURCES)) {
      if (file.includes('.test.')) continue
      for (const line of text.split('\n')) {
        if (!line.includes(path)) continue
        if (GUEST_FACING.some((allowed) => line.includes(allowed))) continue
        // 匹配 `api.get(` / `api.post<T>(` 等；`localApi.` 因前置字符是 l 而不会命中
        if (/(^|[^a-zA-Z])api\.(get|post|put|patch|delete)\b/.test(line)) {
          offenders.push(`${file}: ${line.trim()}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
