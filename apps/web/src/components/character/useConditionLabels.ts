// 规则系统声明的持续状态中文名（manifest 的 conditions 段），按规则系统缓存。
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

type Labels = Record<string, string>

// 按规则系统缓存 manifest 里的状态中文名：同一页多个角色卡面板只发一次请求。
// 失败不缓存（下次挂载重试），这期间回落内置中文名或状态键。
const labelCache = new Map<string, Promise<Labels>>()

function fetchConditionLabels(ruleSystem: string): Promise<Labels> {
  let p = labelCache.get(ruleSystem)
  if (!p) {
    p = api.get<{ conditions?: { key: string; label: string }[] }>(`/rulebooks/village-rules/${ruleSystem}/manifest`)
      .then((m) => Object.fromEntries((m.conditions ?? []).map((c) => [c.key, c.label])))
    p.catch(() => labelCache.delete(ruleSystem))
    labelCache.set(ruleSystem, p)
  }
  return p
}

/** 测试用：清空缓存。 */
export function resetConditionLabelCache() {
  labelCache.clear()
}

/** 规则系统声明的状态中文名；enabled 为 false 时不发请求（角色身上没状态就不必拉 manifest）。 */
export function useConditionLabels(ruleSystem: string, enabled: boolean): Labels | undefined {
  const [labels, setLabels] = useState<Labels>()
  useEffect(() => {
    if (!enabled) return
    let alive = true
    fetchConditionLabels(ruleSystem).then((l) => { if (alive) setLabels(l) }, () => {})
    return () => { alive = false }
  }, [ruleSystem, enabled])
  return labels
}
