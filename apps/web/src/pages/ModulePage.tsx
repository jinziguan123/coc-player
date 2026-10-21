import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api, localApi } from '../api/client'
import { useModuleStore } from '../stores/moduleStore'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { GiUpCard, GiScrollUnfurled, GiQuillInk } from 'react-icons/gi'
import { AnimatePresence, m } from 'motion/react'
import { ModuleCard } from '@/components/module/ModuleCard'
import { spring } from '@/lib/motion'
import { ArchiveHead } from '@/components/layout/ArchiveHead'
import { Loader2, X } from 'lucide-react'

const ALLOWED_EXTS = ['txt', 'md', 'pdf', 'docx', 'doc', 'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']

export function ModulePage() {
  const { modules, loading, fetchModules, startUpload } = useModuleStore()
  const fileRef = useRef<HTMLInputElement>(null)
  const [ruleSystem, setRuleSystem] = useState('coc')
  const [uploading, setUploading] = useState(false)
  const [uploadJob, setUploadJob] = useState<{ stage: string; percent: number } | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  // 导入抽屉：上传是偶尔才做的事，不该常年占着首屏最好的位置。书架空着时自动摊开。
  const [uploadToggled, setUploadToggled] = useState<boolean | null>(null)
  const uploadOpen = uploadToggled ?? (!loading && modules.length === 0)
  const setUploadOpen = (fn: (v: boolean) => boolean) => setUploadToggled(fn(uploadOpen))

  useEffect(() => {
    fetchModules()
  }, [fetchModules])

  // 有模组在建原文索引时轮询刷新，直到 indexing → ready/failed
  useEffect(() => {
    if (!modules.some((m) => m.rag_status === 'indexing')) return
    const t = setTimeout(() => fetchModules(), 3000)
    return () => clearTimeout(t)
  }, [modules, fetchModules])

  const rebuildRag = async (id: string) => {
    try {
      await localApi.post(`/modules/${id}/rag/rebuild`)
      toast.success('已开始重建原文索引')
      fetchModules()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '重建索引失败')
    }
  }

  const addFiles = (fileList: FileList | File[]) => {
    const valid: File[] = []
    for (const file of Array.from(fileList)) {
      const ext = file.name.split('.').pop()?.toLowerCase()
      if (!ALLOWED_EXTS.includes(ext || '')) {
        toast.error(`「${file.name}」格式不支持，仅支持 ${ALLOWED_EXTS.map(e => '.' + e).join('、')}`)
        continue
      }
      valid.push(file)
    }
    if (valid.length) setSelectedFiles((prev) => [...prev, ...valid])
  }

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index))
  }

  const handleUpload = async () => {
    if (!selectedFiles.length) return
    setUploading(true)
    try {
      const jobId = await startUpload(selectedFiles, ruleSystem)
      setSelectedFiles([])
      if (fileRef.current) fileRef.current.value = ''
      setUploadJob({ stage: '排队中', percent: 0 })
      // 轮询后台解析任务进度，直到 done / failed
      type JobStatus = {
        status: 'running' | 'done' | 'failed'
        stage: string
        percent: number
        detail: string
        result: { title?: string } | null
      }
      for (;;) {
        const s = await api.get<JobStatus>(`/modules/upload/status/${jobId}`)
        if (s.status === 'running') {
          setUploadJob({ stage: s.stage, percent: s.percent })
          await new Promise((r) => setTimeout(r, 1200))
          continue
        }
        if (s.status === 'done') {
          toast.success(`模组「${s.result?.title ?? ''}」解析完成`)
          await fetchModules()
        } else {
          toast.error(s.detail || '模组解析失败')
        }
        break
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '模组上传失败')
    } finally {
      setUploadJob(null)
      setUploading(false)
    }
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files)
  }, [])

  const navigate = useNavigate()

  const deleteModule = async (id: string) => {
    try {
      await localApi.delete(`/modules/${id}`)
      fetchModules()
      toast.success('模组已删除')
    } catch (e) {
      toast.error(`删除失败：${e instanceof Error ? e.message : '未知原因'}`)
    }
  }

  const totalSize = selectedFiles.reduce((s, f) => s + f.size, 0)

  return (
    // 上传区是线性表单（窄栏更好用），列表是并列卡片（放宽让网格铺开）——分别限宽
    <div className="max-w-[100rem]">
      {/* 标题按人怎么叫它取（模组），不叫「模组管理」——管理是系统视角 */}
      <ArchiveHead
        title="模组"
        stats={[{ label: '在库', value: modules.length }]}
        actions={(
          <>
            <button
              onClick={() => setUploadOpen((v) => !v)}
              aria-expanded={uploadOpen}
              className="btn-secondary btn-sm flex items-center gap-1"
            >
              <GiUpCard /> {uploadOpen ? '收起导入' : '导入剧本'}
            </button>
            <button onClick={() => navigate('/modules/new')} className="btn-primary btn-sm flex items-center gap-1">
              <GiQuillInk /> 新建模组
            </button>
          </>
        )}
      />

      <AnimatePresence initial={false}>
      {uploadOpen && (
      <m.div
        key="upload"
        className="upload-drawer"
        initial={{ height: 0, opacity: 0 }}
        animate={{ height: 'auto', opacity: 1 }}
        exit={{ height: 0, opacity: 0 }}
        transition={{ height: spring.soft, opacity: { duration: 0.2 } }}
      >
      <div className="card mb-8 max-w-3xl">
        <h3 className="card-title flex items-center gap-2">
          <GiUpCard /> 导入剧本
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
            accept=".txt,.md,.pdf,.docx,.doc,.png,.jpg,.jpeg,.webp,.gif,.bmp,image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files)
              e.target.value = ''
            }}
          />
          {selectedFiles.length > 0 ? (
            <div>
              <p className="text-sm font-semibold" style={{ color: 'var(--color-text-accent)' }}>
                已选择 {selectedFiles.length} 个文件
              </p>
              <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                共 {(totalSize / 1024).toFixed(1)} KB · 点击继续添加
              </p>
            </div>
          ) : (
            <div>
              <GiUpCard className="dropzone-icon" aria-hidden="true" />
              <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                拖拽文件到此处，或点击选择（可多选）
              </p>
              <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)', opacity: 0.7 }}>
                支持 .txt、.md、.pdf、.docx、.doc、图片(png/jpg…) · 多个文件视为同一模组（图片走视觉模型识别）
              </p>
            </div>
          )}
        </div>

        {selectedFiles.length > 0 && (
          <div className="mb-3 space-y-1">
            {selectedFiles.map((f, i) => (
              <div
                key={`${f.name}-${i}`}
                className="flex items-center justify-between px-2 py-1 rounded text-sm"
                style={{ background: 'var(--color-bg-tertiary)' }}
              >
                <span className="truncate flex-1 mr-2">{f.name}</span>
                <span className="text-xs mr-2 flex-shrink-0 font-mono" style={{ color: 'var(--color-text-secondary)' }}>
                  {(f.size / 1024).toFixed(1)} KB
                </span>
                {/* 图标守则：删除走 lucide 矢量图标，不用 × 字符 */}
                <button
                  onClick={(e) => { e.stopPropagation(); removeFile(i) }}
                  className="icon-btn icon-btn--danger !w-6 !h-6"
                  title={`移除 ${f.name}`}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex gap-3 items-center">
          <Select value={ruleSystem} onValueChange={setRuleSystem}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="coc">CoC</SelectItem>
              <SelectItem value="dnd">DnD</SelectItem>
            </SelectContent>
          </Select>
          <button onClick={handleUpload} disabled={uploading || !selectedFiles.length} className="btn-primary">
            {uploading ? '解析中...' : '上传并解析'}
          </button>
        </div>
      </div>
      </m.div>
      )}
      </AnimatePresence>

      {uploadJob && (
        <div className="card" style={{ padding: '0.75rem 1rem', marginBottom: '1rem' }}>
          <div
            style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              fontSize: '0.82rem', color: 'var(--color-text-secondary)', marginBottom: '0.4rem',
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
              <Loader2 size={13} className="animate-spin" />
              {uploadJob.stage}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)' }}>{uploadJob.percent}%</span>
          </div>
          <div className="upload-progress-track">
            <div
              className="upload-progress-fill"
              style={{ width: `${Math.max(uploadJob.percent, 4)}%` }}
            />
          </div>
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--color-text-secondary)' }}>加载中...</p>
      ) : modules.length === 0 ? (
        <div className="empty-state">
          <span className="empty-state-icon"><GiScrollUnfurled /></span>
          <span className="empty-state-title">书架上还空着</span>
          <span className="empty-state-hint">
            上传一份剧本（txt / md / pdf / 图片），AI 会把它解析成场景、NPC 与线索；
            也可以点右上角「新建模组」从零开始编排。
          </span>
        </div>
      ) : (
        <div className="module-grid">
          {modules.map((m, i) => (
            <ModuleCard key={m.id} module={m} index={i} onRebuildRag={rebuildRag} onDelete={deleteModule} />
          ))}
        </div>
      )}
    </div>
  )
}
