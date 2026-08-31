import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'

/**
 * 卷宗抬头。
 *
 * 这一页在侧边栏里就叫「卷宗」（图标都是 GiArchiveResearch），可它长得一直是「居中大标题
 * ＋三张并排圆角卡」——任何一个 SaaS 落地页都长这样，和 1920 年代的档案没有半点关系。
 * 产品自己的语汇没在视觉上兑现，是这一页最没个性的地方。
 *
 * 所以把顶部改成一份卷宗的抬头：**左对齐**（档案是左对齐的，居中是海报的语言），
 * 右侧挂一行等宽的库存清单。
 *
 * 那行清单不是仿真的档案编号——假编号只是装饰，编码不了任何真实信息。它列的是你手上
 * **真有什么**，正好回答这一页要回答的问题：我现在能不能开一局。手上是空的时候，
 * 它就直接变成下一步该点哪儿。
 */
interface Row {
  label: string
  count: number
  /** 一个都没有时，这条就从「计数」变成一句动作——空状态是邀请，不是状态播报 */
  emptyText: string
  to: string
}

export function CaseFileHeader() {
  const [rows, setRows] = useState<Row[] | null>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [chars, modules, sessions] = await Promise.all([
          api.get<unknown[]>('/characters'),
          api.get<unknown[]>('/modules'),
          api.get<{ status: string }[]>('/sessions'),
        ])
        const open = sessions.filter(
          (s) => s.status === 'active' || s.status === 'paused' || s.status === 'setup',
        ).length
        if (!alive) return
        setRows([
          { label: '调查员', count: chars.length, emptyText: '建一位调查员', to: '/characters' },
          { label: '模组', count: modules.length, emptyText: '导入模组', to: '/modules' },
          { label: '在跑', count: open, emptyText: '开一局', to: '/game' },
        ])
      } catch {
        // 取不到就不显示这一行——首页的其余部分照常可用，不为一行统计挡住整页
        if (alive) setRows(null)
      }
    })()
    return () => { alive = false }
  }, [])

  return (
    <header className="case-head">
      <div className="case-head-top">
        <span className="case-tab">卷宗</span>
        {rows && (
          <div className="case-stats">
            {rows.map(({ label, count, emptyText, to }) => (
              <Link
                key={label}
                to={to}
                className="case-stat no-underline"
                // 读屏里「调查员 7」听着像半句话，补一句完整的
                aria-label={count > 0 ? `${label} ${count}，前往` : emptyText}
              >
                {count > 0 ? (
                  <>
                    <span className="case-stat-label">{label}</span>
                    <span className="case-stat-num">{count}</span>
                  </>
                ) : (
                  <span className="case-stat-empty">{emptyText}</span>
                )}
              </Link>
            ))}
          </div>
        )}
      </div>

      <h1 className="case-title">CoC Player</h1>
      <p className="case-sub">AI 当守秘人，你带调查员上桌</p>
    </header>
  )
}
