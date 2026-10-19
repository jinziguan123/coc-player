/**
 * @ 补全菜单的本体：只负责把行画出来，判定、两级切换与插入都在 useMentionMenu。
 *
 * 单独成文件的两个理由：组件文件里混着导出的 hook 会让 fast refresh 退化成整页刷新
 * （同 kpCatalogs.ts 的注释），以及 useMentionMenu 的行数本来就该留给状态机。
 *
 * 两级的行长得一样（图标 + 名字 + 右侧说明），只有右侧说明不同：一级是「N 项 ›」，
 * 二级是地点状态或技能值。键盘高亮用同一个 cursor——两级各自复位，语义不混。
 */
import { useEffect, useRef, type ReactNode } from 'react'

import type { MentionCandidate } from './mentionSources'

export interface PaletteItem extends MentionCandidate {
  /** 按 kind 定的图标底色 class（who / where / skill）。 */
  cls: string
  icon: ReactNode
}

export interface PaletteGroup {
  key: string
  label: string
  count: number
  cls: string
  icon: ReactNode
}

type Rows =
  | { mode: 'groups'; groups: PaletteGroup[] }
  | { mode: 'items'; items: PaletteItem[]; scopeLabel?: string }

export function MentionPalette(input: Rows & {
  cursor: number
  onHover: (i: number) => void
  onPick: (i: number) => void
}) {
  const { cursor, onHover, onPick } = input
  // 长列表（技能动辄五十多项）用方向键走的时候，高亮行必须自己滚进可视区，
  // 否则按十几下也看不见选中了谁。只有高亮行变了才滚，鼠标划过不会带着整页抖。
  const activeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [cursor])
  const activeProps = (i: number) => (i === cursor ? { ref: activeRef } : {})
  return (
    <div
      className="kp-mention"
      role="listbox"
      aria-label="可以提到的人与地方"
      // 别让点选把焦点从输入框抢走：抢走会先触发 textarea 的 blur（菜单当场收起），
      // 这一下 click 就落空了。preventDefault 掉 mousedown 是最省事的守法。
      onMouseDown={(e) => e.preventDefault()}
    >
      {input.mode === 'items' && input.scopeLabel && (
        // 二级的头部：不是候选行，所以不进 role=option，也不占键盘高亮的序号。
        <div className="kp-mention-head">
          <span>{input.scopeLabel}</span>
          <span className="kp-mention-hint">Esc 返回</span>
        </div>
      )}
      {input.mode === 'groups'
        ? input.groups.map((g, i) => (
          <button
            key={g.key}
            type="button"
            role="option"
            aria-selected={i === cursor}
            className={`kp-mention-row ${i === cursor ? 'kp-mention-row--on' : ''}`}
            onMouseEnter={() => onHover(i)}
            onClick={() => onPick(i)}
            {...activeProps(i)}
          >
            <span className={`kp-mention-ic ${g.cls}`}>{g.icon}</span>
            <span className="kp-mention-name">{g.label}</span>
            <span className="kp-mention-hint">{g.count} 项 ›</span>
          </button>
        ))
        : input.items.map((m, i) => (
          <button
            key={`${m.kind}:${m.label}`}
            type="button"
            role="option"
            aria-selected={i === cursor}
            className={`kp-mention-row ${i === cursor ? 'kp-mention-row--on' : ''}`}
            onMouseEnter={() => onHover(i)}
            onClick={() => onPick(i)}
            {...activeProps(i)}
          >
            <span className={`kp-mention-ic ${m.cls}`}>{m.icon}</span>
            <span className="kp-mention-name">{m.label}</span>
            {m.hint && <span className="kp-mention-hint">{m.hint}</span>}
          </button>
        ))}
    </div>
  )
}
