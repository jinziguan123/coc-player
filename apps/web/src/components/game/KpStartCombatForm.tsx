import { GiCrossedSwords } from 'react-icons/gi'

import { MultiSelect } from '@/components/ui/multi-select'
import { KpInput } from './KpInput'
import { characterIds, splitCsv } from './kpFormUtils'

interface CatalogItem { id: string; name: string }

interface KpStartCombatFormProps {
  fields: Record<string, string>
  characters: CatalogItem[]
  npcs: CatalogItem[]
  setField: (key: string, value: string) => void
}

/**
 * 开战表单：先选参战调查员，再选敌人。
 *
 * 此前这里只有敌人多选，后端把「主角 + 全队」一股脑拉进先攻——明明只有两个人
 * 在车厢里，其余席位的角色也会被拽进战斗。现在不点选参战角色＝沿用全队；
 * 点选之后只让选中的角色入战，其余人留在场外。
 */
export function KpStartCombatForm({ fields, characters, npcs, setField }: KpStartCombatFormProps) {
  const participants = characterIds(fields.participants)
  const enemies = splitCsv(fields.enemies).map((ref) => {
    const hit = npcs.find((item) => item.id === ref || item.name === ref)
    return hit ? hit.name : ref
  })
  return (
    <>
      <div className="flex w-full flex-wrap items-center gap-2">
        <GiCrossedSwords size={16} aria-hidden />
        <span className="kp-form-label">参战</span>
        <MultiSelect
          value={participants}
          options={characters.map((item) => ({ value: item.id, label: item.name }))}
          onValueChange={(value) => setField('participants', value.join(','))}
          placeholder={characters.length ? '默认全员（可点选）' : '暂无调查员'}
          emptyLabel="暂无调查员"
          aria-label="参战调查员"
          className="min-w-44 flex-1"
        />
        <span className="kp-form-label">敌人</span>
        <MultiSelect
          value={enemies}
          options={npcs.map((item) => ({ value: item.name, label: item.name }))}
          onValueChange={(value) => setField('enemies', value.join(','))}
          placeholder="选择参战敌人"
          emptyLabel="暂无模组 NPC"
          aria-label="参战敌人"
          className="min-w-44 flex-1"
        />
        <KpInput name="trigger" placeholder="开战原因（可选）" fields={fields} onChange={setField} />
      </div>
      <p className="w-full text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>
        不点选参战角色＝全队入战；点选后只拉选中的角色，其余人留在场外。
      </p>
    </>
  )
}
