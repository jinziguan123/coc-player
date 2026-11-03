import { GiTrashCan } from 'react-icons/gi'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { TiltCard } from '@/components/ui/TiltCard'

export interface Rulebook {
  id: string
  title: string
  rule_system: string
  page_count: number
  chunk_count: number
  status: string
  embed_model: string
  error: string
  /** 索引由旧版切块逻辑建成：有原件会在启动时后台重建，没有原件需重新上传 */
  index_outdated?: boolean
  has_source?: boolean
  /** 在导入队列里时的进度 */
  progress?: { stage: string; done: number; total: number; queue_position: number } | null
}

/** 导入进度的一行说明：排队 / 解析 / 建索引 x/y / 写入 */
function progressLabel(p: NonNullable<Rulebook['progress']>): string {
  switch (p.stage) {
    case 'queued': return p.queue_position > 0 ? `排队中（第 ${p.queue_position} 位）` : '排队中'
    case 'parsing': return '解析 PDF…'
    case 'embedding': return `建索引 ${p.done}/${p.total}`
    case 'saving': return '写入中…'
    default: return p.stage
  }
}

const STATUS_LABEL: Record<string, string> = {
  indexing: '索引中',
  ready: '可检索',
  failed: '失败',
}

/**
 * 规则书：一册立在书架上的精装书。
 *
 * 瓶绿布面、黄铜双线框、四角饰，书名竖排（中文书名本就适合竖写，长书名也不必截断）。
 * 能不能检索是这本书唯一要紧的状态，盖成一枚小章；页数、片段数退到书脚。
 * 悬停时书微微转向光源（TiltCard），封面边缘透出一道烫金的反光。
 */
export function RulebookTome({ book: b, index, onDelete }: { book: Rulebook; index: number; onDelete: (id: string) => void }) {
  return (
    <TiltCard index={index} max={9} className={`tome-book tome-book--${b.status}`}>
      <div className="tome-book-cover">
        <span className="tome-book-frame" aria-hidden />
        <span className="tome-book-system">{b.rule_system.toUpperCase()}</span>
        <h4 className="tome-book-title" title={b.title}>{b.title}</h4>
        {b.index_outdated ? (
          <span
            className="tome-book-seal tome-book-seal--outdated"
            title={b.has_source
              ? '索引由旧版切块逻辑建成，正在后台按原件重建，完成前照常可检索'
              : '索引由旧版切块逻辑建成（条文可能被切断）：删除后重新上传这本 PDF，检索更准确'}
          >
            {b.has_source ? '重建中' : '旧版索引'}
          </span>
        ) : (
          <span className={`tome-book-seal tome-book-seal--${b.status}`}>{STATUS_LABEL[b.status] || b.status}</span>
        )}
        {b.status === 'indexing' && b.progress ? (
          <div className="tome-book-progress" aria-live="polite">
            <span>{progressLabel(b.progress)}</span>
            <div
              className="upload-progress-track"
              role="progressbar"
              aria-label={`《${b.title}》导入进度`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={b.progress.total ? Math.round((b.progress.done / b.progress.total) * 100) : 0}
            >
              <div
                className="upload-progress-fill"
                style={{ width: `${b.progress.total ? (b.progress.done / b.progress.total) * 100 : b.progress.stage === 'parsing' ? 5 : 0}%` }}
              />
            </div>
          </div>
        ) : (
          <p className="tome-book-foot">
            {b.page_count} 页 · {b.chunk_count} 片段
          </p>
        )}
      </div>
      {b.status === 'failed' && b.error && <p className="tome-book-error" title={b.error}>错误：{b.error}</p>}
      <div className="tome-book-actions entity-card-actions">
        <ConfirmDialog
          title="删除规则书"
          description={`确定要删除「${b.title}」及其索引吗？此操作不可恢复。`}
          confirmLabel="删除"
          onConfirm={() => onDelete(b.id)}
        >
          {(open) => (
            <button onClick={open} className="tome-book-delete" aria-label={`删除规则书「${b.title}」`}>
              <GiTrashCan aria-hidden /> 删除
            </button>
          )}
        </ConfirmDialog>
      </div>
    </TiltCard>
  )
}
