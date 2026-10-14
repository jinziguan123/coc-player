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
        <span className={`tome-book-seal tome-book-seal--${b.status}`}>{STATUS_LABEL[b.status] || b.status}</span>
        <p className="tome-book-foot">
          {b.page_count} 页 · {b.chunk_count} 片段
        </p>
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
            <button onClick={open} className="chip chip--danger chip-btn chip-btn--danger">删除</button>
          )}
        </ConfirmDialog>
      </div>
    </TiltCard>
  )
}
