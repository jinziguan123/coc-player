/**
 * NPC 卡里的战斗数值（HP / 护甲 / 武器 / 伤害）。
 *
 * 从 ModuleDetailPage 抽出来的，那页撞了行数红线。这一段自成一体——只读 npc、只回写这
 * 四个字段，是最该先搬走的一块。
 */
interface CombatValues {
  hp?: number
  armor?: number
  weapon?: string
  damage?: string
}

const FIELD_STYLE = {
  background: 'var(--color-bg-card)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text-primary)',
}

export function NpcCombatFields({ npc, edit, onChange }: {
  npc: CombatValues
  edit: boolean
  onChange: (patch: CombatValues) => void
}) {
  if (!edit) {
    const parts = [
      npc.hp != null ? `HP ${npc.hp}` : '',
      npc.armor != null ? `护甲 ${npc.armor}` : '',
      npc.weapon ? `武器 ${npc.weapon}` : '',
      npc.damage ? `伤害 ${npc.damage}` : '',
    ].filter(Boolean)
    return <span className="text-xs">{parts.join('、') || '—'}</span>
  }
  return (
    <div className="flex items-center gap-3 text-xs flex-wrap">
      {([['hp', 'HP'], ['armor', '护甲']] as const).map(([key, label]) => (
        <label key={key} className="flex items-center gap-1">
          <span style={{ color: 'var(--color-text-secondary)' }}>{label}</span>
          <input type="number" value={npc[key] ?? ''} className="w-14 px-1 py-0.5 rounded" style={FIELD_STYLE}
            onChange={(e) => onChange({ [key]: e.target.value === '' ? undefined : Number(e.target.value) })} />
        </label>
      ))}
      <label className="flex items-center gap-1 flex-1 min-w-32">
        <span style={{ color: 'var(--color-text-secondary)' }}>武器</span>
        <input value={npc.weapon || ''} placeholder="如 匕首、撕咬" className="w-full px-1 py-0.5 rounded"
          style={FIELD_STYLE} onChange={(e) => onChange({ weapon: e.target.value })} />
      </label>
      {/* 伤害骰：怪物的自创攻击方式（撕咬/触手）不在武器表里，不填就只能按徒手 1D3 估伤 */}
      <label className="flex items-center gap-1 flex-1 min-w-32"
        title="怪物自创攻击方式必填，否则按徒手 1D3+DB 估伤；常规武器留空即按武器表结算">
        <span style={{ color: 'var(--color-text-secondary)' }}>伤害</span>
        <input value={npc.damage || ''} placeholder="如 1D6（常规武器可留空）" className="w-full px-1 py-0.5 rounded"
          style={FIELD_STYLE} onChange={(e) => onChange({ damage: e.target.value })} />
      </label>
    </div>
  )
}
