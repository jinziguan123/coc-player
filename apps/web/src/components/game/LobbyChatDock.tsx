import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { GiTalk } from 'react-icons/gi'

export interface ChatLine { id: string; name: string; content: string }

interface Props {
  lines: ChatLine[]
  /** 谁在输入（自己不算）；空串表示无人。 */
  typingName: string
  /** 没入座的人只能看，不能说。 */
  canSpeak: boolean
  onSend: (text: string) => void
  /** 每次敲键都会调；节流由调用方负责。 */
  onTyping: () => void
}

const OPEN_KEY = 'trpg.lobbyChat.open'
/** 判定「已经贴在底部」的容差：差这点像素也算贴着，否则滚动条的亚像素误差会让自动贴底失效。 */
const STICK_SLACK = 24

/**
 * 大厅聊天：右侧可收起的竖直面板。
 *
 * 从前它是主栏里的一张卡，卡上 `minHeight:160` 和日志区 `flex-1 + overflow-auto`
 * 互相打架——按 flex 规范，overflow 非 visible 的项自动最小尺寸为 0，于是日志在固有
 * 尺寸计算里贡献 0，卡片停在 160px，日志只分到 29px（实测 25 条消息内容 560px、
 * 可视 29px），那条 `maxHeight:200` 从头到尾没生效过。
 *
 * 挪到右侧后高度由外层 `h-full` 给定，日志天然定高滚动，**不再参与页面高度**：
 * 聊多久大厅都不会被顶长。
 */
export function LobbyChatDock({ lines, typingName, canSpeak, onSend, onTyping }: Props) {
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) !== '0')
  const [draft, setDraft] = useState('')
  /** 收起期间新增的条数；展开即清零。 */
  const [unread, setUnread] = useState(0)
  /** 用户往上翻历史时不要把他拽回底部——只在原本就贴着底时才自动跟随。 */
  const [pinned, setPinned] = useState(true)
  const logRef = useRef<HTMLDivElement>(null)
  const seen = useRef(lines.length)

  useEffect(() => { localStorage.setItem(OPEN_KEY, open ? '1' : '0') }, [open])

  // 展开时 seen 一路跟着长度走，未读恒为 0；收起后 seen 冻在那一刻，差值就是未读。
  useEffect(() => {
    if (open) { seen.current = lines.length; setUnread(0) }
    else setUnread(Math.max(0, lines.length - seen.current))
  }, [open, lines.length])

  // useLayoutEffect：要在浏览器绘制之前贴底，否则新消息会先闪一下再跳。
  useLayoutEffect(() => {
    if (!open || !pinned) return
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines.length, typingName, open, pinned])

  const onScroll = () => {
    const el = logRef.current
    if (!el) return
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLACK)
  }

  const send = () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')
    setPinned(true)   // 自己说话必然要看到自己那句
    onSend(text)
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="chat-rail"
        title="展开大厅聊天"
        aria-label="展开大厅聊天"
      >
        <GiTalk size={16} aria-hidden="true" />
        <span className="chat-rail-label">大厅聊天</span>
        {unread > 0 && <span className="chat-rail-dot">{unread > 99 ? '99+' : unread}</span>}
      </button>
    )
  }

  return (
    <aside className="chat-dock">
      <div className="chat-dock-head">
        <GiTalk size={14} aria-hidden="true" />
        <span className="flex-1">大厅聊天</span>
        <button
          onClick={() => setOpen(false)}
          className="btn-secondary !px-1.5 !py-0.5"
          title="收起"
          aria-label="收起大厅聊天"
        >
          <ChevronRight size={13} />
        </button>
      </div>

      <div ref={logRef} onScroll={onScroll} className="chat-dock-log">
        {lines.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            还没有人发言。开局前可以在这里商量分工。
          </p>
        ) : lines.map((m) => (
          <div key={m.id} className="text-sm leading-relaxed">
            <span className="font-semibold" style={{ color: 'var(--color-text-accent)' }}>{m.name}：</span>
            <span style={{ wordBreak: 'break-word' }}>{m.content}</span>
          </div>
        ))}
      </div>

      {/* 翻历史时新消息不再抢滚动条，改成一条可点的提示 */}
      {!pinned && (
        <button
          onClick={() => { setPinned(true) }}
          className="chat-dock-jump"
        >回到最新</button>
      )}

      <div className="chat-dock-typing">{typingName ? `${typingName} 正在输入…` : ''}</div>

      <div className="chat-dock-send">
        <input
          value={draft}
          onChange={(e) => { setDraft(e.target.value); onTyping() }}
          onKeyDown={(e) => {
            // 输入法合成中（中文选词/确认）的回车不当作发送
            if (e.nativeEvent.isComposing) return
            if (e.key === 'Enter') { e.preventDefault(); send() }
          }}
          placeholder={canSpeak ? '说点什么…' : '加入房间后可发言'}
          disabled={!canSpeak}
          className="input flex-1 min-w-0 !py-1 text-sm"
        />
        <button onClick={send} disabled={!canSpeak || !draft.trim()} className="btn-primary !px-2.5 !py-1 text-sm">
          发送
        </button>
      </div>
    </aside>
  )
}
