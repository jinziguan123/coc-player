import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m } from 'motion/react'
import { RefreshCw, X } from 'lucide-react'
import { GiArrowScope, GiScrollUnfurled } from 'react-icons/gi'
import { api } from '../../api/client'
import { spring } from '@/lib/motion'

interface TraceItem {
  id: string
  agent: string
  stage: string
  outcome: string
  duration_ms: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  created_at: string | null
}

interface IntentItem {
  actor_id: string
  actor_name: string
  source: string
  action: string
  target: string
  verb: string
  content: string
  observes_seq: number
  target_version: number
  precondition_ok: boolean
}

const AGENT_LABEL: Record<string, string> = {
  planner: '规划', team: '队友', npc: 'NPC', intent: '裁决',
  kp: 'KP 叙事', combat: '战斗', backstage: '幕后',
}
const OUTCOME_LABEL: Record<string, string> = {
  ok: '成功', error: '出错', timeout: '超时', cancelled: '已取消', conflict: '冲突',
}
const SOURCE_FILTERS: Array<{ id: string; label: string }> = [
  { id: '', label: '全部' },
  { id: 'npc', label: 'NPC' },
  { id: 'combat', label: '战斗' },
]
const SOURCE_LABEL: Record<string, string> = { npc: 'NPC', combat: '战斗' }

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function fmtTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

/**
 * KP 运行轨迹：每次 Agent 调用的耗时 / token，与 NPC、战斗动作的意图历史。
 * 数据来自房主专用接口，不进入玩家视图。
 *
 * 做成右侧抽屉而不是浮在角落的小窗：它是「回头查一下这一轮慢在哪、谁为什么这么动」的
 * 排查面板，要竖着读一长列；入口收在顶栏「更多」里，平时不占屏幕。
 * 调用列表每行带一根耗时比例条（相对本页最慢的一次）——一眼找到拖慢回合的那一步。
 */
export function AgentTracePanel({ sessionId, open, onClose }: {
  sessionId: string
  open: boolean
  onClose: () => void
}) {
  const [tab, setTab] = useState<'traces' | 'intents'>('traces')
  const [items, setItems] = useState<TraceItem[]>([])
  const [intents, setIntents] = useState<IntentItem[]>([])
  const [filterSource, setFilterSource] = useState('')
  const [filterActor, setFilterActor] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadTraces = useCallback(() => {
    setLoading(true)
    setError('')
    api.get<{ items: TraceItem[] }>(`/sessions/${sessionId}/agent-traces?limit=50`)
      .then((data) => setItems(data.items || []))
      .catch((e) => { setItems([]); setError(e instanceof Error ? e.message : '读取失败') })
      .finally(() => setLoading(false))
  }, [sessionId])

  const loadIntents = useCallback(() => {
    setLoading(true)
    setError('')
    const params = new URLSearchParams({ limit: '50' })
    if (filterSource) params.set('source', filterSource)
    if (filterActor.trim()) params.set('actor', filterActor.trim())
    api.get<{ items: IntentItem[] }>('/sessions/' + sessionId + '/intent-history?' + params.toString())
      .then((data) => setIntents(data.items || []))
      .catch((e) => { setIntents([]); setError(e instanceof Error ? e.message : '读取失败') })
      .finally(() => setLoading(false))
  }, [filterActor, filterSource, sessionId])

  const load = useCallback(() => {
    if (tab === 'intents') loadIntents()
    else loadTraces()
  }, [loadIntents, loadTraces, tab])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const summary = useMemo(() => {
    const tokens = items.reduce((s, it) => s + (it.total_tokens || 0), 0)
    const avg = items.length ? items.reduce((s, it) => s + it.duration_ms, 0) / items.length : 0
    const failed = items.filter((it) => it.outcome !== 'ok').length
    const slowest = items.reduce((mx, it) => Math.max(mx, it.duration_ms), 0)
    return { tokens, avg, failed, slowest }
  }, [items])

  const drawer = (
    <AnimatePresence>
      {open && (
        <m.aside
          key="trace"
          role="dialog"
          aria-label="运行轨迹"
          className="trace-drawer"
          initial={{ x: '104%', opacity: 0.6 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: '104%', opacity: 0.6, transition: { duration: 0.18 } }}
          transition={spring.soft}
        >
          <header className="trace-head">
            <div className="min-w-0">
              <div className="trace-title"><GiScrollUnfurled aria-hidden /> 运行轨迹</div>
              <div className="trace-sub">每次 AI 调用的耗时与用量，以及 NPC / 战斗的动作意图 · 仅 KP 可见</div>
            </div>
            <button type="button" className="trace-icon-btn" onClick={load} disabled={loading} title="刷新" aria-label="刷新">
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
            <button type="button" className="trace-icon-btn" onClick={onClose} title="关闭（Esc）" aria-label="关闭">
              <X size={15} />
            </button>
          </header>

          <div className="trace-tabs" role="tablist">
            {([['traces', '调用轨迹'], ['intents', '动作意图']] as const).map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className={`trace-tab ${tab === id ? 'is-on' : ''}`} onClick={() => setTab(id)}>
                {tab === id && <m.span layoutId="trace-tab-bg" className="trace-tab-bg" transition={spring.snappy} />}
                {label}
              </button>
            ))}
          </div>

          {error && <div className="trace-error">{error}</div>}

          {tab === 'traces' && (
            <div className="trace-body">
              {items.length > 0 && (
                <div className="trace-summary">
                  <div><b>{items.length}</b><span>次调用</span></div>
                  <div><b>{summary.tokens.toLocaleString()}</b><span>合计 token</span></div>
                  <div><b>{fmtMs(Math.round(summary.avg))}</b><span>平均耗时</span></div>
                  <div className={summary.failed ? 'is-bad' : ''}><b>{summary.failed}</b><span>未成功</span></div>
                </div>
              )}
              {!items.length && !loading && <div className="trace-empty">还没有调用记录。下一轮生成后会出现。</div>}
              <ol className="trace-list">
                {items.map((it) => {
                  const ok = it.outcome === 'ok'
                  const ratio = summary.slowest ? it.duration_ms / summary.slowest : 0
                  return (
                    <li key={it.id} className={`trace-row ${ok ? '' : 'trace-row--bad'}`}>
                      <span className={`trace-agent trace-agent--${it.agent}`}>{AGENT_LABEL[it.agent] || it.agent}</span>
                      <span className="trace-row-main">
                        <span className="trace-row-top">
                          <span className="trace-stage">{it.stage || '—'}</span>
                          {!ok && <span className="trace-outcome">{OUTCOME_LABEL[it.outcome] || it.outcome}</span>}
                          <time className="trace-time">{fmtTime(it.created_at)}</time>
                        </span>
                        <span className="trace-row-bottom">
                          <span className="trace-bar"><i style={{ width: `${Math.max(3, ratio * 100)}%` }} /></span>
                          <span className="trace-ms">{fmtMs(it.duration_ms)}</span>
                          <span className="trace-tok" title={it.prompt_tokens != null ? `输入 ${it.prompt_tokens} · 输出 ${it.completion_tokens ?? 0}` : undefined}>
                            {it.total_tokens ? `${it.total_tokens.toLocaleString()} token` : '—'}
                          </span>
                        </span>
                      </span>
                    </li>
                  )
                })}
              </ol>
            </div>
          )}

          {tab === 'intents' && (
            <div className="trace-body">
              <div className="trace-filters">
                <span className="trace-seg">
                  {SOURCE_FILTERS.map((f) => (
                    <button key={f.id || 'all'} type="button" aria-pressed={filterSource === f.id} className={filterSource === f.id ? 'is-on' : ''} onClick={() => setFilterSource(f.id)}>
                      {f.label}
                    </button>
                  ))}
                </span>
                <input
                  className="input trace-search"
                  placeholder="按角色名或 id 筛选"
                  value={filterActor}
                  onChange={(e) => setFilterActor(e.target.value)}
                />
              </div>
              {!intents.length && !loading && <div className="trace-empty">还没有动作记录。NPC 或战斗动作后会出现。</div>}
              <ol className="trace-list">
                {intents.map((it, index) => (
                  <li key={`${it.source}-${it.actor_id}-${it.observes_seq}-${index}`} className={`trace-row trace-intent ${it.precondition_ok ? '' : 'trace-row--bad'}`}>
                    <span className={`trace-agent trace-agent--${it.source}`}>{SOURCE_LABEL[it.source] || it.source}</span>
                    <span className="trace-row-main">
                      <span className="trace-row-top">
                        <b className="trace-actor">{it.actor_name || it.actor_id}</b>
                        <span className="trace-verb">{it.verb || it.action}</span>
                        {it.target && <><GiArrowScope className="trace-target-ic" aria-label="目标" /><span className="trace-target">{it.target}</span></>}
                        {!it.precondition_ok && <span className="trace-outcome">前置不满足</span>}
                        <span className="trace-seq" title="动作依据的是第几条事件之后的局面">观测 #{it.observes_seq}</span>
                      </span>
                      {it.content && <span className="trace-content">{it.content}</span>}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </m.aside>
      )}
    </AnimatePresence>
  )

  if (typeof document === 'undefined') return null
  return createPortal(drawer, document.body)
}
