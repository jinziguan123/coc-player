import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { AnimatePresence, m } from 'motion/react'
import { GiSandsOfTime, GiScrollUnfurled } from 'react-icons/gi'
import { CircleAlert, CircleCheck, Loader2, RefreshCw, X } from 'lucide-react'
import { spring } from '@/lib/motion'
import { runningJobCount, useUploadJobsStore, type UploadJob } from '@/stores/uploadJobsStore'

function timeLabel(sec: number) {
  const d = new Date(sec * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function JobItem({ job, onDismiss, onNavigate }: { job: UploadJob; onDismiss: () => void; onNavigate: () => void }) {
  const title = job.result?.title || job.files.join('、') || '未命名文件'
  return (
    <li className="upload-job">
      <div className="upload-job-head">
        <span className={`upload-job-icon is-${job.status}`} aria-hidden>
          {job.status === 'running' ? <Loader2 size={14} className="animate-spin" />
            : job.status === 'done' ? <CircleCheck size={14} /> : <CircleAlert size={14} />}
        </span>
        <span className="upload-job-title" title={job.files.join('\n')}>{title}</span>
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
            <span style={{ fontFamily: 'var(--font-mono)' }}>{job.percent}%</span>
          </div>
          <div className="upload-progress-track">
            <div className="upload-progress-fill" style={{ width: `${Math.max(job.percent, 4)}%` }} />
          </div>
        </>
      )}
      {job.status === 'failed' && <p className="upload-job-error">{job.detail || '解析失败'}</p>}
      {job.status === 'done' && job.result?.id && (
        <Link to={`/modules/${job.result.id}`} className="upload-job-link" onClick={onNavigate}>
          <GiScrollUnfurled aria-hidden /> 查看模组
        </Link>
      )}
    </li>
  )
}

/** 「解析进度」抽屉：右侧滑入，列出最近的模组上传解析任务（与运行轨迹抽屉同一套外观）。 */
export function UploadJobsDrawer() {
  const { jobs, open, setOpen, refresh, dismiss, clearFinished } = useUploadJobsStore()
  const finished = jobs.length - runningJobCount(jobs)

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
              <div className="trace-sub">导入的剧本在后台解析，刷新或离开页面不影响；重启程序后列表清空。</div>
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
              <p className="upload-jobs-empty">暂无解析任务。到「模组」页导入剧本后，进度会显示在这里。</p>
            ) : (
              <ul className="upload-jobs-list">
                {jobs.map((job) => (
                  <JobItem key={job.id} job={job} onDismiss={() => void dismiss(job.id)} onNavigate={() => setOpen(false)} />
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
