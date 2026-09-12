/**
 * 斜杠命令：在 KP 的输入框里打 `/` 唤起动作面板。
 *
 * 为什么值得单做一个：真人 KP 抱怨的核心是「产出不顺手」，而最直接的一处就是——
 * 想发一次检定，手得从键盘挪到鼠标、找到那个动作、再挪回来。打两个字就切过去，
 * 这条路全程不离键盘。
 *
 * 只在**整段输入以 / 开头**时唤起：写旁白时句中出现的斜杠（网址、日期）不该弹面板。
 */
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react'

import { ACTION_COMMANDS, ACTION_LABELS, type KpAction } from './kpActions'

export function useSlashCommands(input: {
  text: string
  onPick: (action: KpAction) => void
}): { palette: ReactNode; onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => boolean } {
  const { text, onPick } = input
  const [cursor, setCursor] = useState(0)

  const query = text.startsWith('/') ? text.slice(1) : null
  const matches = useMemo(() => {
    if (query == null) return []
    const q = query.trim().toLowerCase()
    if (!q) return ACTION_COMMANDS
    return ACTION_COMMANDS.filter(
      (c) => c.cmd.toLowerCase().includes(q) || ACTION_LABELS[c.action].includes(q),
    )
  }, [query])
  const open = matches.length > 0

  useEffect(() => { setCursor(0) }, [query])

  const pick = (i: number) => {
    const hit = matches[i]
    if (hit) onPick(hit.action)
  }

  /** 返回 true 表示这一下按键已被面板消费，调用方不要再处理（如别再当成「发布」）。 */
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!open) return false
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => (c + 1) % matches.length); return true }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => (c - 1 + matches.length) % matches.length); return true }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(cursor); return true }
    if (e.key === 'Escape') { e.preventDefault(); onPick('narration'); return true }
    return false
  }

  const palette = open ? (
    <div className="kp-slash" role="listbox" aria-label="KP 命令">
      {matches.map((m, i) => (
        <button
          key={m.action}
          type="button"
          role="option"
          aria-selected={i === cursor}
          className={`kp-slash-row ${i === cursor ? 'kp-slash-row--on' : ''}`}
          onMouseEnter={() => setCursor(i)}
          onClick={() => pick(i)}
        >
          <span className="kp-slash-cmd">/{m.cmd}</span>
          <span>{ACTION_LABELS[m.action]}</span>
          <span className="kp-slash-hint">{m.hint}</span>
        </button>
      ))}
    </div>
  ) : null

  return { palette, onKeyDown }
}
