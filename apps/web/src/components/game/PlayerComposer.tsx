/**
 * 玩家发言框：草稿 textarea + @ 补全菜单 + 发送按钮。
 *
 * 从 GameSessionPage 搬出来（那个页面顶着行数红线，tests/fileSize.test.ts）：这一块自带
 * 一串输入期行为——自动长高、输入法组字守卫、@ 触发段接线、菜单开着时 Enter 先选人——
 * 都是「输入框自己的事」，跟着输入框走比留在页面里清楚。
 */
import { useRef, useState, type KeyboardEvent, type RefObject } from 'react'

import {
  applyDraftHeight, useMentionCandidates, usePlayerMentions, type MentionLocationLike,
} from './usePlayerMentions'
import type { PartySeatLike } from './mentionSources'

export function PlayerComposer(input: {
  /** 发送：拿到的是「要发的原文」。发送失败时页面负责把草稿放回来（见 restore）。 */
  onSend: (text: string) => void
  placeholder: string
  /** KP 叙事中：草稿照收，但不发送（同原先的 streaming 行为）。 */
  locked?: boolean
  seats?: PartySeatLike[] | null
  locations?: MentionLocationLike[] | null
  skills?: Record<string, number> | null
  /** 打进草稿时上报「正在输入」（节流交给调用方）。 */
  onTyping: (text: string) => void
  textareaRef?: RefObject<HTMLTextAreaElement | null>
}) {
  const { onSend, placeholder, locked, seats, locations, skills, onTyping, textareaRef } = input
  const [draft, setDraft] = useState('')
  const ownRef = useRef<HTMLTextAreaElement>(null)
  const ref = textareaRef ?? ownRef
  const composing = useRef(false)

  const { candidates, icons } = useMentionCandidates({ seats, locations, skills })
  const menu = usePlayerMentions({ candidates, icons, textareaRef: ref, onTextChange: setDraft })

  const submit = () => {
    if (locked || !draft.trim()) return
    onSend(draft.trim())
    setDraft('')
    onTyping('')
    const el = ref.current
    if (el) applyDraftHeight(el)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法组字中：Enter 是选字，不是发送。菜单那边有同样的守卫。
    if (composing.current || e.nativeEvent.isComposing) return
    // 菜单先消费：上下键选人、Enter/Tab 选中；消费掉就不再当「发送」。
    if (menu.onKeyDown(e, draft)) return
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <>
      {/* 外壳只负责给菜单定位：菜单浮在输入框上方（同 KP 斜杠面板的理由——
          往下开会盖住发送按钮，而整条输入区贴着底边）。 */}
      <div className="chat-input-wrap">
        {menu.palette}
        <textarea
          ref={ref}
          data-tour="input"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            menu.sync(e.target)
            applyDraftHeight(e.target)
            onTyping(e.target.value)
          }}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={() => { composing.current = false }}
          onKeyDown={onKeyDown}
          // 点到输入框以外就收起（菜单本身的 mousedown 被 preventDefault，不会走到这里）。
          onBlur={() => menu.close()}
          // 只有「点一下把光标挪到别处」需要重新同步：打字与方向键都已经走 onChange。
          // 特意不在 keyUp 上同步——选中项之后那次 keyUp 会从 DOM 读回旧光标位
          // （React 刚赋完值、selection 还没落定），把刚收起的菜单又弹回来。
          onClick={(e) => menu.sync(e.currentTarget)}
          placeholder={placeholder}
          className="input flex-1"
          rows={1}
          style={{ resize: 'none' }}
        />
      </div>
      <button onClick={submit} disabled={!!locked || !draft.trim()} className="btn-primary">
        发送
      </button>
    </>
  )
}
