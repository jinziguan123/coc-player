/**
 * @ 补全菜单的本体：只负责把候选画出来，判定与插入都在 useMentionMenu。
 *
 * 单独成文件的两个理由：组件文件里混着导出的 hook 会让 fast refresh 退化成整页刷新
 * （同 kpCatalogs.ts 的注释），以及 useMentionMenu 的行数本来就该留给状态机。
 */
import type { ReactNode } from 'react'

import type { MentionCandidate } from './mentionSources'

export interface PaletteItem extends MentionCandidate {
  /** 按 kind 定的图标底色 class（who / where / skill）。 */
  cls: string
  icon: ReactNode
}

export function MentionPalette(input: {
  items: PaletteItem[]
  cursor: number
  onHover: (i: number) => void
  onPick: (i: number) => void
}) {
  const { items, cursor, onHover, onPick } = input
  return (
    <div
      className="kp-mention"
      role="listbox"
      aria-label="可以提到的人与地方"
      // 别让点选把焦点从输入框抢走：抢走会先触发 textarea 的 blur（菜单当场收起），
      // 这一下 click 就落空了。preventDefault 掉 mousedown 是最省事的守法。
      onMouseDown={(e) => e.preventDefault()}
    >
      {items.map((m, i) => (
        <button
          key={`${m.kind}:${m.label}`}
          type="button"
          role="option"
          aria-selected={i === cursor}
          className={`kp-mention-row ${i === cursor ? 'kp-mention-row--on' : ''}`}
          onMouseEnter={() => onHover(i)}
          onClick={() => onPick(i)}
        >
          <span className={`kp-mention-ic ${m.cls}`}>{m.icon}</span>
          <span className="kp-mention-name">{m.label}</span>
          {m.hint && <span className="kp-mention-hint">{m.hint}</span>}
        </button>
      ))}
    </div>
  )
}
