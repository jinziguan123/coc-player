import { csv } from '@/lib/csvList'
import { CsvInput, Row, TextInput } from './ModuleFormParts'

/** 剧情事件场景（kind=event）上与「触发」相关的字段。 */
export interface EventScene { when?: string; npcs?: string[]; set_flags?: string[] }

/** 剧情事件（kind=event）专属字段：触发条件、涉及 NPC（触发前蛰伏）、触发后置的标志。 */
export function SceneEventFields({ scene, npcs, edit, onChange }: {
  scene: EventScene
  npcs: { id: string; name?: string }[]
  edit: boolean
  onChange: (patch: Partial<EventScene>) => void
}) {
  const involved = scene.npcs || []
  const npcName = (id: string) => npcs.find((n) => n.id === id)?.name || id
  const toggleNpc = (id: string) => onChange({
    npcs: involved.includes(id) ? involved.filter((x) => x !== id) : [...involved, id],
  })
  return (
    <div className="mt-1 mb-1 rounded" style={{ border: '1px dashed var(--color-text-accent)', padding: 8 }}>
      <div className="text-xs font-semibold mb-1" style={{ color: 'var(--color-text-accent)' }}>
        剧情事件（按条件触发，不是地点，不上地图）
      </div>
      <Row label="触发条件">{edit
        ? <TextInput value={scene.when || ''} onChange={(v) => onChange({ when: v })} multiline
            placeholder="照抄模组原文，如『约30-40分钟后（调查员探索完前室和耳室时）』" />
        : <span className="whitespace-pre-wrap text-xs">{scene.when || '—'}</span>}</Row>
      <Row label="涉及 NPC">{edit ? (
        <div className="flex flex-wrap gap-1">
          {npcs.filter((n) => n.id).map((n) => {
            const on = involved.includes(n.id)
            return (
              <button key={n.id} type="button" onClick={() => toggleNpc(n.id)} aria-pressed={on}
                className="chip text-xs"
                style={on ? { color: 'var(--color-text-accent)', borderColor: 'var(--color-text-accent)' } : { opacity: 0.6 }}>
                {n.name || n.id}
              </button>
            )
          })}
          {npcs.length === 0 && <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>模组还没有 NPC</span>}
        </div>
      ) : <span className="text-xs">{involved.map(npcName).join('、') || '—'}</span>}</Row>
      {edit && <p className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>
        选中的 NPC 在事件触发前处于蛰伏：幕后推演不让它行动，KP 也不会让它提前现身。
      </p>}
      <Row label="触发标志">{edit
        ? <CsvInput values={scene.set_flags} onChange={(v) => onChange({ set_flags: v })}
            placeholder="触发后置的标志，逗号分隔；与剧情钩子呼应（留空则保存时自动生成）" />
        : <span className="text-xs" style={{ color: 'var(--color-text-accent)' }}>{csv(scene.set_flags) || '—'}</span>}</Row>
    </div>
  )
}
