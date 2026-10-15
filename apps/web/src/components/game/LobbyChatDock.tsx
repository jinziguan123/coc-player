import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { GiTalk } from 'react-icons/gi'

export interface ChatLine { id: string; name: string; content: string }

interface Props {
  lines: ChatLine[]
  /** 历史是否已拉到：拉到之前不计未读，免得进房就把历史全算成未读。缺省视为已拉到。 */
  loaded?: boolean
  /** 「某某 正在输入…」整句（自己不算）；空串表示无人。 */
  typingLabel: string
  /** 没入座的人只能看，不能说。 */
  canSpeak: boolean
  onSend: (text: string) => void
  /** 每次输入都会调，传入当前全文；节流由调用方负责。 */
  onTyping: (text: string) => void
  /** 右栏（角色卡预览）开着时往左让一个栏宽，别把人家盖住。 */
  shifted?: boolean
  /** 选人舞台等专注模式：默认收成圆形浮标，点开后以浮层盖在上面（不挤占版面），离开该模式即收起。 */
  compact?: boolean
}

const OPEN_KEY = 'trpg.lobbyChat.open'
/** 与后端 event_protocol.MAX_OOC_CHARS 对齐。 */
const MAX_OOC_CHARS = 500
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
 * 现在它是**浮层**（absolute 定位，右侧垂直居中）：收起是一颗圆形 token，展开是一张
 * 悬浮聊天卡。高度写死在卡片上、日志内部滚动，所以它**完全不参与页面高度**，也不再
 * 占掉主栏一整条宽度——大厅真正要办的事（配座位、开局）拿回全部版面。
 */
export function LobbyChatDock({
  lines, loaded = true, typingLabel, canSpeak, onSend, onTyping, shifted, compact,
}: Props) {
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) !== '0')
  /** 专注模式下临时展开（不写进 OPEN_KEY，退出专注模式即收起）。 */
  const [peek, setPeek] = useState(false)
  /** 聊天卡此刻是否真的看得见：未读与贴底都以它为准。 */
  const visible = compact ? peek : open
  const [draft, setDraft] = useState('')
  /** 收起期间新增的条数；展开即清零。 */
  const [unread, setUnread] = useState(0)
  /** 用户往上翻历史时不要把他拽回底部——只在原本就贴着底时才自动跟随。 */
  const [pinned, setPinned] = useState(true)
  const logRef = useRef<HTMLDivElement>(null)
  /** 最后看过的那条的 id：undefined = 还没定基线，null = 定基线时一条都没有。按 id 而不是条数记，
   *  记录封顶 200 条后条数不再增长，按条数算未读会永远停在 0。 */
  const seenId = useRef<string | null | undefined>(undefined)

  useEffect(() => { localStorage.setItem(OPEN_KEY, open ? '1' : '0') }, [open])
  useEffect(() => { if (!compact) setPeek(false) }, [compact])

  // 看得见时基线一路跟到最新一条，未读恒为 0；收起后基线冻在那一刻，之后的条数就是未读。
  // 历史拉到之前不定基线；拉到那一刻若是收着的，历史本身记为已读。
  useEffect(() => {
    if (!loaded) return
    const lastId = lines.length ? lines[lines.length - 1].id : null
    if (visible || seenId.current === undefined) { seenId.current = lastId; setUnread(0); return }
    const idx = seenId.current == null ? -1 : lines.findIndex((l) => l.id === seenId.current)
    setUnread(idx < 0 ? lines.length : lines.length - 1 - idx)
  }, [visible, lines, loaded])

  // useLayoutEffect：要在浏览器绘制之前贴底，否则新消息会先闪一下再跳。
  useLayoutEffect(() => {
    if (!visible || !pinned) return
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines.length, typingLabel, visible, pinned])

  const onScroll = () => {
    const el = logRef.current
    if (!el) return
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLACK)
  }

  const send = () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')
    onTyping('')   // 让节流器知道输入框清空了，下一句重新算「变长」
    setPinned(true)   // 自己说话必然要看到自己那句
    onSend(text)
  }

  const show = (v: boolean) => (compact ? setPeek(v) : setOpen(v))

  if (!visible) {
    return (
      <button
        onClick={() => show(true)}
        className={`chat-float chat-token${shifted ? ' chat-float--shifted' : ''}`}
        title="展开大厅聊天"
        aria-label="展开大厅聊天"
      >
        <GiTalk size={20} aria-hidden="true" />
        {unread > 0 && <span className="chat-token-dot">{unread > 99 ? '99+' : unread}</span>}
      </button>
    )
  }

  return (
    // 专注模式下是盖在上面的浮层（chat-dock--overlay），不触发主栏让位
    <aside className={`chat-float chat-dock${shifted ? ' chat-float--shifted' : ''}${compact ? ' chat-dock--overlay' : ''}`}>
      <div className="chat-dock-head">
        <GiTalk size={14} aria-hidden="true" />
        <span className="flex-1">大厅聊天</span>
        <button
          onClick={() => show(false)}
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

      <div className="chat-dock-typing">{typingLabel}</div>

      <div className="chat-dock-send">
        <input
          value={draft}
          onChange={(e) => { setDraft(e.target.value); onTyping(e.target.value) }}
          onKeyDown={(e) => {
            // 输入法合成中（中文选词/确认）的回车不当作发送
            if (e.nativeEvent.isComposing) return
            if (e.key === 'Enter') { e.preventDefault(); send() }
          }}
          placeholder={canSpeak ? '说点什么…' : '加入房间后可发言'}
          disabled={!canSpeak}
          maxLength={MAX_OOC_CHARS}
          className="input flex-1 min-w-0 !py-1 text-sm"
        />
        <button onClick={send} disabled={!canSpeak || !draft.trim()} className="btn-primary !px-2.5 !py-1 text-sm">
          发送
        </button>
      </div>
    </aside>
  )
}
