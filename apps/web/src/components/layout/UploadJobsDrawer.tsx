import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { AnimatePresence, m } from 'motion/react'
import type { IconType } from 'react-icons'
import {
  GiArchiveResearch, GiBookCover, GiCloudDownload, GiCycle, GiPaintBrush, GiSandsOfTime, GiScrollUnfurled,
} from 'react-icons/gi'
import { Ban, CircleAlert, CircleCheck, Loader2, RefreshCw, X } from 'lucide-react'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import { spring } from '@/lib/motion'
import { runningJobCount, useUploadJobsStore, type UploadJob } from '@/stores/uploadJobsStore'

/** 各类后台任务的标识图标（与对应页面的图标一致） */
const KIND_ICON: Record<string, IconType> = {
  module: GiScrollUnfurled,
  module_index: GiArchiveResearch,
  rulebook: GiBookCover,
  embed_download: GiCloudDownload,
  embed_rebuild: GiCycle,
  module_image: GiPaintBrush,
}

function timeLabel(sec: number) {
  const d = new Date(sec * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function JobItem({ job, onDismiss, onNavigate, onGenerateImages, onSkipImages, hideImagePrompt }: {
  job: UploadJob
  onDismiss: () => void
  onNavigate: () => void
  onGenerateImages: () => void
  onSkipImages: () => void
  hideImagePrompt: boolean
}) {
  const KindIcon = KIND_ICON[job.kind] ?? GiSandsOfTime
  return (
    <li className="upload-job">
      <div className="upload-job-head">
        <span className={`upload-job-icon is-${job.status}`} aria-hidden>
          {job.status === 'running' ? <Loader2 size={14} className="animate-spin" />
            : job.status === 'done' ? <CircleCheck size={14} />
              : job.status === 'cancelled' ? <Ban size={14} /> : <CircleAlert size={14} />}
        </span>
        <KindIcon className="upload-job-kind" aria-hidden />
        <span className="upload-job-title" title={job.title}>{job.title}</span>
        <span className="upload-job-time">{timeLabel(job.created_at)}</span>
        {job.status !== 'running' && (
          <button type="button" className="upload-job-dismiss" onClick={onDismiss} title="从列表移除" aria-label="从列表移除">
            <X size={13} />
          </button>
        )}
      </div>
      {job.status === 'running' && (
        <>
          <div className="upload-job-stage">
            <span>{job.stage}</span>
            {job.percent !== null && <span style={{ fontFamily: 'var(--font-mono)' }}>{job.percent}%</span>}
          </div>
          <div
            className="upload-progress-track"
            role="progressbar"
            aria-label={`${job.title} 进度`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={job.percent ?? undefined}
          >
            {/* 没有总量的步骤（解析 PDF、等待模型）给一段流动微光，示意仍在工作 */}
            <div className="upload-progress-fill" style={{ width: `${job.percent === null ? 30 : Math.max(job.percent, 4)}%` }} />
          </div>
        </>
      )}
      {job.status === 'failed' && <p className="upload-job-error">{job.detail || '失败'}</p>}
      {job.status !== 'running' && job.status !== 'failed' && job.stage && (
        <p className="upload-job-note">{job.stage}</p>
      )}
      {job.status === 'done' && job.kind === 'module' && job.link && !hideImagePrompt && (
        <div className="flex items-center gap-3 mt-2">
          <button type="button" className="btn-secondary btn-sm" onClick={onGenerateImages}>
            <GiPaintBrush size={13} aria-hidden /> 生成配图
          </button>
          <button
            type="button"
            className="text-xs underline"
            style={{ color: 'var(--color-text-secondary)' }}
            onClick={onSkipImages}
          >
            暂不生成
          </button>
        </div>
      )}
      {job.status === 'done' && job.link && (
        <Link to={job.link.to} className="upload-job-link" onClick={onNavigate}>
          <KindIcon aria-hidden /> {job.link.label}
        </Link>
      )}
    </li>
  )
}

/** 「解析进度」抽屉：右侧滑入，列出最近的后台任务（模组解析、规则书导入、检索模型下载与索引重建……）。 */
export function UploadJobsDrawer() {
  const { jobs, open, setOpen, refresh, dismiss, clearFinished } = useUploadJobsStore()
  const finished = jobs.length - runningJobCount(jobs)
  // 解析完成后的「要不要配图」只在这一页生命周期内问一次；点了任一个就不再打扰
  const [hiddenImagePrompts, setHiddenImagePrompts] = useState<Set<string>>(new Set())
  const [startingImageJobs, setStartingImageJobs] = useState<Set<string>>(new Set())

  const generateImages = async (job: UploadJob) => {
    const moduleId = job.link?.to.split('/').filter(Boolean).pop()
    if (!moduleId) return
    setStartingImageJobs((s) => new Set(s).add(job.id))
    try {
      const res = await localApi.post<{ task_id: string }>(`/modules/${moduleId}/images/generate`, {})
      toast.success('已开始生成模组配图')
      if (res?.task_id) await useUploadJobsStore.getState().track(res.task_id)
      setHiddenImagePrompts((s) => new Set(s).add(job.id))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '生成配图失败')
    } finally {
      setStartingImageJobs((s) => {
        const next = new Set(s)
        next.delete(job.id)
        return next
      })
    }
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  return createPortal(
    <AnimatePresence>
      {open && (
        <m.aside
          key="upload-jobs"
          role="dialog"
          aria-label="解析进度"
          className="trace-drawer"
          initial={{ x: '104%', opacity: 0.6 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: '104%', opacity: 0.6, transition: { duration: 0.18 } }}
          transition={spring.soft}
        >
          <header className="trace-head">
            <div className="min-w-0">
              <div className="trace-title"><GiSandsOfTime aria-hidden /> 解析进度</div>
              <div className="trace-sub">剧本解析、规则书导入、检索模型下载与索引重建都在后台进行，刷新或离开页面不影响；重启程序后列表清空。</div>
            </div>
            <button type="button" className="trace-icon-btn" onClick={() => void refresh()} title="刷新" aria-label="刷新">
              <RefreshCw size={14} />
            </button>
            <button type="button" className="trace-icon-btn" onClick={() => setOpen(false)} title="关闭（Esc）" aria-label="关闭">
              <X size={15} />
            </button>
          </header>
          <div className="upload-jobs-body">
            {jobs.length === 0 ? (
              <p className="upload-jobs-empty">暂无后台任务。导入剧本或规则书、下载或切换检索模型后，进度会显示在这里。</p>
            ) : (
              <ul className="upload-jobs-list">
                {jobs.map((job) => (
                  <JobItem
                    key={job.id}
                    job={job}
                    onDismiss={() => void dismiss(job.id)}
                    onNavigate={() => setOpen(false)}
                    onGenerateImages={() => void generateImages(job)}
                    onSkipImages={() => setHiddenImagePrompts((s) => new Set(s).add(job.id))}
                    hideImagePrompt={hiddenImagePrompts.has(job.id) || startingImageJobs.has(job.id)}
                  />
                ))}
              </ul>
            )}
          </div>
          {finished > 0 && (
            <footer className="upload-jobs-foot">
              <button type="button" className="btn-secondary btn-sm" onClick={() => void clearFinished()}>
                清除已结束（{finished}）
              </button>
            </footer>
          )}
        </m.aside>
      )}
    </AnimatePresence>,
    document.body,
  )
}
