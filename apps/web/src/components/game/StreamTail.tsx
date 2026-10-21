import { RotateCcw } from 'lucide-react'

/**
 * 生成中的那一行：跳动的点、思考提示、满 5 秒才出现的秒表，以及卡住时的「打断并重新生成」。
 * 聊天流挂在消息末尾，对话框模式放在对话框底栏。
 */
export function StreamTail({ note, waitSec, onInterrupt }: {
  /** 思考提示 / 尾注；空则只有跳动的点 */
  note: string
  waitSec: number
  /** 给了才出现打断按钮 */
  onInterrupt?: () => void
}) {
  return (
    <div className="chat-loading flex items-center gap-2">
      <span className="dot-pulse" />
      {note && (
        <span className="text-xs italic" style={{ color: 'var(--color-text-secondary)' }}>
          {note}
        </span>
      )}
      {/* 满 5 秒才出现：短回合不必看见秒表，长回合才需要「它还在跑」的凭据 */}
      {waitSec >= 5 && (
        <span className="text-xs tabular-nums" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>
          {waitSec}s
        </span>
      )}
      {onInterrupt && (
        <button
          onClick={onInterrupt}
          title="打断当前生成并用本轮既有输入重新生成（生成卡住时用）"
          className="ml-2 inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded transition-colors hover:opacity-80"
          style={{ color: 'var(--color-danger)' }}
        >
          <RotateCcw size={11} /> 打断并重新生成
        </button>
      )}
    </div>
  )
}
