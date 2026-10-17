import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { GiScrollUnfurled } from 'react-icons/gi'
import { api } from '../../api/client'

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
  planner: 'planner', team: '队友', npc: 'NPC', intent: '裁决',
  kp: 'KP', combat: '战斗', backstage: '幕后',
}

const SOURCE_LABEL: Record<string, string> = { npc: 'NPC', combat: '战斗' }

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

/**
 * KP 运行抽屉：调用耗时 / token 与 NPC/战斗动作意图历史。
 * 数据来自房主专用接口，不进入玩家视图。
 */
export function AgentTracePanel({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false)
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
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const panel = (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="fixed right-4 bottom-24 z-40 btn-xs rounded border inline-flex items-center gap-1"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)', color: 'var(--color-text-secondary)' }}
        title="查看 Agent 调用耗时与动作意图"
      >
        <GiScrollUnfurled size={14} />
        运行轨迹
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="运行轨迹"
          className="fixed right-4 bottom-36 z-50 w-[min(620px,94vw)] max-h-[64vh] overflow-auto rounded border p-3 shadow-lg"
          style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
        >
          <div className="flex items-center justify-between gap-3 mb-2">
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="btn-xs rounded border px-2"
                onClick={() => setTab('traces')}
                style={{ borderColor: tab === 'traces' ? 'var(--color-text-secondary)' : 'var(--color-border)' }}
              >
                调用轨迹
              </button>
              <button
                type="button"
                className="btn-xs rounded border px-2"
                onClick={() => setTab('intents')}
                style={{ borderColor: tab === 'intents' ? 'var(--color-text-secondary)' : 'var(--color-border)' }}
              >
                动作意图
              </button>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" className="btn-xs rounded border px-2" onClick={load} disabled={loading}>
                {loading ? '读取中…' : '刷新'}
              </button>
              <button type="button" className="btn-xs rounded border px-2" onClick={() => setOpen(false)}>
                关闭
              </button>
            </div>
          </div>

          {tab === 'traces' && (
            <>
              {error && (
                <div className="text-xs mb-2" style={{ color: 'var(--color-danger)' }}>{error}</div>
              )}
              {!items.length && !loading && (
                <div className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  还没有调用记录。下一轮生成后会出现。
                </div>
              )}
              <div className="space-y-1">
                {items.map((it) => (
                  <div
                    key={it.id}
                    className="grid grid-cols-[5.5rem_4.5rem_4rem_1fr] gap-2 items-baseline rounded px-2 py-1 text-xs"
                    style={{ background: 'var(--color-surface-raised, transparent)' }}
                  >
                    <span className="font-medium">{AGENT_LABEL[it.agent] || it.agent}</span>
                    <span style={{ color: it.outcome === 'ok' ? 'var(--color-text-secondary)' : 'var(--color-danger)' }}>
                      {it.outcome}
                    </span>
                    <span className="font-mono tabular-nums">{fmtMs(it.duration_ms)}</span>
                    <span className="truncate" style={{ color: 'var(--color-text-secondary)' }}>
                      {it.total_tokens ? `${it.total_tokens} tok` : '—'}
                      {it.stage ? ` · ${it.stage}` : ''}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}

          {tab === 'intents' && (
            <>
              {error && (
                <div className="text-xs mb-2" style={{ color: 'var(--color-danger)' }}>{error}</div>
              )}
              <div className="flex items-center gap-2 mb-2">
                <select
                  className="btn-xs rounded border px-2"
                  style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
                  value={filterSource}
                  onChange={(e) => setFilterSource(e.target.value)}
                >
                  <option value="">全部来源</option>
                  <option value="npc">NPC</option>
                  <option value="combat">战斗</option>
                </select>
                <input
                  className="btn-xs rounded border px-2 min-w-0 flex-1"
                  style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
                  placeholder="按角色 id / 名字筛选"
                  value={filterActor}
                  onChange={(e) => setFilterActor(e.target.value)}
                />
              </div>
              {!intents.length && !loading && (
                <div className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  还没有动作记录。NPC 或战斗动作后会出现。
                </div>
              )}
              <div className="space-y-1">
                {intents.map((it, index) => (
                  <div
                    key={`${it.source}-${it.actor_id}-${it.observes_seq}-${index}`}
                    className="grid grid-cols-[4.5rem_5rem_1fr_5.5rem] gap-2 items-baseline rounded px-2 py-1 text-xs"
                    style={{ background: 'var(--color-surface-raised, transparent)' }}
                  >
                    <span className="font-medium">{SOURCE_LABEL[it.source] || it.source}</span>
                    <span className="truncate">{it.actor_name || it.actor_id}</span>
                    <span className="truncate" style={{ color: 'var(--color-text-secondary)' }}>
                      {it.verb || it.action}
                      {it.target ? ` -> ${it.target}` : ''}
                      {it.content ? ` · ${it.content}` : ''}
                    </span>
                    <span
                      className="font-mono tabular-nums text-right"
                      style={{ color: it.precondition_ok ? 'var(--color-text-secondary)' : 'var(--color-danger)' }}
                    >
                      {it.precondition_ok ? 'ok' : '前置不满足'}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </>
  )

  if (typeof document === 'undefined') return null
  return createPortal(panel, document.body)
}
