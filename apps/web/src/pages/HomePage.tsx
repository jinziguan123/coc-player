import { Link } from 'react-router-dom'
import { Dices, Sparkles, Upload } from 'lucide-react'
import { GiScrollUnfurled, GiCharacter, GiBookmarklet } from 'react-icons/gi'

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
  return (
    <div className="mx-auto mt-12 w-full max-w-4xl">
      <div className="mb-10 text-center">
        <h1
          className="mb-2 text-4xl font-bold"
          style={{
            fontFamily: 'var(--font-display)',
            color: 'var(--color-text-accent)',
            letterSpacing: '0.06em',
            textShadow: '0 0 26px rgba(212, 162, 78, 0.22)',
          }}
        >
          TRPG Player
        </h1>
        <p style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--text-sm)' }}>
          AI 驱动的跑团平台
        </p>
        {/* 标题下的细分隔纹，收住居中标题与下方卡片 */}
        <div className="home-rule mx-auto mt-5" />
      </div>

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

      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        {SHORTCUTS.map(({ to, Icon, label }) => (
          <Link key={label} to={to} className="home-shortcut no-underline">
            <Icon /> {label}
          </Link>
        ))}
      </div>
    </div>
  )
}
