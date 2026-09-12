/**
 * 轮廓选择器：给某个参战方挑一枚战棋剪影。
 *
 * 做成一次点开、平铺全部选项的小面板，而不是下拉——挑轮廓是看图选的，
 * 下拉把图缩成一行文字就白挑了。
 */
import { FIGURE_GROUPS, figureIcon, type FigureKey } from './figures'

export function FigurePicker({ current, name, onPick, onClose }: {
  current: FigureKey
  name: string
  onPick: (key: FigureKey) => void
  onClose: () => void
}) {
  const Current = figureIcon(current)
  return (
    <div className="kl-figpick" role="dialog" aria-label={`为 ${name} 选择轮廓`}>
      <div className="kl-figpick-head">
        <Current size={16} aria-hidden="true" />
        <span className="flex-1 truncate">{name}</span>
        <button className="kl-figpick-close" onClick={onClose} aria-label="关闭">×</button>
      </div>
      <div className="kl-figpick-body">
        {FIGURE_GROUPS.map((g) => (
          <div key={g.label}>
            <div className="kl-figpick-group">{g.label}</div>
            <div className="kl-figpick-grid">
              {g.items.map(({ key, label, Icon }) => (
                <button
                  key={key}
                  className={`kl-figpick-cell ${key === current ? 'kl-figpick-cell--on' : ''}`}
                  title={label}
                  onClick={() => onPick(key)}
                >
                  <Icon size={20} aria-hidden="true" />
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
