import { useEffect, useRef, useState, useCallback } from 'react'
import { toast } from 'sonner'
import { api, getApiBase, getPlayerToken, localApi } from '../api/client'
import { ConfirmDialog } from '../components/ui/confirm-dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { GiBookCover, GiMagnifyingGlass, GiScrollUnfurled } from 'react-icons/gi'
import { VillageRulesPanel } from '@/components/game/VillageRulesPanel'
import { VillageRulesSummary } from '@/components/game/VillageRulesSummary'
import { ArchiveHead } from '@/components/layout/ArchiveHead'
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

/** 摘要要的那点东西：差异项、桌面约定、总开关。完整回显交给折叠里的配置面板。 */
interface VillageRulesBrief {
  options: Record<string, unknown>
  table_notes: string
  enabled?: boolean
}

export function RulebookPage() {
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
  const [searchOpen, setSearchOpen] = useState(false)

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

  const [rules, setRules] = useState<VillageRulesBrief | null>(null)
  const [rulesOpen, setRulesOpen] = useState(false)

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

  // 摘要与配置面板各拉各的：面板是折叠的、展开才挂载，而摘要要一进页就在。
  // 面板保存后回调这里重取，免得摘要还停在旧值上。
  const fetchRules = useCallback(async () => {
    try {
      setRules(await api.get<VillageRulesBrief>(`/rulebooks/village-rules/${ruleSystem}`))
    } catch {
      setRules(null)   // 读不到就不显示摘要，不打扰——规则书那块照常可用
    }
  }, [ruleSystem])
  useEffect(() => { void fetchRules() }, [fetchRules])

  return (
    <div className="max-w-[100rem]">
      <ArchiveHead title="规则书" stats={[{ label: '已装载', value: books.length }]} />

      {/* 顺序按「谁来看、看什么」排，不按「谁改得多」：
          玩家（含联机进来的客人，他们连村规都改不了——端点限本机）来这一页只想知道
          「我在什么规则下掷骰」，那件事该一眼看完，所以规矩摘要排最前、且只列与原文
          不同的那几项。改村规是房主的低频动作，收进「调整」里；书库拿回整幅宽度——
          这一页的名字就叫规则书。 */}
      <div className="space-y-6">
        <div className="card">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="card-title !mb-0 flex items-center gap-2">
              <GiScrollUnfurled /> 本桌规矩 · {ruleSystem.toUpperCase()}
            </h3>
            <button
              onClick={() => setRulesOpen((v) => !v)}
              aria-expanded={rulesOpen}
              className="btn-secondary btn-sm"
            >
              {rulesOpen ? '收起' : '调整'}
            </button>
          </div>

          {rules && (
            <VillageRulesSummary
              options={rules.options} notes={rules.table_notes} enabled={rules.enabled !== false}
            />
          )}

          {rulesOpen && (
            <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--color-border)' }}>
              <VillageRulesPanel ruleSystem={ruleSystem} twoColumn onSaved={fetchRules} />
            </div>
          )}
        </div>

        <div className="min-w-0" aria-label="规则书库">
          {/* 书库与上传合成一张卡：上传是**低频入口动作**，此前那块大拖拽区占掉首屏三分之一，
              把真正的主体（装了哪几本书）挤到折叠线以下。收成一行工具条，整张卡仍可拖入 PDF。 */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className="card"
            style={dragOver ? {
              borderColor: 'var(--color-accent)',
              boxShadow: '0 0 0 1px var(--color-accent) inset',
            } : undefined}
          >
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h3 className="card-title !mb-0 flex items-center gap-2">
                <GiBookCover /> 已装载的规则书
              </h3>
              <div className="flex flex-wrap items-center gap-2">
                <Select value={ruleSystem} onValueChange={setRuleSystem}>
                  <SelectTrigger className="w-24 text-sm" aria-label="规则系统"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="coc">CoC</SelectItem>
                    <SelectItem value="dnd">DnD</SelectItem>
                  </SelectContent>
                </Select>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".pdf"
                  className="hidden"
                  onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = '' }}
                />
                <button
                  onClick={() => fileRef.current?.click()}
                  className="btn-secondary btn-sm max-w-[14rem] truncate"
                  title={file ? `${file.name}（点击可重选）` : '选择要索引的 PDF；也可直接拖进这张卡'}
                >
                  {file ? `${file.name}（${(file.size / 1024 / 1024).toFixed(1)} MB）` : '选择 PDF…'}
                </button>
                <button
                  onClick={handleUpload}
                  disabled={uploading || !file}
                  className="btn-primary btn-sm"
                >
                  {uploading ? '上传中…' : '上传并索引'}
                </button>
              </div>
            </div>

            {books.length === 0 ? (
              <div className="empty-state !py-8">
                <span className="empty-state-icon"><GiBookCover /></span>
                <span className="empty-state-title">尚无规则书</span>
                <span className="empty-state-hint">
                  把规则书 PDF（如《守秘人规则书》）拖到这里，系统在本地建立可检索索引；
                  跑团时守秘人遇到拿不准的精确规则，会按需查阅原文再裁定。仅支持含文字层的
                  PDF，扫描件需先 OCR。
                </span>
              </div>
            ) : (
              // 不再挤在 21rem 侧栏里，书卡可以并排了。三列封顶：书名本就长，
              // 再窄下去标题就得截断，反而不如两列读得清。
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {books.map((b, i) => (
                  <div
                    key={b.id}
                    style={staggerStyle(i)}
                    className="entity-card list-enter flex flex-col overflow-hidden rounded"
                  >
                    <div
                      className="flex items-start gap-2.5 px-3 pt-3 pb-2.5"
                      style={{ borderBottom: '1px solid var(--color-border)' }}
                    >
                      <span className="char-sigil" aria-hidden="true"><GiBookCover /></span>
                      <div className="min-w-0 flex-1">
                        <h4 className="card-title !mb-0.5 truncate !text-[length:var(--text-base)]" title={b.title}>
                          {b.title}
                        </h4>
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
          </div>

          {/* 测试检索是调试工具，不是日常操作——默认收起，别占着首屏 */}
          {hasReady && (
            <div className="card">
              <button
                onClick={() => setSearchOpen((v) => !v)}
                aria-expanded={searchOpen}
                className="card-title !mb-0 flex w-full items-center gap-2"
              >
                <GiMagnifyingGlass /> 测试检索
                <span className="ml-auto" style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}>
                  {searchOpen ? '收起' : '展开'}
                </span>
              </button>
              {searchOpen && (
                <div className="mt-3">
                  <div className="flex gap-2">
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
          )}
        </div>
      </div>
    </div>
  )
}
