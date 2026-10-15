// 角色卡上的持续状态徽标（system_data.conditions，如流血）。
// 名称与图标的映射和战斗面板共用 combat/meta 的 badgesFor；中文名取规则 manifest 的 conditions。
import { badgesFor } from '../game/combat/meta'
import { useConditionLabels } from './useConditionLabels'

function conditionKeys(systemData: Record<string, unknown>): string[] {
  const raw = systemData?.conditions
  return Array.isArray(raw) ? raw.filter((k): k is string => typeof k === 'string' && k !== '') : []
}

export function ConditionBadges({ systemData, ruleSystem }: { systemData: Record<string, unknown>; ruleSystem?: string }) {
  const keys = conditionKeys(systemData)
  const labels = useConditionLabels(ruleSystem || 'coc', keys.length > 0)
  if (keys.length === 0) return null
  return (
    <>
      {badgesFor(keys, labels).map(({ key, label, Icon }) => (
        <span key={key} className="chip chip--danger mt-1" data-condition={key}>
          <Icon size={10} aria-hidden /> {label}
        </span>
      ))}
    </>
  )
}
