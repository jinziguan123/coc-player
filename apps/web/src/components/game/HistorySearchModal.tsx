import { useCallback, useEffect, useRef, useState } from 'react'
import { Search, X, ArrowDownWideNarrow, ArrowUpNarrowWide, ChevronLeft, ChevronRight } from 'lucide-react'
import { Modal } from '@/components/ui/modal'
import { api } from '../../api/client'

const PAGE_SIZE = 8

export interface SearchHit {
  id: string
  sequence_num: number
  event_type: string
  actor_name: string
  content: string
  created_at?: string | null
}

/** 事件类型 → 一眼能认的中文标签。结果混着旁白/台词/骰子，不标就得逐条读才知道是什么。 */
const TYPE_LABEL: Record<string, string> = {
  narration: '旁白',
  dialogue: '台词',
  action: '行动',
  dice: '骰子',
  ooc: '场外',
}

function timeOf(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/**
 * 把片段按关键词切开并高亮。
 *
 * 不用 dangerouslySetInnerHTML——片段是模型写的正文，里面什么字符都可能有。
 * 大小写不敏感（英文关键词），但切分用的是原文的下标，所以原文大小写原样保留。
 */
function Highlighted({ text, query }: { text: string; query: string }) {
  const q = query.trim()
  if (!q) return <>{text}</>
  const parts: Array<{ s: string; hit: boolean }> = []
  const lower = text.toLowerCase()
  const needle = q.toLowerCase()
  let i = 0
  while (i < text.length) {
    const at = lower.indexOf(needle, i)
    if (at < 0) { parts.push({ s: text.slice(i), hit: false }); break }
    if (at > i) parts.push({ s: text.slice(i, at), hit: false })
    parts.push({ s: text.slice(at, at + needle.length), hit: true })
    i = at + needle.length
  }
  return (
    <>
      {parts.map((p, idx) => (p.hit
        ? <mark key={idx} className="search-mark">{p.s}</mark>
        : <span key={idx}>{p.s}</span>
      ))}
    </>
  )
}

/**
 * 本局历史检索。
 *
 * 片段由后端以命中处为中心截取（见 session_service.search_snippet）——早先取的是正文
 * 前 140 字，关键词落在长旁白的后半截时就被切没了，看着像「这条不含关键词却被搜出来」。
 */
export function HistorySearchModal({
  sessionId,
  onClose,
  onJump,
}: {
  sessionId: string
  onClose: () => void
  onJump: (eventId: string) => void
}) {
  const [q, setQ] = useState('')
  const [order, setOrder] = useState<'desc' | 'asc'>('desc')
  const [page, setPage] = useState(0)
  const [hits, setHits] = useState<SearchHit[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** 请求序号：翻页/改序/改词会并发发出，只认最后一次的结果，避免旧响应盖新结果。 */
  const reqId = useRef(0)

  const fetchPage = useCallback(async (query: string, pageIdx: number, ord: 'desc' | 'asc') => {
    const keyword = query.trim()
    if (!keyword) { setHits([]); setTotal(0); return }
    const mine = ++reqId.current
    setLoading(true)
    try {
      const r = await api.get<{ total: number; results: SearchHit[] }>(
        `/sessions/${sessionId}/search?q=${encodeURIComponent(keyword)}`
        + `&limit=${PAGE_SIZE}&offset=${pageIdx * PAGE_SIZE}&order=${ord}`,
      )
      if (mine !== reqId.current) return
      setHits(r.results || [])
      setTotal(r.total || 0)
    } catch {
      if (mine === reqId.current) { setHits([]); setTotal(0) }
    } finally {
      if (mine === reqId.current) setLoading(false)
    }
  }, [sessionId])

  // 改关键词：防抖，并回到第一页（换了词还停在第 5 页多半是空的）
  const onQueryChange = (value: string) => {
    setQ(value)
    setPage(0)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => fetchPage(value, 0, order), 250)
  }

  // 翻页/改排序：立即取，不防抖（是点击而非连续输入）
  useEffect(() => {
    if (q.trim()) fetchPage(q, page, order)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, order])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const empty = q.trim() && !loading && hits.length === 0

  return (
    <Modal onClose={onClose} widthClass="max-w-2xl" align="top">
      <div className="flex flex-col" style={{ maxHeight: '72vh' }}>
        {/* 检索框 */}
        <div
          className="flex items-center gap-2 px-3 py-2 border-b flex-shrink-0"
          style={{ borderColor: 'var(--color-border)' }}
        >
          <Search size={16} style={{ color: 'var(--color-text-secondary)' }} />
          <input
            autoFocus
            value={q}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') onClose() }}
            placeholder="检索本局历史（旁白 / 台词 / 行动 / 骰子 / 场外）…"
            className="input flex-1 !py-1 text-sm"
          />
          <button onClick={onClose} title="关闭检索（Esc）" style={{ color: 'var(--color-text-secondary)' }}>
            <X size={16} />
          </button>
        </div>

        {/* 命中条数 + 排序：只在真的有结果时占位，空态下不该有一排灰控件 */}
        {total > 0 && (
          <div
            className="flex items-center gap-2 px-3 py-1.5 border-b flex-shrink-0 text-xs"
            style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
          >
            <span>
              命中 <strong className="font-mono" style={{ color: 'var(--color-text-accent)' }}>{total}</strong> 条
            </span>
            <button
              onClick={() => { setOrder((o) => (o === 'desc' ? 'asc' : 'desc')); setPage(0) }}
              className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded border transition-colors hover:bg-[var(--color-bg-tertiary)]"
              style={{ borderColor: 'var(--color-border)' }}
              title="切换时间排序"
            >
              {order === 'desc'
                ? <><ArrowDownWideNarrow size={12} /> 由新到旧</>
                : <><ArrowUpNarrowWide size={12} /> 由旧到新</>}
            </button>
          </div>
        )}

        {/* 结果 */}
        <div className="min-h-0 flex-1 overflow-y-auto chat-scroll p-2 flex flex-col gap-1.5">
          {!q.trim() ? (
            <p className="text-xs px-2 py-8 text-center" style={{ color: 'var(--color-text-secondary)' }}>
              输入关键词以检索本局历史记录，点结果可跳转到对应位置
            </p>
          ) : empty ? (
            <p className="text-xs px-2 py-8 text-center" style={{ color: 'var(--color-text-secondary)' }}>
              没有匹配「{q.trim()}」的记录
            </p>
          ) : hits.map((h) => (
            <button
              key={h.id}
              onClick={() => { onJump(h.id); onClose() }}
              className="search-result text-left"
              title="跳转到该记录"
            >
              <div className="flex items-baseline gap-1.5 mb-0.5">
                <span className="search-result-type">{TYPE_LABEL[h.event_type] || h.event_type}</span>
                <span className="text-xs font-medium truncate" style={{ color: 'var(--color-text-accent)' }}>
                  {h.actor_name || '旁白'}
                </span>
                <span className="ml-auto flex-shrink-0 text-[0.6rem] font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                  {timeOf(h.created_at)}
                </span>
              </div>
              <p className="text-xs leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                <Highlighted text={h.content} query={q} />
              </p>
            </button>
          ))}
        </div>

        {/* 分页 */}
        {totalPages > 1 && (
          <div
            className="flex items-center justify-center gap-3 px-3 py-1.5 border-t flex-shrink-0 text-xs"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page <= 0}
              className="btn-secondary !px-2 !py-0.5 !text-xs disabled:opacity-40 inline-flex items-center gap-1"
            >
              <ChevronLeft size={12} /> 上一页
            </button>
            <span className="font-mono" style={{ color: 'var(--color-text-secondary)' }}>
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="btn-secondary !px-2 !py-0.5 !text-xs disabled:opacity-40 inline-flex items-center gap-1"
            >
              下一页 <ChevronRight size={12} />
            </button>
          </div>
        )}
      </div>
    </Modal>
  )
}
