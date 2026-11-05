import { GiPadlock } from 'react-icons/gi'
import { Flag, ChevronRight, AlertTriangle, Skull, MapPin, Zap } from 'lucide-react'

interface SceneState { when?: string[]; danger?: string; atmosphere?: string; description?: string }
interface NpcState { when?: string[]; personality?: string; initial_location?: string; alive?: boolean }
interface Scene { id: string; name?: string; title?: string; states?: SceneState[]; exits?: unknown[] }
interface NPC { id: string; name?: string; states?: NpcState[] }
interface Trigger { id: string; when?: string; set_flags?: string[]; clear_flags?: string[]; description?: string }

interface Effect {
  type?: string
  skill?: string
  difficulty?: string
  loss?: string
  dice?: string
  amount?: string
  target?: string
  flag?: string
  clue_id?: string
  item?: string
  ending_id?: string
  text?: string
  on_success?: Effect[]
  on_failure?: Effect[]
}
interface FlowEvent {
  id: string
  title?: string
  at?: string
  phase?: string
  once?: boolean
  when?: { text?: string; expr?: unknown }
  effects?: Effect[]
}

const DANGER_LABEL: Record<string, string> = { calm: '平静', uneasy: '不安', dangerous: '危险', deadly: '致命' }
const PHASE_LABEL: Record<string, string> = { opening: '开场', scene: '场景', climax: '高潮', ending: '收场' }
const sceneName = (s: Scene) => s.name || s.title || s.id || '(未命名)'

function summarizeScene(st: SceneState): string {
  const parts: string[] = []
  if (st.danger) parts.push('危险度变为「' + (DANGER_LABEL[st.danger] || st.danger) + '」')
  if (st.atmosphere) parts.push('氛围变化')
  if (st.description) parts.push('描述变化')
  return parts.join('、') || '状态变化'
}
function summarizeNpc(st: NpcState): { text: string; dead: boolean; moved: boolean } {
  if (st.alive === false) return { text: '死亡', dead: true, moved: false }
  const parts: string[] = []
  if (st.initial_location) parts.push('转移至 ' + st.initial_location)
  if (st.personality) parts.push('态度变化')
  return { text: parts.join('、') || '状态变化', dead: false, moved: !!st.initial_location }
}

/** 某些 flag 被置上后，会影响哪些场景/NPC（其变体 when 引用了这些 flag）。 */
function affectedBy(flags: string[], scenes: Scene[], npcs: NPC[]) {
  const set = new Set(flags)
  const items: { kind: 'scene' | 'npc'; name: string; summary: string; dead?: boolean; moved?: boolean }[] = []
  for (const s of scenes) {
    for (const st of s.states || []) {
      if ((st.when || []).some((w) => set.has(w))) {
        items.push({ kind: 'scene', name: sceneName(s), summary: summarizeScene(st) })
      }
    }
  }
  for (const n of npcs) {
    for (const st of n.states || []) {
      if ((st.when || []).some((w) => set.has(w))) {
        const r = summarizeNpc(st)
        items.push({ kind: 'npc', name: n.name || n.id, summary: r.text, dead: r.dead, moved: r.moved })
      }
    }
  }
  return items
}

/** 把一条效果压成一句人话（与后端 effect_brief 同口径，仅展示用）。 */
function effectBrief(e: Effect): string {
  const type = e.type || ''
  if (type === 'check') {
    const head = (e.skill || '技能') + '检定' + (e.difficulty ? '（' + e.difficulty + '）' : '')
    const branches: string[] = []
    if (e.on_success?.length) branches.push('成功：' + e.on_success.map(effectBrief).join('，'))
    if (e.on_failure?.length) branches.push('失败：' + e.on_failure.map(effectBrief).join('，'))
    return head + (branches.length ? ' → ' + branches.join('；') : '')
  }
  if (type === 'sanity') return 'SAN 损失 ' + (e.loss || '?')
  if (type === 'damage') return 'HP 损失 ' + (e.dice || '?')
  if (type === 'heal') return 'HP 恢复 ' + (e.amount || '?')
  if (type === 'goto') return '前往 ' + (e.target || '?')
  if (type === 'set_flag') return '置标志 ' + (e.flag || '?')
  if (type === 'clear_flag') return '清标志 ' + (e.flag || '?')
  if (type === 'reveal_clue') return '揭示线索 ' + (e.clue_id || '?')
  if (type === 'grant_item') return '获得 ' + (e.item || '?')
  if (type === 'remove_item') return '失去 ' + (e.item || '?')
  if (type === 'ending') return '抵达结局 ' + (e.ending_id || '?')
  if (type === 'narration') return e.text || '演出提示'
  return type
}

const accent = 'var(--color-text-accent)'

export function ModuleTimeline({ scenes, npcs, triggers, events = [] }: {
  scenes: Scene[]; npcs: NPC[]; triggers: Trigger[]; events?: FlowEvent[]
}) {
  // 所有被某个变体引用的 flag
  const referenced = new Set<string>()
  for (const s of scenes) for (const st of s.states || []) for (const w of st.when || []) referenced.add(w)
  for (const n of npcs) for (const st of n.states || []) for (const w of st.when || []) referenced.add(w)
  // 所有被某个触发器置/清的 flag
  const provided = new Set<string>()
  for (const t of triggers) { for (const f of t.set_flags || []) provided.add(f); for (const f of t.clear_flags || []) provided.add(f) }

  const danglingVariants = [...referenced].filter((f) => !provided.has(f))
  const danglingTriggers = [...provided].filter((f) => !referenced.has(f))
  const sceneNameById = new Map(scenes.map((s) => [s.id, sceneName(s)]))

  if (triggers.length === 0 && referenced.size === 0 && events.length === 0) {
    return (
      <div className="card text-sm" style={{ color: 'var(--color-text-secondary)' }}>
        本模组未定义剧情推进（无事件、无触发器、无场景/NPC 变体）——它是一份静态模组，时间线为空。
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
        时间线呈现剧情如何推进：事件带可执行效果（传送、检定、结局），触发器负责切换场景/NPC 样貌——这正是关系图（空间结构）表达不了的时间维度。
      </p>

      {events.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1 flex items-center gap-1.5" style={{ color: accent }}>
            <Zap size={14} /> 剧情事件
          </div>
          <p className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
            结构化条件由系统自动判定；未结构化的条件由守秘人按叙事判断。事件触发后效果由后端确定性结算。
          </p>
          <div className="space-y-2">
            {events.map((ev) => {
              const structured = !!ev.when?.expr
              const effects = ev.effects || []
              return (
                <div key={ev.id} className="rounded-lg p-2.5" style={{ border: '1px solid var(--color-border)' }}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{ev.title || ev.id}</span>
                    {ev.phase && <span className="badge">{PHASE_LABEL[ev.phase] || ev.phase}</span>}
                    {ev.at && (
                      <span className="text-xs inline-flex items-center gap-1" style={{ color: 'var(--color-text-secondary)' }}>
                        <MapPin size={11} /> {sceneNameById.get(ev.at) || ev.at}
                      </span>
                    )}
                    <span className="badge" style={{ color: structured ? accent : 'var(--color-text-secondary)' }}>
                      {structured ? '条件可判定' : '需守秘人判断'}
                    </span>
                  </div>
                  <div className="text-xs mt-1.5" style={{ color: 'var(--color-text-secondary)' }}>
                    条件：{ev.when?.text || '—'}
                  </div>
                  {effects.length > 0 ? (
                    <ul className="mt-1.5 space-y-0.5">
                      {effects.map((e, i) => (
                        <li key={i} className="text-xs flex items-start gap-1.5">
                          <ChevronRight size={12} className="mt-0.5 flex-shrink-0" style={{ color: accent }} />
                          <span>{effectBrief(e)}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>
                      无机械效果，由守秘人演出
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="relative pl-6">
        <div className="absolute left-2 top-1 bottom-1 w-px" style={{ background: 'var(--color-border)' }} />
        {triggers.length === 0 && events.length > 0 && <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>（未定义触发器，剧情推进由上方事件驱动。）</p>}
        {triggers.map((t, i) => {
          const flags = [...(t.set_flags || []), ...(t.clear_flags || [])]
          const affected = affectedBy(flags, scenes, npcs)
          return (
            <div key={t.id || i} className="relative mb-4">
              <div className="absolute -left-[18px] top-1 rounded-full p-1" style={{ background: 'var(--color-bg-card)', border: '1px solid ' + accent }}>
                <Flag size={11} style={{ color: accent }} />
              </div>
              <div className="card !mb-0">
                <div className="text-sm font-semibold mb-1">{t.when || '(未填触发条件)'}</div>
                <div className="flex flex-wrap items-center gap-1.5 mb-2">
                  {(t.set_flags || []).map((f) => (
                    <span key={f} className="badge inline-flex items-center gap-1" style={{ color: accent, borderColor: accent }}><Flag size={10} />{f}</span>
                  ))}
                  {(t.clear_flags || []).map((f) => (
                    <span key={f} className="badge inline-flex items-center gap-1" style={{ color: 'var(--color-text-secondary)', borderColor: 'var(--color-border)', textDecoration: 'line-through' }}><Flag size={10} />{f}</span>
                  ))}
                </div>
                {affected.length > 0 ? (
                  <div className="space-y-1">
                    {affected.map((a, k) => (
                      <div key={k} className="flex items-center gap-1.5 text-xs">
                        <ChevronRight size={12} style={{ color: 'var(--color-text-secondary)' }} />
                        {a.kind === 'npc'
                          ? (a.dead ? <Skull size={12} style={{ color: 'var(--color-danger)' }} /> : a.moved ? <MapPin size={12} style={{ color: accent }} /> : <span style={{ width: 12 }} />)
                          : <span style={{ width: 12 }} />}
                        <span style={{ color: 'var(--color-text-secondary)' }}>{a.kind === 'scene' ? '场景' : 'NPC'}</span>
                        <span className="font-medium">{a.name}</span>
                        <span style={{ color: a.dead ? 'var(--color-danger)' : 'var(--color-text-primary)' }}>{a.summary}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>（暂无场景/NPC 变体消费这些标志）</p>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {(danglingVariants.length > 0 || danglingTriggers.length > 0) && (
        <div className="card text-xs" style={{ borderColor: 'var(--color-dice-gold)' }}>
          <div className="flex items-center gap-1 font-semibold mb-1" style={{ color: 'var(--color-dice-gold)' }}>
            <AlertTriangle size={13} /> 标志一致性提示
          </div>
          {danglingVariants.length > 0 && (
            <p style={{ color: 'var(--color-text-secondary)' }} className="flex items-start gap-1">
              <GiPadlock className="mt-0.5 flex-shrink-0" />
              <span>以下标志被场景/NPC 变体引用，却没有任何触发器会置上它（这些变体永不生效）：{danglingVariants.join('、')}</span>
            </p>
          )}
          {danglingTriggers.length > 0 && (
            <p style={{ color: 'var(--color-text-secondary)' }} className="mt-1">
              以下标志被触发器置/清，却没有任何场景/NPC 变体消费它（置了也没效果）：{danglingTriggers.join('、')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
