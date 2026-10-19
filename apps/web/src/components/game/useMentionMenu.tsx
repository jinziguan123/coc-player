/**
 * 输入框里的 @ 补全：光标处的触发、候选菜单、键盘选择与插入。
 *
 * 与 useSlashCommands（KP 的 / 命令面板）刻意不复用同一套：斜杠的判定是「整串以 / 开头」，
 * 这里是「@ 前一个字符不是词内字符」——两者只有"弹一个列表"这一层像，硬合成一个 hook 会得到
 * 一个谁都不好用的中间态。真正共用的部分（触发段解析、插入与光标）已经沉在 mentionTrigger.ts，
 * 那才是会写错的地方。
 *
 * 只在**玩家发言框**里用：KP 走写作台，他要的是 /动作 而不是 @某人。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import { completeMention, detectMention, type MentionTrigger } from './mentionTrigger'
import { filterCandidates, type MentionCandidate, type MentionKind } from './mentionSources'
import { MentionPalette } from './MentionPalette'

/** 菜单本体与两个事件处理器。返回值里的成员都是稳定的 useCallback：
 *  调用方可以把它们直接放进 useEffect 依赖，不必再包一层。 */
export interface MentionMenu {
  /** 菜单本体；没有命中时为 null（不占位）。 */
  palette: ReactNode | null
  /** 文本或光标变了就调它——菜单是否开着、候选是什么，全由这里算。
   *  只要求 value/selectionStart 两个字段，传原生事件目标或造的对象都行。 */
  sync: (el: { value: string; selectionStart: number | null }) => void
  /** 返回 true 表示这一下按键已被菜单消费，调用方不要再处理（如别再当成"发送"）。 */
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>, text: string) => boolean
}

/** 图标由调用方注入（react-icons/gi 的 Gi* 组件），这个文件不引图标库——
 *  与项目「图标不进文本、由前端按语义渲染」的约定一致。 */
export interface MentionIcons {
  character: ReactNode
  location: ReactNode
  skill: ReactNode
}

const KIND_CLASS: Record<MentionKind, string> = {
  character: 'kp-mention-ic--who',
  location: 'kp-mention-ic--where',
  skill: 'kp-mention-ic--skill',
}

export function useMentionMenu(input: {
  candidates: MentionCandidate[]
  /** 选中后拿回新文本与新光标位；调用方负责 setInput + 放光标 + 重算高度。 */
  onPick: (next: { text: string; caret: number }) => void
  icons: MentionIcons
}): MentionMenu {
  const { candidates, onPick, icons } = input
  // 文本整串存一份（不是只存光标位）：插入时要按 from..to 切开原文，中间态拿不到 text 会串行。
  const [snap, setSnap] = useState<{ text: string; caret: number } | null>(null)
  const [cursor, setCursor] = useState(0)
  /** 刚被替换掉的那一段（绝对下标）：文本没再变之前不重新判定它。
   *  选中后浏览器常把光标停在名字右侧——那正是刚才那个 @ 的位置，不压住的话
   *  下一次同步会把同一段重新判成触发态，菜单立刻又弹回来。 */
  const dismissed = useRef<{ text: string; start: number; end: number } | null>(null)

  /** 刚替换掉的那一段里的光标位置（就是「刚选完」的痕迹）：与文本一起变了才算真的翻篇。 */
  const dismissedCaret = (snap && dismissed.current?.text === snap.text) ? dismissed.current : null

  const trigger: MentionTrigger | null = useMemo(
    () => (snap ? detectMention(snap.text, snap.caret) : null),
    [snap],
  )
  const matches = useMemo(
    () => (trigger ? filterCandidates(candidates, trigger.query) : []),
    [candidates, trigger],
  )

  // 刚选完那一下不该立刻再弹：光标停在名字右侧时，同一个 @ 会被重新判成触发态
  // （query 成了名字本身），菜单马上又冒出来。要接着打字才算翻篇——
  // 那种情况下文本已变、dismissed 自动失效，菜单照常出现。
  const open = !!trigger && matches.length > 0
  const confirmedOpen = open && !(dismissedCaret && trigger?.at === dismissedCaret.start)
  // 换了一段触发（改了查询词、或换了个 @）就把高亮拉回第一条；
  // 只是点了别处再点回来（触发段没变）时不重置——否则鼠标一动高亮就跳。
  const seg = trigger ? `${trigger.from}:${trigger.to}` : null
  useEffect(() => { setCursor(0) }, [seg])

  const sync = useCallback((el: { value: string; selectionStart: number | null }) => {
    const text = el.value
    const caret = el.selectionStart ?? text.length
    const gone = dismissed.current
    if (gone && gone.text === text && caret >= gone.start && caret <= gone.end) return
    dismissed.current = null
    setSnap({ text, caret })
  }, [])

  const pick = useCallback((i: number, text: string) => {
    const hit = matches[i]
    if (!hit || !trigger) return
    // 名字后面补不补空格交给纯函数：中文不补，拉丁补，免得「@Sarah走了」粘成一个词。
    const next = completeMention(text, trigger, hit.label)
    // 顺序要紧：onPick 会同步回调进 sync（调用方要把光标放回去就会调它），
    // 所以 dismissed 必须在 onPick **之前**写好，否则那一次同步读到的是 null，
    // 菜单当场又弹回来。
    dismissed.current = { text: next.text, start: trigger.from, end: next.caret }
    onPick(next)
    setSnap({ text: next.text, caret: next.caret })
  }, [matches, onPick, trigger])

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>, text: string): boolean => {
    // 中文输入法组字中：Enter 是选字，不是"确认这一项"。textarea 那边有同样的守卫。
    if (e.nativeEvent.isComposing || e.keyCode === 229) return false
    if (!confirmedOpen) return false
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => (c + 1) % matches.length); return true }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => (c - 1 + matches.length) % matches.length); return true }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(cursor, text); return true }
    // 空格不当确认键：它是输入法的候选翻页键。Esc 只是收起菜单、不改文本——
    // 想把 @ 当普通字符打进去，接着写就是了（触发段一旦出现空格/换行自然失效）。
    if (e.key === 'Escape') { e.preventDefault(); setSnap(null); return true }
    return false
  }, [matches.length, confirmedOpen, pick, cursor])

  const palette = confirmedOpen && trigger ? (
    <MentionPalette
      items={matches.map((m) => ({ ...m, cls: KIND_CLASS[m.kind], icon: icons[m.kind] }))}
      cursor={cursor}
      onHover={setCursor}
      onPick={(i) => pick(i, snap?.text ?? '')}
    />
  ) : null

  return { palette, sync, onKeyDown }
}
