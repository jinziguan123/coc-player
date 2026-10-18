import { NavLink } from 'react-router-dom'
import {
  GiArchiveResearch,
  GiCharacter,
  GiDiceTwentyFacesTwenty,
  GiGears,
  GiRuleBook,
  GiScrollUnfurled,
} from 'react-icons/gi'
import { canLeave } from '@/lib/navGuard'

/**
 * 手机底部导航：<=767px 时替代桌面左侧栏。
 *
 * 六个一级入口与 Sidebar 完全一致——手机上少一个入口，用户就真的没有别的路可走；
 * 六个格子各约 60px，图标 + 两字标签放得下。放不下的操作（检索、战报等）留在页面内。
 *
 * 对局页（/game/:id）不渲染它：聊天输入框就在屏幕底部，再压一条导航会顶掉输入区，
 * 而且对局内的「返回」已经在顶栏。见 AppShell。
 */
const NAV_ITEMS = [
  { to: '/', label: '卷宗', icon: GiArchiveResearch },
  { to: '/modules', label: '模组', icon: GiScrollUnfurled },
  { to: '/rules', label: '规则', icon: GiRuleBook },
  { to: '/characters', label: '角色', icon: GiCharacter },
  { to: '/game', label: '游戏', icon: GiDiceTwentyFacesTwenty },
  { to: '/settings', label: '设置', icon: GiGears },
]

export function MobileNav() {
  return (
    <nav className="mobile-nav" aria-label="主导航">
      {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          title={label}
          aria-label={label}
          // 有未保存改动的页面（如规则页）先问它，与桌面侧栏同一套守卫
          onClick={(e) => { if (!canLeave(to)) e.preventDefault() }}
          className={({ isActive }) => `mobile-nav-link ${isActive ? 'active' : ''}`}
        >
          <Icon aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
