// 规则页「规则书」标签：规则书 PDF 书库与检索试查。
//
// 职责边界：这里的原文只供 AI 守秘人叙事时查阅（被动注入最相关的几段、或它主动调用
// rule_lookup），**不参与任何结算**——判定由「本桌规则」标签里的规则组件决定。
import { useEffect, useRef, useState, useCallback } from 'react'
import { toast } from 'sonner'
import { api, getApiBase, getPlayerToken, localApi } from '@/api/client'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { GiBookCover, GiMagnifyingGlass } from 'react-icons/gi'
import { Collapse } from '@/components/ui/Collapse'
import { RulebookTome, type Rulebook } from '@/components/rulebook/RulebookTome'

interface RuleHit {
  text: string
  page: number
  score: number
  rulebook_id: string
}

export function RulebookLibrary({ onCount }: {
  /** 已装载的册数变了就告诉页面（页头统计用） */
  onCount?: (n: number) => void
}) {
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
    } catch (e) {
      toast.error(`删除失败：${e instanceof Error ? e.message : '未知原因'}`)
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

  useEffect(() => { onCount?.(books.length) }, [books.length, onCount])

  return (
    <div className="max-w-[100rem]">
      <div className="space-y-6">
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
              // 书架：一册册立着排开，书名竖排不必截断
              <div className="tome-shelf">
                {books.map((b, i) => (
                  <RulebookTome key={b.id} book={b} index={i} onDelete={deleteBook} />
                ))}
              </div>
            )}
          </div>

          {/* 检索试查：看看 AI 守秘人遇到这个词会读到哪几段原文。低频调试，默认收起 */}
          {hasReady && (
            <div className="card">
              <button
                onClick={() => setSearchOpen((v) => !v)}
                aria-expanded={searchOpen}
                className="card-title !mb-0 flex w-full items-center gap-2"
              >
                <GiMagnifyingGlass /> 试试 AI 会查到什么
                <span className="ml-auto" style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}>
                  {searchOpen ? '收起' : '展开'}
                </span>
              </button>
              <Collapse open={searchOpen}>
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
              </Collapse>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
