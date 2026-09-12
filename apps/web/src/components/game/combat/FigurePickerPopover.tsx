/**
 * 战棋轮廓选择器（KP 专属）。
 *
 * 为什么必须由人来点：`Combatant` 上只有名字与阵营，没有任何「这是人还是东西」的信息。
 * 照阵营一刀切的话，「不明存在」会和「陌生男性」共用同一个人形——而这两者在桌上是
 * 完全不同的两回事，扫一眼必须能分清。
 *
 * 平铺全部候选而不是做成下拉：轮廓是看图挑的，缩成一行文字就白挑了。
 */
import { FIGURE_GROUPS, defaultFigureKey, figureIcon, type FigureKey } from './figures'
import type { Combatant } from './types'

export function FigurePickerPopover({ c, onPick, onClose }: {
  c: Combatant
  onPick: (key: FigureKey) => void
  onClose: () => void
}) {
  const current = c.figure || defaultFigureKey(c.side)
  const Current = figureIcon(current)
  return (
    <div className="combat-figpick" role="dialog" aria-label={`为 ${c.name} 选择轮廓`}>
      <div className="combat-figpick-head">
        <Current size={15} aria-hidden="true" />
        <span className="flex-1 truncate">{c.name}</span>
        <button type="button" onClick={onClose} aria-label="关闭">×</button>
      </div>
      <div className="combat-figpick-body chat-scroll">
        {FIGURE_GROUPS.map((g) => (
          <div key={g.label}>
            <div className="combat-figpick-group">{g.label}</div>
            <div className="combat-figpick-grid">
              {g.items.map(({ key, label, Icon }) => (
                <button
                  key={key}
                  type="button"
                  className={`combat-figpick-cell${key === current ? ' combat-figpick-cell--on' : ''}`}
                  title={label}
                  onClick={() => onPick(key)}
                >
                  <Icon size={19} aria-hidden="true" />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
