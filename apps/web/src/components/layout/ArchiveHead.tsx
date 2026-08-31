import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { GiReturnArrow } from 'react-icons/gi'

/**
 * 页面抬头：标题 ＋ 一行等宽统计 ＋ 右侧操作。
 *
 * 曾经这里还有一枚「名录 / 书目 / 书架」的档案书签，现在拿掉了。两个原因，后一个才是根本：
 *
 * 1. 各页容器宽度与上边距本就不同（表单窄、列表宽），书签挂在页面容器里，跳一次页就挪一次
 *    位置，看着像在飘。
 * 2. 它根本没编码任何新信息——侧边栏已经高亮「角色」、标题也写着「角色」，书签再说一遍
 *    「名录」，是第三个说同一件事的词，页面里还真有个叫「名录」的小标题跟它撞脸。
 *    结构性装置要么承载真实信息，要么就该删掉。
 *
 * 留下的统计是真信息：你手上有几位调查员、几本书、几桌在跑。它跟首页卷宗抬头那行同源
 * （共用 .case-stats），也仍然是等宽 + tabular-nums。
 */
export interface HeadStat {
  label: string
  /** 数字走等宽与 tabular-nums；给字符串时原样显示（如「未解析」） */
  value: number | string
}

export function ArchiveHead({ title, stats, actions, back = false, onBack }: {
  title: string
  stats?: HeadStat[]
  actions?: ReactNode
  /**
   * 显示返回按钮。**默认不显示**——侧边栏能直达的一级页面（角色/模组/规则书/游戏）
   * 用不上它，摆在那儿只是多一个不会被按的按钮。
   */
  back?: boolean
  /** 覆盖返回行为。多步流程里「上一步」不等于浏览器的上一页（见 GamePage）。 */
  onBack?: () => void
}) {
  const navigate = useNavigate()

  return (
    <div className="archive-head">
      {back && (
        <button
          onClick={() => (onBack ? onBack() : navigate(-1))}
          className="btn-secondary btn-sm flex items-center gap-1 archive-back"
        >
          <GiReturnArrow /> 返回
        </button>
      )}
      <h2 className="archive-title">{title}</h2>

      {stats && stats.length > 0 && (
        <div className="case-stats archive-stats">
          {stats.map(({ label, value }) => (
            <span key={label} className="case-stat">
              <span className="case-stat-label">{label}</span>
              <span className="case-stat-num">{value}</span>
            </span>
          ))}
        </div>
      )}

      {actions && <div className="page-head-actions">{actions}</div>}
    </div>
  )
}
