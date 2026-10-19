/**
 * 玩家发言框的 @ 补全接线：候选从哪来、图标给谁、选中后怎么写回输入框。
 *
 * 单独成 hook 是给 GameSessionPage 腾地方——那个页面顶着行数红线（tests/fileSize.test.ts），
 * 按约定新增内容进新文件而不是把小数字调大。
 *
 * 只在玩家席用：KP 走写作台，他要的是 /动作 而不是 @某人。
 */
import { useMemo, type ReactNode, type RefObject } from 'react'
import { GiCharacter, GiPositionMarker, GiRollingDices } from 'react-icons/gi'

import { useMentionMenu } from './useMentionMenu'
import { buildCandidates, type MentionCandidate, type PartySeatLike } from './mentionSources'

/** 地点只取这几个字段，形状与 useKnownLocations 的 KnownLocation 兼容。 */
export interface MentionLocationLike {
  id: string
  name: string
  current?: boolean
  visited?: boolean
}

/** 草稿框按内容重算高度：清空回到一行，否则按 scrollHeight 长、上限内收住。
 *
 * 手机与桌面各一个上限（手机上给更大的草稿视野，但不超过视口 40%）。这段原本只写在
 * GameSessionPage 的 onChange 里，@ 补全插入后也要走同一套，所以收在这里一份。
 */
export function applyDraftHeight(el: HTMLTextAreaElement) {
  el.style.height = 'auto'
  const narrow = typeof window.matchMedia === 'function'
    && window.matchMedia('(max-width: 767px)').matches
  const cap = narrow
    ? Math.min((window.visualViewport?.height ?? window.innerHeight) * 0.4, 220)
    : 120
  el.style.height = (narrow && !el.value ? 44 : Math.min(el.scrollHeight, cap)) + 'px'
}

/** 候选与图标。抽成 hook 是因为它要 useMemo——直接写成函数会在每帧重算一遍候选表。 */
export function useMentionCandidates(input: {
  seats?: PartySeatLike[] | null
  locations?: MentionLocationLike[] | null
  skills?: Record<string, number> | null
}): { candidates: MentionCandidate[]; icons: Record<'character' | 'location' | 'skill', ReactNode> } {
  const { seats, locations, skills } = input
  const candidates = useMemo(() => buildCandidates({ seats, locations, skills }), [seats, locations, skills])
  // 图标由页面侧注入，hook 不引图标库展示逻辑——与项目「图标不进文本、由前端按语义渲染」同一条约定。
  const icons = useMemo(
    () => ({
      character: <GiCharacter size={12} />,
      location: <GiPositionMarker size={12} />,
      skill: <GiRollingDices size={12} />,
    }),
    [],
  )
  return { candidates, icons }
}

export function usePlayerMentions(input: {
  candidates: MentionCandidate[]
  icons: Record<'character' | 'location' | 'skill', ReactNode>
  /** 输入框引用：选中后要把光标放回去（受控赋值会把光标甩到串尾）。 */
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** 文本变了（含补全插入）：调用方据此写回草稿 state。 */
  onTextChange: (text: string) => void
}) {
  const { candidates, icons, textareaRef, onTextChange } = input
  return useMentionMenu({
    candidates,
    icons,
    onPick: ({ text, caret }) => {
      onTextChange(text)
      const el = textareaRef.current
      if (!el) return
      el.focus({ preventScroll: true })
      el.setSelectionRange(caret, caret)
      applyDraftHeight(el)
    },
  })
}
