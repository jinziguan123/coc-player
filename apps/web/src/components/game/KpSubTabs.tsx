/**
 * 「资料」档下的二级标签（参谋 / 配图与队友 / 导演台 / 模组资料）。
 *
 * 单拎出来是因为它纯粹是导航——一组按钮加一次回调，与 KP 工作台的任何状态都不耦合。
 * 标签表本身在 kpActions.ts：组件文件里混着导出常量，热更新会退化成整页刷新。
 */
import { REFERENCE_TABS, type ReferenceTab } from './kpActions'

export function KpSubTabs({ tab, onPick }: {
  tab: string
  onPick: (id: ReferenceTab) => void
}) {
  return (
    <div className="kp-subtabs">
      {REFERENCE_TABS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          onClick={() => onPick(id)}
          className={`kp-subtab inline-flex items-center gap-1 whitespace-nowrap ${tab === id ? 'kp-subtab--active' : ''}`}
          aria-pressed={tab === id}
        >
          <Icon size={12} /> {label}
        </button>
      ))}
    </div>
  )
}
