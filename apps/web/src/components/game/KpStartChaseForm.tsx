import { useEffect } from 'react'
import { GiRun } from 'react-icons/gi'

import { MultiSelect } from '@/components/ui/multi-select'
import { KpInput } from './KpInput'
import { characterIds, splitCsv } from './kpFormUtils'

interface CatalogItem { id: string; name: string }

interface KpStartChaseFormProps {
  fields: Record<string, string>
  characters: CatalogItem[]
  npcs: CatalogItem[]
  setField: (key: string, value: string) => void
}

/** 一个带占位的数字/技能输入：和旁边的下拉同高，不再各自缩成不同尺寸。 */
function ChaseInput({ value, placeholder, onChange, className, numeric = false, ariaLabel }: {
  value: string
  placeholder: string
  onChange: (value: string) => void
  className?: string
  numeric?: boolean
  ariaLabel: string
}) {
  return (
    <input
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      aria-label={ariaLabel}
      inputMode={numeric ? 'numeric' : undefined}
      className={'input h-9 text-xs' + (className ? ' ' + className : '')}
    />
  )
}

/**
 * 发起追逐：逃方与追方都支持多选；旧版的单个 select + 自由输入混搭在这里统一成
 * 同一套 MultiSelect。追方额外留一个临场起名框，模组没登记的追兵也能上桌。
 */
export function KpStartChaseForm({ fields, characters, npcs, setField }: KpStartChaseFormProps) {
  const quarryIds = characterIds(fields.quarries || fields.quarry)
  const pursuers = splitCsv(fields.pursuers || fields.pursuer)

  // 旧字段 quarry / pursuer 迁移到新的多选字段，避免打开表单时看起来是空的。
  useEffect(() => {
    if (!fields.quarries && fields.quarry) setField('quarries', characterIds(fields.quarry).join(','))
    if (!fields.pursuers && fields.pursuer) setField('pursuers', splitCsv(fields.pursuer).join(','))
  }, [fields.quarries, fields.quarry, fields.pursuer, fields.pursuers, setField])

  return (
    <div className="kp-chase-start w-full">
      <div className="flex w-full flex-wrap items-center gap-2">
        <GiRun size={16} aria-hidden />
        <span className="kp-form-label">逃方</span>
        <MultiSelect
          value={quarryIds}
          options={characters.map((item) => ({ value: item.id, label: item.name }))}
          onValueChange={(value) => {
            setField('quarries', value.join(','))
            if (fields.quarry) setField('quarry', '')
          }}
          placeholder={characters.length ? '默认全员（可点选）' : '暂无调查员'}
          emptyLabel="暂无调查员"
          aria-label="逃跑的调查员"
          className="min-w-40 flex-1"
        />
        <span className="kp-form-label">追方</span>
        <MultiSelect
          value={pursuers}
          options={npcs.map((item) => ({ value: item.name, label: item.name }))}
          onValueChange={(value) => {
            setField('pursuers', value.join(','))
            if (fields.pursuer) setField('pursuer', '')
          }}
          placeholder="选择追兵（可多选）"
          emptyLabel="暂无模组 NPC"
          aria-label="追逐的追兵"
          className="min-w-40 flex-1"
        />
        <ChaseInput
          value={fields.pursuer_extra || ''}
          onChange={(value) => setField('pursuer_extra', value)}
          placeholder="临场追兵（可选，逗号分隔）"
          className="min-w-40 flex-1"
          ariaLabel="临场追兵"
        />
      </div>

      <div className="flex w-full flex-wrap items-center gap-2">
        <ChaseInput value={fields.skill || ''} onChange={(value) => setField('skill', value)} placeholder="比拼技能" className="w-40" ariaLabel="比拼技能，默认运动" />
        <ChaseInput value={fields.pursuer_mov || ''} onChange={(value) => setField('pursuer_mov', value)} placeholder="追方 MOV" className="w-28" numeric ariaLabel="追方 MOV，可选，统一覆盖" />
        <ChaseInput value={fields.start_gap || ''} onChange={(value) => setField('start_gap', value)} placeholder="起始 0" className="w-28" numeric ariaLabel="起始距离" />
        <ChaseInput value={fields.escape_at || ''} onChange={(value) => setField('escape_at', value)} placeholder="甩脱 5" className="w-28" numeric ariaLabel="甩脱距离" />
        <ChaseInput value={fields.caught_at || ''} onChange={(value) => setField('caught_at', value)} placeholder="被追上 −3" className="w-28" numeric ariaLabel="被追上的距离" />
        <KpInput name="trigger" placeholder="起因（写进叙事，可选）" fields={fields} onChange={setField} />
      </div>

      <p className="text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>
        留空用规则书默认：起始 0 / 甩脱 5 / 被追上 −3；不点逃方＝全员逃跑。多人时按最慢的逃者与最快的追兵代表全组判定；追方 MOV 填了会统一覆盖所选追兵。
      </p>
    </div>
  )
}
