import { Link } from 'react-router-dom'
import { m } from 'motion/react'
import { GiCompass, GiRollingDices, GiScrollUnfurled } from 'react-icons/gi'
import { ArrowRight } from 'lucide-react'
import { HomeIntro } from '@/components/home/HomeIntro'
import { CaseFileHeader } from '@/components/home/CaseFileHeader'
import { HomePortal } from '@/components/home/HomePortal'
import { ResumeSessions } from '@/components/home/ResumeSessions'
import { pickFeatured, useHomeInventory } from '@/features/home/useHomeInventory'
import { itemVariants, listVariants } from '@/lib/motion'

/** 开一局新的三条路：大字列表而不是三张同权卡片——焦点让给右边的拱门。 */
const ENTRIES = [
  { to: '/onboarding', Icon: GiCompass, title: '体验新手团', desc: '十分钟走完一场微型调查，边玩边熟悉规则与投骰', primary: true },
  { to: '/game', Icon: GiRollingDices, title: '开始游戏', desc: '开新局，或用房间码加入队友的桌', primary: false },
  { to: '/modules', Icon: GiScrollUnfurled, title: '上传模组', desc: '导入剧本，由 AI 解析成场景、NPC 与线索', primary: false },
] as const

export function HomePage() {
  // 库存拉一次，抬头、拱门、续玩列表、新手介绍几处共用（见 useHomeInventory 的说明）
  const inventory = useHomeInventory()
  const featured = inventory ? pickFeatured(inventory.openSessions) : null
  const others = inventory?.openSessions.filter((s) => s.id !== featured?.id) ?? []

  return (
    // 左：题铭、库存、入口；右：拱门。宽屏两栏铺满，不再挤在左上角；窄屏拱门落到下面。
    <div className="home">
      <section className="home-lead">
        <CaseFileHeader inventory={inventory} />

        <div>
          <h2 className="home-label">开一局新的</h2>
          <m.ul className="home-entries" variants={listVariants} initial="initial" animate="enter">
            {ENTRIES.map(({ to, Icon, title, desc, primary }) => (
              <m.li key={title} variants={itemVariants}>
                <Link to={to} className={`home-entry no-underline ${primary ? 'home-entry--primary' : ''}`}>
                  <span className="home-entry-icon" aria-hidden><Icon /></span>
                  <span className="home-entry-text">
                    <span className="home-entry-title">{title}</span>
                    <span className="home-entry-desc">{desc}</span>
                  </span>
                  <ArrowRight className="home-entry-arrow" size={18} aria-hidden />
                </Link>
              </m.li>
            ))}
          </m.ul>
        </div>

        {/* 最近那桌在拱门里，这里只列剩下的；没有别的桌时整块不渲染 */}
        <ResumeSessions sessions={others} />

        {/* 一个调查员都没有 = 还没上过桌，这种人才需要看科普；老玩家默认收起。 */}
        <div className="mt-auto pt-6">
          <HomeIntro defaultOpen={inventory?.characters === 0} />
        </div>
      </section>

      {/* 库存还没回来时不画拱门：先画空门再换成「上次那桌」会闪一下 */}
      {inventory !== null && <HomePortal session={featured} />}
    </div>
  )
}
