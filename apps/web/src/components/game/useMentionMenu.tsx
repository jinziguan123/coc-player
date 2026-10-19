/**
 * 输入框里的 @ 补全：光标处的触发、两级菜单、键盘选择与插入。
 *
 * 两级的分法：打 @ 先给「队友 / 已知地点 / 技能」三个入口，进去再看具体是谁、哪儿、哪一项。
 * 一屏几十条名字混着排的话，找「技能」里的「侦查」要在一堆人名和地名里翻；先分组再选，
 * 每级都只有几条。**但直接打字就跳过一级**——"我知道我要提谁"时不该逼他先点一次分组。
 *
 * 与 useSlashCommands（KP 的 / 命令面板）刻意不复用同一套：斜杠的判定是「整串以 / 开头」，
 * 这里是「@ 前一个字符不是词内字符」——两者只有"弹一个列表"这一层像，硬合成一个 hook 会得到
 * 一个谁都不好用的中间态。真正共用的部分（触发段解析、插入与光标）已经沉在 mentionTrigger.ts。
 *
 * 只在**玩家发言框**里用：KP 走写作台，他要的是 /动作 而不是 @某人。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import { completeMention, detectMention, type MentionTrigger } from './mentionTrigger'
import {
  candidatesOfKind, countByKind, filterCandidates, KIND_LABELS,
  type MentionCandidate, type MentionKind,
} from './mentionSources'
import { MentionPalette } from './MentionPalette'

/** 菜单本体与三个事件处理器。返回值里的成员都是稳定的 useCallback：
 *  调用方可以把它们直接放进 useEffect 依赖，不必再包一层。 */
export interface MentionMenu {
  /** 菜单本体；一级没有分组、二级没有命中时为 null（不占位）。 */
  palette: ReactNode | null
  /** 文本或光标变了就调它——菜单是否开着、候选是什么，全由这里算。
   *  只要求 value/selectionStart 两个字段，传原生事件目标或造的对象都行。 */
  sync: (el: { value: string; selectionStart: number | null }) => void
  /** 返回 true 表示这一下按键已被菜单消费，调用方不要再处理（如别再当成"发送"）。 */
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>, text: string) => boolean
  /** 收起菜单（点了别处时用）。不碰文本，只收起。 */
  close: () => void
}

/** 图标由调用方注入（react-icons/gi 的 Gi* 组件），这个文件不引图标库——
 *  与项目「图标不进文本、由前端按语义渲染」的约定一致。 */
export interface MentionIcons {
  character: ReactNode
  location: ReactNode
  skill: ReactNode
}

export const KIND_CLASS: Record<MentionKind, string> = {
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
  /** 一级选了哪一组；null = 还在一级（分组入口）。 */
  const [scope, setScope] = useState<MentionKind | null>(null)
  /** 从二级返回时回到原来那一组：来回翻不会丢位置。 */
  const groupCursor = useRef(0)
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
  const query = trigger?.query ?? ''
  const groups = useMemo(() => countByKind(candidates), [candidates])
  // 二级：只看这一组。组内没命中就退回全局找——比「菜单直接消失、Enter 反而把没写完的
  // 那句话发出去」好。退回时头部会写明是全局结果，不是装作还在组里。
  const scoped = useMemo(
    () => (scope ? candidatesOfKind(candidates, scope) : candidates),
    [candidates, scope],
  )
  const scopedMatches = useMemo(() => filterCandidates(scoped, query), [scoped, query])
  const fellBack = scope != null && query !== '' && scopedMatches.length === 0
  const matches = fellBack ? filterCandidates(candidates, query) : scopedMatches

  /** 一级（分组）还是二级（具体值）：打了字就直接进二级——"我知道我要提谁"。 */
  const mode: 'groups' | 'items' = query !== '' || scope != null ? 'items' : 'groups'
  const rows = mode === 'groups' ? groups.length : matches.length

  // 刚选完那一下不该立刻再弹：光标停在名字右侧时，同一个 @ 会被重新判成触发态
  // （query 成了名字本身），菜单马上又冒出来。要接着打字才算翻篇——
  // 那种情况下文本已变、dismissed 自动失效，菜单照常出现。
  const confirmedOpen = rows > 0 && !!trigger && !(dismissedCaret && trigger.at === dismissedCaret.start)
  // 换了一个 @（at 变了）就从头开始：分组回到一级、高亮回到第一条。
  const at = trigger?.at
  useEffect(() => {
    setScope(null)
    setCursor(0)
    groupCursor.current = 0
  }, [at])
  // 改了查询词就把高亮拉回第一条。**只看查询词，不看层级**：层级切换由各自的动作
  // 显式设光标（进组回到第一条、退组停回原来那一组），在这里再加一次复位会把后者冲掉。
  // 只是点了别处再点回来时不重置——否则鼠标一动高亮就跳。
  const typed = trigger ? `${trigger.at}:${trigger.query}` : null
  useEffect(() => { setCursor(0) }, [typed])

  const sync = useCallback((el: { value: string; selectionStart: number | null }) => {
    const text = el.value
    const caret = el.selectionStart ?? text.length
    const gone = dismissed.current
    if (gone && gone.text === text && caret >= gone.start && caret <= gone.end) return
    dismissed.current = null
    setSnap({ text, caret })
  }, [])

  const close = useCallback(() => {
    dismissed.current = null
    setScope(null)
    setSnap(null)
  }, [])

  const enterGroup = useCallback((i: number) => {
    const hit = groups[i]
    if (!hit) return
    groupCursor.current = i
    setScope(hit.kind)
    setCursor(0)          // 二级从第一条开始
  }, [groups])

  /** 退回一级。已经把查询词打进去了就退回不去（那一段文字还在，判定会立刻回到二级），
   *  这时按 Esc 的意图其实是"别烦我"，直接收起。 */
  const backOrClose = useCallback(() => {
    if (scope != null && query === '') {
      setScope(null)
      setCursor(groupCursor.current)
      return
    }
    close()
  }, [close, query, scope])

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
    setScope(null)
  }, [matches, onPick, trigger])

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>, text: string): boolean => {
    // 中文输入法组字中：Enter 是选字，不是"确认这一项"。textarea 那边有同样的守卫。
    if (e.nativeEvent.isComposing || e.keyCode === 229) return false
    if (!confirmedOpen) return false
    const move = (delta: number) => {
      e.preventDefault()
      setCursor((c) => (c + delta + rows) % rows)
    }
    if (e.key === 'ArrowDown') { move(1); return true }
    if (e.key === 'ArrowUp') { move(-1); return true }
    if (mode === 'groups') {
      // 一级：确认＝进这一组。→ 与 Enter/Tab 同义——列表里"右边有东西"是通用暗示。
      if (e.key === 'Enter' || e.key === 'Tab' || e.key === 'ArrowRight') {
        e.preventDefault()
        enterGroup(cursor)
        return true
      }
      if (e.key === 'Escape') { e.preventDefault(); close(); return true }
      return false
    }
    // 二级：确认＝插入这一项；← / Esc 退回一级（查询词还在时只收起）。
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(cursor, text); return true }
    if (e.key === 'ArrowLeft' || e.key === 'Escape') { e.preventDefault(); backOrClose(); return true }
    // 空格不当确认键：它是输入法的候选翻页键。
    return false
  }, [backOrClose, close, confirmedOpen, cursor, enterGroup, mode, pick, rows])

  const palette = confirmedOpen && trigger ? (
    mode === 'groups' ? (
      <MentionPalette
        mode="groups"
        groups={groups.map((g) => ({
          key: g.kind,
          label: KIND_LABELS[g.kind],
          count: g.count,
          cls: KIND_CLASS[g.kind],
          icon: icons[g.kind],
        }))}
        cursor={cursor}
        onHover={setCursor}
        onPick={enterGroup}
      />
    ) : (
      <MentionPalette
        mode="items"
        items={matches.map((m) => ({ ...m, cls: KIND_CLASS[m.kind], icon: icons[m.kind] }))}
        cursor={cursor}
        // 组内搜索退到全局时说清楚，别让玩家以为"技能里没有艾琳"是数据问题。
        scopeLabel={fellBack ? '全部' : scope ? KIND_LABELS[scope] : undefined}
        onHover={setCursor}
        onPick={(i) => pick(i, snap?.text ?? '')}
      />
    )
  ) : null

  return { palette, sync, onKeyDown, close }
}
