import { Link } from 'react-router-dom'
import type { HomeInventory } from '@/features/home/useHomeInventory'
import { CountUp } from '@/components/ui/CountUp'

/**
 * 卷宗抬头。
 *
 * 这一页在侧边栏里就叫「卷宗」（图标都是 GiArchiveResearch），可它长得一直是「居中大标题
 * ＋三张并排圆角卡」——任何一个 SaaS 落地页都长这样，和 1920 年代的档案没有半点关系。
 * 产品自己的语汇没在视觉上兑现，是这一页最没个性的地方。
 *
 * 所以把顶部改成一份卷宗的抬头：**左对齐**（档案是左对齐的，居中是海报的语言），
 * 题铭之下一条三格的库存横带（装饰艺术的「铭牌」），数字首屏从 0 滚上来。
 *
 * 原先标题上头还有一枚「卷宗」书签，拿掉了：侧边栏第一项已经高亮着「卷宗」，书签只是
 * 把同一件事再说一遍；它还随各页容器宽度浮动，跳页时看着像在飘（详见 ArchiveHead）。
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

function rowsOf(inv: HomeInventory): Row[] {
  return [
    { label: '调查员', count: inv.characters, emptyText: '建一位调查员', to: '/characters' },
    { label: '模组', count: inv.modules, emptyText: '导入模组', to: '/modules' },
    { label: '在跑', count: inv.openSessions.length, emptyText: '开一局', to: '/game' },
  ]
}

export function CaseFileHeader({ inventory }: { inventory: HomeInventory | null }) {
  return (
    <header className="case-head">
      <p className="case-eyebrow">CoC Player · 克苏鲁的呼唤 第七版</p>
      <h1 className="case-title">AI 当守秘人，<br />你带调查员上桌</h1>
      {inventory && (
        <div className="case-band">
          {rowsOf(inventory).map(({ label, count, emptyText, to }) => (
            <Link
              key={label}
              to={to}
              className="case-band-cell no-underline"
              // 读屏里「调查员 7」听着像半句话，补一句完整的
              aria-label={count > 0 ? `${label} ${count}，前往` : emptyText}
            >
              {count > 0 ? (
                <>
                  <CountUp value={count} className="case-band-num" />
                  <span className="case-band-label">{label}</span>
                </>
              ) : (
                <span className="case-stat-empty">{emptyText}</span>
              )}
            </Link>
          ))}
        </div>
      )}
    </header>
  )
}
