import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { m } from 'motion/react'
import { GiScrollUnfurled, GiCharacter, GiDiceTwentyFacesTwenty, GiArchiveResearch, GiGears, GiRuleBook, GiSandsOfTime } from 'react-icons/gi'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { spring } from '@/lib/motion'
import { DecoSunburst } from '@/components/ui/deco'
import { canLeave } from '@/lib/navGuard'
import { runningJobCount, useUploadJobsStore } from '@/stores/uploadJobsStore'

const NAV_ITEMS = [
  { to: '/', label: '卷宗', icon: GiArchiveResearch },
  { to: '/modules', label: '模组', icon: GiScrollUnfurled },
  // 「规则」页含两个标签：本桌规则（决定骰子怎么判）与规则书（给 AI 查阅的参考）
  { to: '/rules', label: '规则', icon: GiRuleBook },
  { to: '/characters', label: '角色', icon: GiCharacter },
  { to: '/game', label: '游戏', icon: GiDiceTwentyFacesTwenty },
  { to: '/settings', label: '设置', icon: GiGears },
]

/**
 * @param autoCollapse 进入对局时自动收成图标栏，把宽度让给叙事流；用户在对局里手动展开
 *   只在本次对局内有效，不改写全局的收起偏好——离开对局后回到他平时的习惯。
 */
export function Sidebar({ autoCollapse = false }: { autoCollapse?: boolean }) {
  const [collapsedPref, setCollapsedPref] = useState(
    () => localStorage.getItem('trpg_sidebar_collapsed') === '1',
  )
  // 对局内的临时覆盖：null = 跟随自动收起
  const [sessionOverride, setSessionOverride] = useState<boolean | null>(null)
  const [lastAuto, setLastAuto] = useState(autoCollapse)
  if (lastAuto !== autoCollapse) {
    setLastAuto(autoCollapse)
    setSessionOverride(null)
  }
  const collapsed = autoCollapse ? (sessionOverride ?? true) : collapsedPref
  const runningJobs = useUploadJobsStore((s) => runningJobCount(s.jobs))
  const jobsOpen = useUploadJobsStore((s) => s.open)
  const setJobsOpen = useUploadJobsStore((s) => s.setOpen)

  const toggle = () => {
    if (autoCollapse) {
      setSessionOverride(!collapsed)
      return
    }
    setCollapsedPref((c) => {
      const next = !c
      localStorage.setItem('trpg_sidebar_collapsed', next ? '1' : '0')
      return next
    })
  }

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-logo" title="CoC Player">
        <DecoSunburst className="sidebar-logo-mark" />
        {/* 收展时文字换成另一套：等宽度过渡走完大半再淡入，否则「COC PLAYER」会在
            还没展开的窄栏里被居中裁成「OC PLAY」 */}
        <m.span
          key={collapsed ? 'short' : 'long'}
          className="sidebar-logo-word"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.2, delay: collapsed ? 0 : 0.22 }}
        >
          {collapsed ? 'COC' : 'COC PLAYER'}
        </m.span>
        {!collapsed && (
          <m.span
            className="sidebar-logo-sub"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2, delay: 0.26 }}
          >
            克苏鲁的呼唤 · 第七版
          </m.span>
        )}
      </div>
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            title={label}
            // 当前页有没保存的改动时先问它（如规则页）；它会自己弹框，确认后再跳
            onClick={(e) => { if (!canLeave(to)) e.preventDefault() }}
            className={({ isActive }) =>
              `sidebar-link ${isActive ? 'active' : ''}`
            }
          >
            {({ isActive }) => (
              <>
                {/* 当前项的黄铜底与竖条：同一个 layoutId 在各项之间滑动，而不是这里灭那里亮 */}
                {isActive && (
                  <m.span layoutId="sidebar-active" className="sidebar-link-active" transition={spring.snappy} />
                )}
                <Icon />
                <span className="sidebar-link-label">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>
      {/* 模组解析在后台跑，刷新/换页都不影响——这里随时能看进度 */}
      <div className="sidebar-jobs">
        <button
          type="button"
          className={`sidebar-link ${jobsOpen ? 'active' : ''}`}
          onClick={() => setJobsOpen(!jobsOpen)}
          aria-expanded={jobsOpen}
          title={runningJobs ? `解析进度（${runningJobs} 个进行中）` : '解析进度'}
        >
          <GiSandsOfTime className={runningJobs ? 'sidebar-jobs-spin' : undefined} />
          <span className="sidebar-link-label">解析进度</span>
          {runningJobs > 0 && <i className="sidebar-jobs-badge" aria-label={`${runningJobs} 个进行中`}>{runningJobs}</i>}
        </button>
      </div>
      <button
        className="sidebar-toggle"
        onClick={toggle}
        title={collapsed ? '展开菜单' : '收起菜单'}
      >
        {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        <span>收起</span>
      </button>
      <div
        className="sidebar-footer"
        style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
      >
        {collapsed ? 'v0' : 'v0.1.0'}
      </div>
    </aside>
  )
}
