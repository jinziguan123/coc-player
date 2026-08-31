import { Link } from 'react-router-dom'
import { Dices, Sparkles, Upload } from 'lucide-react'
import { GiScrollUnfurled, GiCharacter, GiBookmarklet } from 'react-icons/gi'
import { HomeIntro } from '@/components/home/HomeIntro'
import { CaseFileHeader } from '@/components/home/CaseFileHeader'
import { ResumeSessions } from '@/components/home/ResumeSessions'
import { useHomeInventory } from '@/features/home/useHomeInventory'

/** 首页入口卡：图标 + 标题 + 一句说明，比裸按钮更能交代「点进去是什么」。 */
const ENTRIES = [
  {
    to: '/onboarding',
    Icon: Sparkles,
    title: '体验新手团',
    desc: '十分钟走完一场微型调查，边玩边熟悉规则与投骰',
    primary: true,
  },
  // primary 要每项都有：`as const` 下缺省会让联合类型里读不到该字段
  { to: '/game', Icon: Dices, title: '开始游戏', desc: '开新局或用房间码加入队友的桌', primary: false },
  { to: '/modules', Icon: Upload, title: '上传模组', desc: '导入剧本，由 AI 解析成场景、NPC 与线索', primary: false },
] as const

/** 次级入口：已有素材的快速跳转。 */
const SHORTCUTS = [
  { to: '/modules', Icon: GiScrollUnfurled, label: '模组库' },
  { to: '/characters', Icon: GiCharacter, label: '角色' },
  { to: '/rulebooks', Icon: GiBookmarklet, label: '规则书' },
] as const

export function HomePage() {
  // 库存拉一次，抬头、续玩卡、新手介绍三处共用（见 useHomeInventory 的说明）
  const inventory = useHomeInventory()

  return (
    // 5xl 而不是 4xl：下方介绍要并排成三栏，容器太窄会把规则表挤到换行。
    // 上下留白也一并收紧——首页的目标是「一屏看全」，标题区不该独吞四分之一屏。
    <div className="mx-auto mt-5 w-full max-w-5xl">
      <CaseFileHeader inventory={inventory} />

      <div className="grid gap-3 sm:grid-cols-3">
        {ENTRIES.map(({ to, Icon, title, desc, primary }) => (
          <Link
            key={title}
            to={to}
            className={`home-entry card !p-4 flex flex-col gap-2 no-underline ${primary ? 'home-entry--primary' : ''}`}
          >
            {/* 纹章框图标：与角色名录的 .char-sigil 同一语言，三个入口像一排铸印 */}
            <span className="home-entry-icon" aria-hidden="true"><Icon /></span>
            <span
              className="font-semibold"
              style={{ fontFamily: 'var(--font-title)', fontSize: 'var(--text-lg)', color: 'var(--color-text-primary)' }}
            >
              {title}
            </span>
            <span
              className="leading-relaxed"
              style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}
            >
              {desc}
            </span>
          </Link>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
        {SHORTCUTS.map(({ to, Icon, label }) => (
          <Link key={label} to={to} className="home-shortcut no-underline">
            <Icon /> {label}
          </Link>
        ))}
      </div>

      {/* 还开着的桌摆在入口之下：回到进行中的游戏是这个工作台上最高频的事，
          此前却要先点「开始游戏」再去列表里找。一桌都没开时整块不渲染。 */}
      {inventory && <ResumeSessions sessions={inventory.openSessions} />}

      {/* 一个调查员都没有 = 还没上过桌，这种人才需要看科普；老玩家默认收起。 */}
      <HomeIntro defaultOpen={inventory?.characters === 0} />
    </div>
  )
}
