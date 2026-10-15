// 数据表删行时的提醒：目前只有武器表需要——删掉一把武器后，已有角色手里的那把
// 会改按角色卡上记下的数据结算（见后端 combat_service._attack_weapon），房主应当知道这件事。
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { DataRow } from '@/lib/ruleData'

/** 给 DataTableView 的 deleteWarning：这张表不需要提醒时返回 undefined。 */
export function useDeleteWarning(ruleSystem: string, table: string | undefined) {
  const [holders, setHolders] = useState<Record<string, number>>({})
  const isWeapons = table === 'weapons'

  useEffect(() => {
    if (!isWeapons) return
    let alive = true
    api.get<{ holders?: Record<string, number> }>(`/rulebooks/weapon-holders/${ruleSystem}`)
      .then((r) => { if (alive) setHolders(r.holders ?? {}) })
      .catch(() => { /* 只是提醒，取不到就不提醒 */ })
    return () => { alive = false }
  }, [ruleSystem, isWeapons])

  if (!isWeapons) return undefined
  return (row: DataRow): string | null => {
    const n = holders[String(row.name ?? '').trim()]
    return n ? `有 ${n} 个角色持有这把武器，删除后他们战斗时改按角色卡上记下的数据使用它` : null
  }
}
