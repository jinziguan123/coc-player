import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { GiReturnArrow } from 'react-icons/gi'

/**
 * 档案抬头：把首页那套卷宗语言推到各内页。
 *
 * 首页（卷宗）的抬头是「左对齐的档案标签 ＋ 一行等宽库存」。内页此前各自是
 * 「返回 ＋ 一个居中偏左的大标题 ＋ 右侧按钮」，与首页不像同一个产品；页面里真正
 * 有用的计数（名录里的「7 人」）还藏在某个小标题旁边。
 *
 * 这里复用首页的 `.case-tab` / `.case-stats` 类，不另起一套——CSS 同源，视觉才必然一致。
 *
 * 标题按人怎么称呼它来取，不按系统怎么实现它：是「角色」不是「角色管理」。
 */
export interface HeadStat {
  label: string
  /** 数字走等宽与 tabular-nums；给字符串时原样显示（如「未解析」） */
  value: number | string
}

export function ArchiveHead({ tab, title, stats, actions, back = true, onBack }: {
  /** 档案标签：这一页在卷宗里叫什么（名录 / 书目 / 卷宗） */
  tab: string
  title: string
  stats?: HeadStat[]
  actions?: ReactNode
  /** 关掉返回按钮（首屏级页面用不上） */
  back?: boolean
  /** 覆盖返回行为。多步流程里「上一步」不等于浏览器的上一页（见 GamePage）。 */
  onBack?: () => void
}) {
  const navigate = useNavigate()

  return (
    <div className="archive-head">
      <div className="case-head-top">
        <span className="case-tab">{tab}</span>
        {stats && stats.length > 0 && (
          <div className="case-stats">
            {stats.map(({ label, value }) => (
              <span key={label} className="case-stat">
                <span className="case-stat-label">{label}</span>
                <span className="case-stat-num">{value}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="archive-head-row">
        {back && (
          <button
            onClick={() => (onBack ? onBack() : navigate(-1))}
            className="btn-secondary btn-sm flex items-center gap-1"
          >
            <GiReturnArrow /> 返回
          </button>
        )}
        <h2 className="archive-title">{title}</h2>
        {actions && <div className="page-head-actions">{actions}</div>}
      </div>
    </div>
  )
}
