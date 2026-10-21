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
  reasoning_tokens?: number | null
  cache_read_tokens?: number | null
  cache_write_tokens?: number | null
  calls?: number | null
  created_at: string | null
}

interface Usage {
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  total_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  calls: number
}

/** 后端 agent_trace.usage_summary：按环节的累计（不受轨迹条数裁剪影响）与本局累计的差额。 */
export interface UsageSummary {
  agents: Array<Usage & { agent: string }>
  session_total: Usage
  since: Usage
  traced: Usage
  untraced: Usage
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
  validator: '终检', style: '去口癖', summary: '摘要', chronicle: '小传',
  illustration: '配图', advisor: '参谋',
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

function fmtTok(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

function pct(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '—'
}

/** 单条轨迹的悬停明细：入/出之外，思考与缓存命中决定「钱花得值不值」。 */
function rowUsageTitle(it: TraceItem): string | undefined {
  if (it.prompt_tokens == null) return undefined
  const lines = [`输入 ${it.prompt_tokens} · 输出 ${it.completion_tokens ?? 0}`]
  if (it.calls) lines.push(`${it.calls} 次调用`)
  if (it.reasoning_tokens) lines.push(`其中思考 ${it.reasoning_tokens}`)
  if (it.cache_read_tokens) lines.push(`缓存命中 ${it.cache_read_tokens}（${pct(it.cache_read_tokens, it.prompt_tokens)}）`)
  return lines.join('\n')
}

/**
 * 按环节汇总：哪一环花得最多、缓存有没有生效、输出里多少是被丢弃的思考。
 * 末行对账：各环节之和与「本局消耗」的差额——没挂在任何环节上的调用，理论上接近 0。
 */
export function UsageBreakdown({ summary }: { summary: UsageSummary }) {
  const { agents, untraced, since, session_total: total } = summary
  if (!agents.length && !total.total_tokens) return null
  const scoped = total.total_tokens - since.total_tokens
  const max = agents.reduce((m, a) => Math.max(m, a.total_tokens), 0)
  return (
    <section className="trace-usage" aria-label="按环节的用量">
      <div className="trace-usage-head">
        <span>环节</span><span>token</span><span title="输入里命中提示词缓存的比例（命中部分约按 1/10 计费）">缓存命中</span><span title="输出里模型思考的比例（思考内容生成完即被丢弃）">思考占比</span>
      </div>
      {agents.map((a) => (
        <div key={a.agent} className="trace-usage-row">
          <span className={`trace-agent trace-agent--${a.agent}`}>{AGENT_LABEL[a.agent] || a.agent}</span>
          <span className="trace-usage-tok" title={`${a.calls} 次调用 · 输入 ${a.prompt_tokens} · 输出 ${a.completion_tokens}`}>
            <span className="trace-bar"><i style={{ width: `${max ? Math.max(3, (a.total_tokens / max) * 100) : 0}%` }} /></span>
            {fmtTok(a.total_tokens)}
          </span>
          <span>{a.cache_read_tokens ? pct(a.cache_read_tokens, a.prompt_tokens) : '—'}</span>
          <span>{a.reasoning_tokens ? pct(a.reasoning_tokens, a.completion_tokens) : '—'}</span>
        </div>
      ))}
      <div
        className={`trace-usage-diff ${Math.abs(untraced.total_tokens) > scoped * 0.02 ? 'is-bad' : ''}`}
        title={since.total_tokens
          ? `按环节记账启用前本局已累计 ${fmtTok(since.total_tokens)}，不计入对账`
          : undefined}
      >
        各环节合计 {fmtTok(summary.traced.total_tokens)} / 本局消耗 {fmtTok(scoped)}
        {' · '}未归入环节 {fmtTok(untraced.total_tokens)}（{untraced.calls} 次调用）
      </div>
    </section>
  )
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
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [intents, setIntents] = useState<IntentItem[]>([])
  const [filterSource, setFilterSource] = useState('')
  const [filterActor, setFilterActor] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadTraces = useCallback(() => {
    setLoading(true)
    setError('')
    api.get<{ items: TraceItem[]; summary?: UsageSummary }>(`/sessions/${sessionId}/agent-traces?limit=50`)
      .then((data) => { setItems(data.items || []); setUsage(data.summary ?? null) })
      .catch((e) => { setItems([]); setUsage(null); setError(e instanceof Error ? e.message : '读取失败') })
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
                  <div><b>{summary.tokens.toLocaleString()}</b><span>本页 token</span></div>
                  <div><b>{fmtMs(Math.round(summary.avg))}</b><span>平均耗时</span></div>
                  <div className={summary.failed ? 'is-bad' : ''}><b>{summary.failed}</b><span>未成功</span></div>
                </div>
              )}
              {usage && <UsageBreakdown summary={usage} />}
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
                          <span className="trace-tok" title={rowUsageTitle(it)}>
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
