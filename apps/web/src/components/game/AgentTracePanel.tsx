import { useCallback, useEffect, useState } from 'react'
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

const AGENT_LABEL: Record<string, string> = {
  planner: 'planner',
  team: '队友',
  npc: 'NPC',
  intent: '裁决',
  kp: 'KP',
  combat: '战斗',
  backstage: '幕后',
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

/**
 * 运行轨迹抽屉：只给真人 KP 看，用来把一轮的耗时拆到具体 Agent。
 * 数据来自 agent_trace_logs（房主专用接口），不进入玩家视图。
 */
export function AgentTracePanel({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<TraceItem[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.get<{ items: TraceItem[] }>(`/sessions/${sessionId}/agent-traces?limit=50`)
      .then((data) => setItems(data.items || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [sessionId])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="fixed right-4 bottom-24 z-40 btn-xs rounded border inline-flex items-center gap-1"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)', color: 'var(--color-text-secondary)' }}
        title="查看本轮各 Agent 的调用耗时与 token"
      >
        <GiScrollUnfurled size={14} />
        调用轨迹
      </button>
      {open && (
        <div
          className="fixed right-4 bottom-36 z-40 w-[min(560px,92vw)] max-h-[62vh] overflow-auto rounded border p-3 shadow-lg"
          style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
        >
          <div className="flex items-center justify-between gap-3 mb-2">
            <div className="text-sm font-semibold">Agent 调用轨迹</div>
            <div className="flex items-center gap-2">
              <button type="button" className="btn-xs rounded border px-2" onClick={load} disabled={loading}>
                {loading ? '读取中…' : '刷新'}
              </button>
              <button type="button" className="btn-xs rounded border px-2" onClick={() => setOpen(false)}>
                关闭
              </button>
            </div>
          </div>
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
        </div>
      )}
    </>
  )
}
