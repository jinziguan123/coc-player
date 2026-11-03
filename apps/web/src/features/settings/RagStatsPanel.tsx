import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { api } from '@/api/client'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

interface SessionListItem {
  id: string
  module_title: string | null
  character_name: string | null
  status?: string
}

interface RagQuadrant {
  calls: number
  empty: number
  total_hits: number
  hit_rate: number
  avg_top_score: number
}

interface RagSample {
  kind: string
  mode: string
  query: string
  n_hits: number
  top_score: number
}

interface RagStats {
  totals: { calls: number; total_hits: number; empty: number; hit_rate: number }
  by_kind_mode: Record<string, RagQuadrant>
  recent: RagSample[]
}

// 四象限固定顺序与中文标签（kind:mode）
const RAG_QUADRANTS: { key: string; label: string }[] = [
  { key: 'rule:active', label: '规则书 · 主动查阅' },
  { key: 'rule:passive', label: '规则书 · 被动注入' },
  { key: 'module:active', label: '模组原文 · 主动查阅' },
  { key: 'module:passive', label: '模组原文 · 被动注入' },
]

const pct = (x: number) => `${Math.round((x || 0) * 100)}%`
const sessionLabel = (s: SessionListItem) =>
  [s.module_title || '未命名模组', s.character_name || '—'].join(' · ')

/** 按局统计规则书 / 模组原文检索（RAG）的调用次数与命中质量（设置页「检索」标签）。 */
export function RagStatsPanel() {
  const [sessions, setSessions] = useState<SessionListItem[]>([])
  const [selected, setSelected] = useState<string>('')
  const [stats, setStats] = useState<RagStats | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.get<SessionListItem[]>('/sessions')
      .then((list) => {
        setSessions(list)
        if (list.length && !selected) setSelected(list[0].id)
      })
      .catch(() => {})
    // 仅首次拉会话列表
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = useCallback((sid: string) => {
    if (!sid) return
    setLoading(true)
    api.get<RagStats>(`/sessions/${sid}/rag-stats`)
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (selected) load(selected)
    else setStats(null)
  }, [selected, load])

  const t = stats?.totals
  const empty = !t || t.calls === 0

  return (
    <div>
      <div className="card">
        <h3 className="card-title">检索用量与命中质量</h3>
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}>
          按局统计规则书 / 模组原文检索（RAG）的调用次数与命中质量，判断这套检索对跑团的实际帮助。
          <br />
          主动＝KP 发起的查阅；被动＝建上下文时按情境预取。命中率低 / 空命中多，说明语料覆盖或检索组织有待改进。
        </p>

        <div className="rag-toolbar" style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '1rem' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Select value={selected} onValueChange={setSelected}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={sessions.length ? '选择一局游戏' : '暂无游戏'} />
              </SelectTrigger>
              <SelectContent>
                {sessions.map((s) => (
                  <SelectItem key={s.id} value={s.id}>{sessionLabel(s)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <button
            className="btn-secondary text-xs"
            onClick={() => selected && load(selected)}
            disabled={!selected || loading}
            style={{ flexShrink: 0 }}
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>

        {!selected ? (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>请选择一局游戏查看。</p>
        ) : empty ? (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            本局尚无 RAG 调用记录（未挂规则书/模组原文索引，或还没跑过需要检索的回合）。
          </p>
        ) : (
          <>
            {/* 总计 */}
            <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
              <Stat label="总检索" value={String(t!.calls)} />
              <Stat label="命中率" value={pct(t!.hit_rate)} />
              <Stat label="命中片段" value={String(t!.total_hits)} />
              <Stat label="空命中" value={String(t!.empty)} />
            </div>

            {/* 四象限 */}
            <div className="rag-table-wrap" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                <thead>
                  <tr style={{ color: 'var(--color-text-secondary)', textAlign: 'left' }}>
                    <th style={ragTh}>类别</th>
                    <th style={ragTh}>调用</th>
                    <th style={ragTh}>命中率</th>
                    <th style={ragTh}>空命中</th>
                    <th style={ragTh}>平均 top 分</th>
                  </tr>
                </thead>
                <tbody>
                  {RAG_QUADRANTS.map(({ key, label }) => {
                    const q = stats!.by_kind_mode[key]
                    return (
                      <tr key={key} style={{ borderTop: '1px solid var(--color-border)' }}>
                        <td style={ragTd}>{label}</td>
                        <td style={ragTd}>{q?.calls ?? 0}</td>
                        <td style={ragTd}>{q ? pct(q.hit_rate) : '—'}</td>
                        <td style={ragTd}>{q?.empty ?? 0}</td>
                        <td style={ragTd}>{q?.avg_top_score != null ? q.avg_top_score.toFixed(3) : '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* 最近样本 */}
            {stats!.recent.length > 0 && (
              <div style={{ marginTop: '1.25rem' }}>
                <div className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.5rem' }}>
                  最近 {stats!.recent.length} 次检索
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                  {stats!.recent.map((r, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex', gap: '0.6rem', alignItems: 'baseline',
                        fontSize: '0.78rem', color: 'var(--color-text-primary)',
                      }}
                    >
                      <span style={{ color: 'var(--color-text-secondary)', flexShrink: 0, width: '9.5rem' }}>
                        {RAG_QUADRANTS.find((x) => x.key === `${r.kind}:${r.mode}`)?.label
                          ?? `${r.kind}:${r.mode}`}
                      </span>
                      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.query || '（空 query）'}
                      </span>
                      <span style={{
                        flexShrink: 0,
                        color: r.n_hits ? 'var(--color-text-secondary)' : 'var(--color-accent)',
                      }}>
                        {r.n_hits ? `${r.n_hits} 命中 · ${r.top_score.toFixed(3)}` : '未命中'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

const ragTh: CSSProperties = { padding: '0.4rem 0.6rem', fontWeight: 600 }
const ragTd: CSSProperties = { padding: '0.4rem 0.6rem' }

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
      <span style={{ fontSize: '0.72rem', color: 'var(--color-text-secondary)' }}>{label}</span>
      <span style={{ fontFamily: 'var(--font-title)', fontSize: '1.15rem', color: 'var(--color-text-primary)' }}>
        {value}
      </span>
    </div>
  )
}
