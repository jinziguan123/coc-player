import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api, getApiBase, getPlayerToken, localApi } from '../api/client'
import { ConfirmDialog } from '../components/ui/confirm-dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { GiBookCover, GiUpCard, GiReturnArrow, GiMagnifyingGlass, GiScrollUnfurled } from 'react-icons/gi'
import { VillageRulesPanel } from '@/components/game/VillageRulesPanel'
import { staggerStyle } from '@/lib/stagger'

interface Rulebook {
  id: string
  title: string
  rule_system: string
  page_count: number
  chunk_count: number
  status: string
  embed_model: string
  error: string
}

interface RuleHit {
  text: string
  page: number
  score: number
  rulebook_id: string
}

const STATUS_LABEL: Record<string, string> = {
  indexing: '索引中…',
  ready: '可检索',
  failed: '失败',
}
/** 状态徽标取色：走主题语义色而非写死十六进制，羊皮纸主题下同样可读。 */
function statusChipStyle(status: string): React.CSSProperties {
  const tone = ({
    indexing: 'var(--color-dice-gold)',
    ready: 'var(--color-success)',
    failed: 'var(--color-danger)',
  } as Record<string, string>)[status]
  return tone ? { color: tone, borderColor: tone } : {}
}

export function RulebookPage() {
  const navigate = useNavigate()
  const fileRef = useRef<HTMLInputElement>(null)
  const [books, setBooks] = useState<Rulebook[]>([])
  const [ruleSystem, setRuleSystem] = useState('coc')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  // 测试检索
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<RuleHit[] | null>(null)
  const [searching, setSearching] = useState(false)

  const fetchBooks = useCallback(async () => {
    try {
      setBooks(await api.get<Rulebook[]>('/rulebooks'))
    } catch {
      /* 静默：列表拉取失败不打扰 */
    }
  }, [])

  useEffect(() => {
    fetchBooks()
  }, [fetchBooks])

  // 有规则书处于索引中时轮询，直到全部 ready/failed
  useEffect(() => {
    if (!books.some((b) => b.status === 'indexing')) return
    const t = setInterval(fetchBooks, 2000)
    return () => clearInterval(t)
  }, [books, fetchBooks])

  const pickFile = (f: File | undefined) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.pdf')) {
      toast.error('规则书目前只支持 PDF')
      return
    }
    setFile(f)
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    pickFile(e.dataTransfer.files[0])
  }, [])

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const params = new URLSearchParams({ rule_system: ruleSystem, title: file.name.replace(/\.pdf$/i, '') })
      const res = await fetch(`${getApiBase()}/rulebooks/upload?${params.toString()}`, {
        method: 'POST',
        headers: { 'X-Player-Token': getPlayerToken() },
        body: form,
      })
      if (!res.ok) throw new Error(await res.text())
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      toast.success('已上传，正在后台建立索引…')
      fetchBooks()
    } catch (e) {
      toast.error(`上传失败：${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setUploading(false)
    }
  }

  const deleteBook = async (id: string) => {
    try {
      await localApi.delete(`/rulebooks/${id}`)
      fetchBooks()
      toast.success('规则书已删除')
    } catch {
      toast.error('删除失败')
    }
  }

  const runSearch = async () => {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    try {
      const params = new URLSearchParams({ q, rule_system: ruleSystem, k: '3' })
      const data = await api.get<{ query: string; hits: RuleHit[] }>(`/rulebooks/search?${params.toString()}`)
      setHits(data.hits)
    } catch {
      toast.error('检索失败')
    } finally {
      setSearching(false)
    }
  }

  const hasReady = books.some((b) => b.status === 'ready' && b.rule_system === ruleSystem)

  return (
    <div className="max-w-[100rem]">
      <div className="page-head">
        <button onClick={() => navigate(-1)} className="btn-secondary btn-sm flex items-center gap-1">
          <GiReturnArrow /> 返回
        </button>
        <h2 className="page-title">规则书</h2>
      </div>

      <p className="text-sm mb-5 max-w-3xl" style={{ color: 'var(--color-text-secondary)' }}>
        上传规则书 PDF（如《守秘人规则书》），系统会在本地建立可检索索引。游戏中守秘人遇到拿不准的精确规则时，会按需查阅规则书原文再裁定。
      </p>

      {/* 两栏：左边是「装了哪几本书」（卡片网格，要宽），右边是「这套规则怎么跑」（表单，要窄）。
          此前一栏到底，村规表单被拉到一千多像素宽，每行只剩一个标题和一个贴在最右的控件，
          中间六百像素空白；三种卡片又是 768 / 1018 / 503 三种宽度，左右边缘全不对齐。 */}
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-6">
          <div className="card">
            <h3 className="card-title flex items-center gap-2">
              <GiUpCard /> 上传规则书
            </h3>

            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => fileRef.current?.click()}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileRef.current?.click() }}
              role="button"
              tabIndex={0}
              className={`dropzone mb-3 ${dragOver ? 'dropzone--over' : ''}`}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = '' }}
              />
              {file ? (
                <div>
                  <p className="text-sm font-semibold" style={{ color: 'var(--color-text-accent)' }}>{file.name}</p>
                  <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                    {(file.size / 1024 / 1024).toFixed(1)} MB · 点击可重选
                  </p>
                </div>
              ) : (
                <div>
                  <GiBookCover className="dropzone-icon" aria-hidden="true" />
                  <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>拖拽 PDF 到此处，或点击选择</p>
                  <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)', opacity: 0.7 }}>
                    仅支持含文字层的 PDF（扫描件需先 OCR）
                  </p>
                </div>
              )}
            </div>

            <div className="flex gap-3 items-center">
              <Select value={ruleSystem} onValueChange={setRuleSystem}>
                <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="coc">CoC</SelectItem>
                  <SelectItem value="dnd">DnD</SelectItem>
                </SelectContent>
              </Select>
              <button onClick={handleUpload} disabled={uploading || !file} className="btn-primary">
                {uploading ? '上传中…' : '上传并索引'}
              </button>
            </div>
          </div>

          {books.length === 0 ? (
            <div className="empty-state">
              <span className="empty-state-icon"><GiBookCover /></span>
              <span className="empty-state-title">尚无规则书</span>
              <span className="empty-state-hint">
                上传规则书 PDF（如《守秘人规则书》），系统在本地建立可检索索引；
                跑团时守秘人遇到拿不准的精确规则，会按需查阅原文再裁定。
              </span>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
              {books.map((b, i) => (
                <div
                  key={b.id}
                  style={staggerStyle(i)}
                  className="card entity-card list-enter !p-0 flex flex-col overflow-hidden"
                >
                  <div
                    className="flex items-start gap-2.5 px-3 pt-3 pb-2.5"
                    style={{ borderBottom: '1px solid var(--color-border)' }}
                  >
                    <span className="char-sigil" aria-hidden="true"><GiBookCover /></span>
                    <div className="min-w-0 flex-1">
                      <h3 className="card-title !mb-0.5 truncate !text-[length:var(--text-base)]" title={b.title}>
                        {b.title}
                      </h3>
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="chip chip--accent">{b.rule_system.toUpperCase()}</span>
                        <span className="chip" style={statusChipStyle(b.status)}>
                          {STATUS_LABEL[b.status] || b.status}
                        </span>
                      </div>
                    </div>
                    <div className="entity-card-actions flex flex-shrink-0 items-center gap-1">
                      <ConfirmDialog
                        title="删除规则书"
                        description={`确定要删除「${b.title}」及其索引吗？此操作不可恢复。`}
                        confirmLabel="删除"
                        onConfirm={() => deleteBook(b.id)}
                      >
                        {(open) => (
                          <button
                            onClick={open}
                            className="chip chip--danger chip-btn chip-btn--danger"
                          >
                            删除
                          </button>
                        )}
                      </ConfirmDialog>
                    </div>
                  </div>
                  <div className="flex flex-1 flex-col px-3 py-2">
                    {/* 页数/片段数是次要信息，此前用两块 stat-tile 撑掉了半张卡的高度——
                        读者真正要认的是书名和它能不能检索。降级成一行 meta 文字。 */}
                    <div
                      className="flex flex-wrap items-center gap-x-2 gap-y-0.5"
                      style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}
                    >
                      <span>{b.page_count} 页</span>
                      <span aria-hidden="true">·</span>
                      <span>{b.chunk_count} 个片段</span>
                      {b.embed_model && (
                        <>
                          <span aria-hidden="true">·</span>
                          <span className="truncate" title={b.embed_model}>{b.embed_model}</span>
                        </>
                      )}
                    </div>
                    {b.status === 'failed' && b.error && (
                      <p className="mt-1.5" style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-danger)' }}>
                        错误：{b.error}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {hasReady && (
            <div className="card">
              <h3 className="card-title flex items-center gap-2">
                <GiMagnifyingGlass /> 测试检索
              </h3>
              <div className="flex gap-2">
                {/* 与全站输入框同一质感（.input）：此前这里手搓内联样式，聚焦态没有琥珀辉光 */}
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') runSearch() }}
                  placeholder="输入规则关键词，如「孤注一掷」「理智丧失」"
                  className="input flex-1"
                />
                <button onClick={runSearch} disabled={searching || !query.trim()} className="btn-secondary">
                  {searching ? '检索中…' : '检索'}
                </button>
              </div>
              {hits && (
                <div className="space-y-2 mt-3">
                  {hits.length === 0 ? (
                    <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>无匹配片段</p>
                  ) : (
                    hits.map((h, i) => (
                      <div key={i} className="px-3 py-2 rounded text-sm" style={{ background: 'var(--color-bg-tertiary)' }}>
                        <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>
                          第 {h.page} 页 · 相关度 {h.score.toFixed(3)}
                        </div>
                        {h.text}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 村规：这一桌沿用的规则改动，跟着上面那个规则系统选择器走。
            和「装了哪几本规则书」同属「这套规则怎么跑」这件事，所以同页；
            但它是表单不是列表，收在窄侧栏里才不至于把每一行拉成大片空白。
            max-w 只在堆叠时起作用（xl 下列宽 21rem 已经更窄）。 */}
        <aside className="min-w-0 max-w-md" aria-label="村规">
          <div className="card">
            <h3 className="card-title flex items-center gap-2">
              <GiScrollUnfurled /> 村规 · {ruleSystem.toUpperCase()}
            </h3>
            <VillageRulesPanel ruleSystem={ruleSystem} />
          </div>
        </aside>
      </div>
    </div>
  )
}
