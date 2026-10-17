/**
 * 操作台：一个常驻输入框 + 全部动作平铺 + 斜杠命令。
 *
 * 针对现状的三处改动，每一处都对着一个具体的浪费：
 *  1. **不再有「更多…」下拉**。12 个动作里现在只有 3 个是直达按钮，其余 9 个要先拉开一个
 *     下拉菜单才找得到。跑团时眼睛在桌上、手在键盘上，多一次下拉就是多一次找。
 *  2. **默认动作就是旁白**。KP 大部分时间在写旁白，那就让「直接打字」等于写旁白，
 *     不必先点一次「叙事」把自己切进去。
 *  3. **斜杠命令**。要发检定时打 `/检`，手不用离开键盘去够鼠标。从调查员卡上点「检定」
 *     也是填进这里，两条路通向同一个地方。
 */
import { useEffect, useMemo, useRef, useState } from 'react'

import { ACTION_LABELS, type KpAction } from '@/components/game/kpActions'

/** 原型里能摆出来的动作 = 正式写作台的动作（解锁地点、移动角色早已由后端提供）。 */
export type DeckAction = KpAction

const DECK_LABELS: Record<DeckAction, string> = ACTION_LABELS

/** 命令 → 动作。中文缩写取「打两个字就够」的长度，KP 不该背全称。 */
const COMMANDS: { cmd: string; action: DeckAction; hint: string }[] = [
  { cmd: '叙事', action: 'narration', hint: '发布一段旁白' },
  { cmd: '台词', action: 'dialogue', hint: '让某个 NPC 说话' },
  { cmd: '检定', action: 'dice_check', hint: '要一次技能检定' },
  { cmd: '对抗', action: 'opposed_check', hint: '两方对着掷' },
  { cmd: '骰', action: 'generic_roll', hint: '临场裁定用的通用骰' },
  { cmd: 'san', action: 'san_check', hint: '理智检定' },
  { cmd: '场景', action: 'scene_change', hint: '把全桌挪到另一个场景' },
  { cmd: '手书', action: 'handout', hint: '发一份信件 / 剪报 / 日记' },
  { cmd: 'hp', action: 'hp_change', hint: '结算伤害或治疗' },
  { cmd: '开战', action: 'start_combat', hint: '进入战斗轮' },
  { cmd: '标志', action: 'set_flag', hint: '推进剧情标志' },
  { cmd: '解除', action: 'clear_flag', hint: '解除剧情标志' },
  { cmd: '解锁', action: 'unlock_place', hint: '把一个地点对玩家公开' },
  { cmd: '移动', action: 'move_actor', hint: '把某人挪到另一个地点' },
]

/** 平铺顺序按「跑团里用到的频次」排，不按后端枚举顺序——枚举顺序对 KP 没有意义。 */
const CHIP_ORDER: DeckAction[] = [
  'narration', 'dialogue', 'dice_check', 'san_check', 'generic_roll', 'opposed_check',
  'handout', 'scene_change', 'hp_change', 'start_combat', 'unlock_place', 'move_actor',
  'set_flag', 'clear_flag',
]

const PLACEHOLDERS: Partial<Record<DeckAction, string>> = {
  narration: '直接写旁白……   打 / 唤起命令',
  dialogue: '这位 NPC 说的话',
  dice_check: '要检定什么？例：山田健太 侦查 困难',
  san_check: '目击了什么？例：亨利·卡特 1/1d6',
  generic_roll: '掷什么？例：1d100',
  opposed_check: '谁对谁，各自用什么技能',
  handout: '这份手书上写着什么',
  scene_change: '切到哪个场景',
  hp_change: '谁，加减多少',
  start_combat: '谁参战，从哪一轮开始',
  unlock_place: '把哪个地点对玩家公开',
  move_actor: '把谁挪到哪里',
}

export function CommandDeck({ action, onActionChange, draft, onDraftChange, onSubmit }: {
  action: DeckAction
  onActionChange: (a: DeckAction) => void
  draft: string
  onDraftChange: (v: string) => void
  onSubmit: () => void
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [cursor, setCursor] = useState(0)

  // 斜杠命令只在「整段输入以 / 开头」时唤起：写旁白时句中出现的斜杠不该弹面板。
  const query = draft.startsWith('/') ? draft.slice(1) : null
  const matches = useMemo(() => {
    if (query == null) return []
    const q = query.trim().toLowerCase()
    if (!q) return COMMANDS
    return COMMANDS.filter((c) =>
      c.cmd.toLowerCase().includes(q) || DECK_LABELS[c.action].includes(q))
  }, [query])
  const open = matches.length > 0

  useEffect(() => { setCursor(0) }, [query])

  const pick = (i: number) => {
    const hit = matches[i]
    if (!hit) return
    onActionChange(hit.action)
    onDraftChange('')
    inputRef.current?.focus()
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (open) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => (c + 1) % matches.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => (c - 1 + matches.length) % matches.length); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(cursor); return }
      if (e.key === 'Escape') { e.preventDefault(); onDraftChange(''); return }
    }
    // 回车发出、Shift+回车换行：和项目里其它输入框同一个约定
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault(); onSubmit()
    }
  }

  return (
    <div className="kl-deck">
      <div className="kl-deck-input-wrap">
        {open && (
          <div className="kl-palette" role="listbox" aria-label="命令">
            {matches.map((m, i) => (
              <button
                key={m.action}
                role="option"
                aria-selected={i === cursor}
                className={`kl-palette-row ${i === cursor ? 'kl-palette-row--on' : ''}`}
                onMouseEnter={() => setCursor(i)}
                onClick={() => pick(i)}
              >
                <span className="kl-palette-cmd">/{m.cmd}</span>
                <span>{DECK_LABELS[m.action]}</span>
                <span className="kl-palette-hint">{m.hint}</span>
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={inputRef}
          className="kl-deck-input"
          value={draft}
          placeholder={PLACEHOLDERS[action] || '写点什么'}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
        />
        <button className="btn-primary whitespace-nowrap !px-4" onClick={onSubmit}>
          {DECK_LABELS[action]}
        </button>
      </div>

      <div className="kl-chips">
        {CHIP_ORDER.map((a) => (
          <button
            key={a}
            className={`kl-chip ${a === action ? 'kl-chip--on' : ''}`}
            onClick={() => onActionChange(a)}
          >
            {DECK_LABELS[a]}
          </button>
        ))}
      </div>
    </div>
  )
}
