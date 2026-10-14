import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { m } from 'motion/react'
import { GiReturnArrow } from 'react-icons/gi'
import { CountUp } from '@/components/ui/CountUp'
import { ease, spring } from '@/lib/motion'

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
 * 留下的统计是真信息：你手上有几位调查员、几本书、几桌在跑——与首页的库存铭牌同一套
 * 字形（仪表数字 + 宽字距小标签），首屏从 0 滚上来。
 *
 * 动效：标题逐字落下、三线底边从中间拉开、数字滚动——每次进页都像翻开一份新档案。
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
    <header className="archive-head">
      <div className="archive-head-row">
        {back && (
          <button
            onClick={() => (onBack ? onBack() : navigate(-1))}
            className="btn-secondary btn-sm flex items-center gap-1 archive-back"
          >
            <GiReturnArrow /> 返回
          </button>
        )}
        {/* 标题逐字落下：铭牌上的字一个个被「铸」上去。读屏读整串，不读拆开的单字 */}
        <h2 className="archive-title" aria-label={title}>
          {[...title].map((ch, i) => (
            <m.span
              key={`${ch}-${i}`}
              aria-hidden
              className="archive-title-ch"
              initial={{ opacity: 0, y: -14, rotateX: 70 }}
              animate={{ opacity: 1, y: 0, rotateX: 0 }}
              transition={{ ...spring.pop, delay: 0.04 + i * 0.06 }}
            >
              {ch}
            </m.span>
          ))}
        </h2>

        {stats && stats.length > 0 && (
          <div className="archive-stats">
            {stats.map(({ label, value }) => (
              <span key={label} className="archive-stat">
                {typeof value === 'number'
                  ? <CountUp value={value} className="archive-stat-num" />
                  : <span className="archive-stat-num archive-stat-num--text">{value}</span>}
                <span className="archive-stat-label">{label}</span>
              </span>
            ))}
          </div>
        )}

        {actions && <div className="page-head-actions">{actions}</div>}
      </div>
      {/* 三线铭牌底边：从正中向两端拉开 */}
      <m.div
        className="archive-rule"
        aria-hidden
        initial={{ scaleX: 0, opacity: 0 }}
        animate={{ scaleX: 1, opacity: 1 }}
        transition={{ duration: 0.7, delay: 0.12, ease: ease.out }}
      >
        <span className="archive-rule-gem" />
      </m.div>
    </header>
  )
}
