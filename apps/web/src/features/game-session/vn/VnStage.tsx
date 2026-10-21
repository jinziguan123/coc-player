import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, m, useReducedMotion } from 'motion/react'
import { ChevronDown, X } from 'lucide-react'
import { GiFastForwardButton, GiPauseButton, GiPlayButton, GiScrollUnfurled } from 'react-icons/gi'
import type { ChatMessage } from '@/stores/sessionStore'
import { absoluteImageUrl } from '@/lib/imageUrl'
import { spring } from '@/lib/motion'
import { buildStops, dwellMs, paginate, speakerOf, stopKey, type Stop } from './vnQueue'

/** 打字机速度：每字毫秒数 */
const TYPE_MS = 28

interface Cursor { key: string; idx: number; page: number }

/**
 * 对话框模式（视觉小说式）的舞台：场景图清晰铺满（由页面的氛围底层负责，这里透明），
 * 文字在底部对话框里一段一段读；检定、掷骰、手书这类要看要点的，作为卡片浮在对话框上方。
 *
 * 读的节奏归玩家：点击 / 空格 / 回车推进，← 回退一页；「自动」按字数停留后自己翻。
 * 新消息不会把人从正在读的地方拽走——已读到最新时自动接上，否则只在角上记「还有 N 段」。
 * 往回翻整段对话走「历史」，用的是聊天流同一套消息渲染，编辑暂存发言等操作都还在。
 */
export function VnStage({ messages, renderMessage, pendingCheckIds, luck, tail, streaming }: {
  /** 已滤掉战斗机械结算行的消息流 */
  messages: ChatMessage[]
  /** 聊天流里那一行的渲染（卡片与历史都用它，交互与聊天流一致） */
  renderMessage: (msg: ChatMessage) => ReactNode
  /** 仍待掷的检定请求 id：读过了也一直挂在对话框上方，直到掷完 */
  pendingCheckIds: Set<string>
  /** 幸运询价卡：要本人拍板，挂着时自动播放停住 */
  luck?: ReactNode
  /** 生成中的提示行（思考提示、秒表、打断按钮）：读到最新时放在对话框底栏 */
  tail?: ReactNode
  streaming: boolean
}) {
  const reduced = useReducedMotion()
  const stops = useMemo(() => buildStops(messages), [messages])
  const [cursor, setCursor] = useState<Cursor | null>(null)
  const [shown, setShown] = useState(0)
  const [auto, setAuto] = useState(false)
  const [history, setHistory] = useState(false)

  // 当前位置：按键找；键没了（流式消息落库换了 id）就按原序号，夹在范围内
  const idx = useMemo(() => {
    if (!stops.length) return -1
    if (!cursor) return stops.length - 1
    const found = stops.findIndex((s) => stopKey(s.msg) === cursor.key)
    return found >= 0 ? found : Math.min(cursor.idx, stops.length - 1)
  }, [cursor, stops])
  const stop: Stop | undefined = stops[idx]
  // 卡片站时对话框保留上一段文字（读到哪儿的上下文不断），卡片浮在上面
  const lineIdx = useMemo(() => {
    for (let i = idx; i >= 0; i--) if (stops[i].kind === 'line') return i
    return -1
  }, [idx, stops])
  const line = lineIdx >= 0 ? stops[lineIdx] : undefined
  const pages = useMemo(() => paginate(line?.text ?? ''), [line?.text])
  const onLine = stop?.kind === 'line'
  const page = onLine ? Math.min(cursor?.page ?? 0, pages.length - 1) : pages.length - 1
  const pageText = pages[page] ?? ''
  const revealed = !onLine || shown >= pageText.length
  const atEnd = idx >= stops.length - 1 && (!onLine || page >= pages.length - 1)

  const goTo = useCallback((i: number, p = 0) => {
    const s = stops[i]
    if (s) setCursor({ key: stopKey(s.msg), idx: i, page: p })
  }, [stops])

  // 换页从头打字；减弱动效时直接整页出现
  const pageId = `${stop ? stopKey(stop.msg) : ''}#${page}`
  useLayoutEffect(() => { setShown(reduced ? Number.MAX_SAFE_INTEGER : 0) }, [pageId, reduced])
  useEffect(() => {
    if (shown >= pageText.length) return
    const t = window.setTimeout(() => setShown((n) => n + 1), TYPE_MS)
    return () => window.clearTimeout(t)
  }, [shown, pageText.length])

  const advance = useCallback(() => {
    if (!stop) return
    if (!revealed) { setShown(Number.MAX_SAFE_INTEGER); return }
    if (onLine && page < pages.length - 1) goTo(idx, page + 1)
    else if (idx < stops.length - 1) goTo(idx + 1)
  }, [goTo, idx, onLine, page, pages.length, revealed, stop, stops.length])

  const back = useCallback(() => {
    if (onLine && page > 0) { goTo(idx, page - 1); return }
    if (idx > 0) {
      const prev = stops[idx - 1]
      goTo(idx - 1, prev.kind === 'line' ? paginate(prev.text).length - 1 : 0)
    }
  }, [goTo, idx, onLine, page, stops])

  // 已读到最新时，新到的一段直接接上（含刚开始流式输出的叙事）；没读完的不打扰
  const caughtUp = useRef(true)
  const prevLen = useRef(stops.length)
  useEffect(() => {
    if (!cursor && stops.length) goTo(stops.length - 1)
    else if (stops.length > prevLen.current && caughtUp.current) goTo(prevLen.current)
    prevLen.current = stops.length
  }, [stops.length, cursor, goTo])
  useEffect(() => { caughtUp.current = atEnd && revealed }, [atEnd, revealed])

  // 自动播放：读完这一页按字数停一会儿再翻。待掷的检定、幸运询价要人拍板，停住不翻
  const blocked = (!!stop && pendingCheckIds.has(String(stop.msg.metadata?.id ?? ''))) || !!luck
  useEffect(() => {
    if (!auto || !revealed || atEnd || blocked || history) return
    const t = window.setTimeout(advance, stop?.kind === 'card' ? 2200 : dwellMs(pageText))
    return () => window.clearTimeout(t)
  }, [auto, revealed, atEnd, blocked, history, advance, stop?.kind, pageText])

  // 键盘：空格 / 回车 / → 推进，← 回退，Esc 关历史；输入框里打字时不抢
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return
      if (history) { if (e.key === 'Escape') setHistory(false); return }
      // 焦点在按钮上时空格 / 回车归按钮自己（比如刚点过「自动」），方向键照常翻页
      const onButton = el?.tagName === 'BUTTON'
      if (e.key === 'ArrowRight' || (!onButton && (e.key === ' ' || e.key === 'Enter'))) { e.preventDefault(); advance() }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); back() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [advance, back, history])

  // 读过但还没掷的检定：一直挂着，直到掷完
  const sticky = stops.filter((s, i) =>
    i < idx && s.kind === 'card' && pendingCheckIds.has(String(s.msg.metadata?.id ?? '')))
  const remaining = Math.max(0, stops.length - 1 - idx) + (onLine ? pages.length - 1 - page : 0)
  const speaker = line ? speakerOf(line.msg) : null
  const portrait = speaker?.portrait ? absoluteImageUrl(speaker.portrait) : ''
  const liveTail = streaming && atEnd

  return (
    <div className="vn-stage">
      {/* 空白处点一下也算推进：视觉小说的老习惯 */}
      <div className="vn-hit" onClick={advance} aria-hidden />

      <div className="vn-float">
        <AnimatePresence initial={false}>
          {sticky.map((s) => (
            <m.div key={`sticky-${stopKey(s.msg)}`} className="vn-card vn-card--sticky"
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={spring.soft}>
              {renderMessage(s.msg)}
            </m.div>
          ))}
          {stop?.kind === 'card' && (
            <m.div key={`card-${stopKey(stop.msg)}`} className="vn-card"
              initial={{ opacity: 0, y: 14, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, transition: { duration: 0.15 } }} transition={spring.pop}>
              {renderMessage(stop.msg)}
            </m.div>
          )}
        </AnimatePresence>
        {luck && <div className="vn-card">{luck}</div>}
      </div>

      <div className={`vn-box ${speaker ? `vn-box--${speaker.kind}` : ''}`} onClick={advance}
        role="region" aria-label="对话框" aria-live="polite">
        {portrait && (
          <m.img key={portrait} src={portrait} alt="" className="vn-portrait"
            initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={spring.soft} />
        )}
        <div className="vn-box-main">
          {speaker?.name && <div className="vn-nameplate">{speaker.name}</div>}
          <p className="vn-text">
            {line ? pageText.slice(0, shown) : <span className="vn-empty">故事还没开始。</span>}
            {line && !revealed && <span className="vn-caret" aria-hidden />}
          </p>
          <div className="vn-meta" onClick={(e) => e.stopPropagation()}>
            {liveTail && (tail || <span className="vn-status"><span className="dot-pulse" /> KP 正在书写</span>)}
            {!liveTail && remaining > 0 && <span className="vn-status">还有 {remaining} 段</span>}
            {revealed && !atEnd && <ChevronDown size={15} className="vn-next" aria-hidden />}
            <span className="vn-tools">
              <button type="button" className={`vn-tool ${auto ? 'is-on' : ''}`} aria-pressed={auto}
                onClick={() => setAuto((v) => !v)} title={auto ? '停止自动播放' : '自动播放：读完一页自己翻'}>
                {auto ? <GiPauseButton size={13} /> : <GiPlayButton size={13} />}<span>自动</span>
              </button>
              <button type="button" className="vn-tool" disabled={atEnd}
                onClick={() => goTo(stops.length - 1)} title="跳到最新一段">
                <GiFastForwardButton size={13} /><span>最新</span>
              </button>
              <button type="button" className="vn-tool" onClick={() => setHistory(true)} title="历史记录（Esc 关闭）">
                <GiScrollUnfurled size={13} /><span>历史</span>
              </button>
            </span>
          </div>
        </div>
      </div>

      <AnimatePresence>
        {history && (
          <VnHistory messages={messages} stops={stops} renderMessage={renderMessage} currentKey={stop ? stopKey(stop.msg) : ''}
            onJump={(i) => { goTo(i); setHistory(false) }} onClose={() => setHistory(false)} />
        )}
      </AnimatePresence>
    </div>
  )
}

/** 历史记录：完整的聊天流，可从任意一段接着读 */
function VnHistory({ messages, stops, renderMessage, currentKey, onJump, onClose }: {
  messages: ChatMessage[]
  stops: Stop[]
  renderMessage: (msg: ChatMessage) => ReactNode
  currentKey: string
  onJump: (stopIdx: number) => void
  onClose: () => void
}) {
  const listRef = useRef<HTMLDivElement>(null)
  const stopIdx = useMemo(() => new Map(stops.map((s, i) => [stopKey(s.msg), i])), [stops])
  // 打开时定位到正在读的那段（而不是总在最底下）
  useLayoutEffect(() => {
    const el = listRef.current?.querySelector('[data-vn-current]') as HTMLElement | null
    if (el) el.scrollIntoView({ block: 'center' })
    else if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [])
  return (
    <m.div className="vn-history" role="dialog" aria-label="历史记录"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.15 } }}>
      <m.div className="vn-history-panel" initial={{ y: 24, opacity: 0.6 }} animate={{ y: 0, opacity: 1 }} transition={spring.soft}>
        <header className="vn-history-head">
          <GiScrollUnfurled aria-hidden /> <b>历史记录</b>
          <span className="vn-history-hint">点「从这里读」回到对话框</span>
          <button type="button" className="vn-tool" onClick={onClose} title="关闭（Esc）" aria-label="关闭"><X size={15} /></button>
        </header>
        <div ref={listRef} className="vn-history-list chat-scroll game-info">
          {messages.map((msg) => {
            const key = stopKey(msg)
            const i = stopIdx.get(key)
            return (
              <div key={key} className="vn-history-row" data-vn-current={key === currentKey ? '' : undefined}>
                {renderMessage(msg)}
                {i !== undefined && (
                  <button type="button" className="vn-history-jump" onClick={() => onJump(i)}>从这里读</button>
                )}
              </div>
            )
          })}
        </div>
      </m.div>
    </m.div>
  )
}
