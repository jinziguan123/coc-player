// 投骰请求卡：系统挂出「请 X 进行一次「技能」检定」时给玩家的那张卡。
//
// 与 CheckResultCard 是一对——那张讲「掷出了什么」，这张讲「为什么要掷」。
// 检定缘由（metadata.reason）由 KP 给出：投骰之前玩家能看到的说明只有这张卡，
// 光一句「请你投个骰」谁也不知道自己在赌什么。奖惩骰的标注在 content 里（后端拼好）。
import { GiRollingDices } from 'react-icons/gi'

export function CheckRequestCard({
  content, reason, actionable, pending, onRoll, disabled, animClass,
}: {
  /** 后端拼好的提示语，如「请 陈守一 进行一次「理智」检定（惩罚骰 ×1：光线昏暗）」 */
  content: string
  /** KP 给的检定缘由；缺省时不占位 */
  reason?: string
  /** 这一掷归我投（是我的角色）→ 才给按钮 */
  actionable: boolean
  /** 还没投 → 显示按钮；已投 → 显示「已投骰」 */
  pending: boolean
  onRoll: () => void
  disabled?: boolean
  animClass?: string
}) {
  return (
    <div data-tour="check-request" className={`chat-msg py-1 flex justify-center ${animClass || ''}`}>
      <div
        className={`rounded-md px-3 py-2 text-sm ${pending && actionable ? 'dice-pending' : ''}`}
        style={{
          background: 'var(--color-bg-tertiary)',
          borderLeft: '3px solid var(--color-accent)',
          maxWidth: '100%',
        }}
      >
        <div className="flex items-center gap-3">
          <GiRollingDices style={{ color: 'var(--color-accent)', fontSize: '1.1rem', flexShrink: 0 }} />
          <span className="whitespace-pre-wrap">{content}</span>
          {pending && actionable && (
            <button
              onClick={onRoll}
              disabled={disabled}
              className="btn-primary text-xs !px-2.5 !py-1 flex items-center gap-1 flex-shrink-0"
              style={disabled ? { opacity: 0.5 } : undefined}
            >
              <GiRollingDices size={13} /> 投骰
            </button>
          )}
          {!pending && (
            <span
              className="text-xs flex-shrink-0"
              style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}
            >
              已投骰
            </span>
          )}
        </div>
        {reason && (
          <div
            className="text-xs mt-1.5 pl-[1.85rem] whitespace-pre-wrap"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            因：{reason}
          </div>
        )}
      </div>
    </div>
  )
}
