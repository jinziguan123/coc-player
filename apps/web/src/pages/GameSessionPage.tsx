import { useEffect, useLayoutEffect, useState, useRef, useCallback, useMemo, type CSSProperties } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { toast } from 'sonner'
import { api, connectSSE, getServerUrl } from '../api/client'
import { runLiveSession } from '@/lib/liveSession'
import { HistorySearchModal } from '../components/game/HistorySearchModal'
import { getSceneBackdropEnabled } from '@/lib/theme'
import {
  buildPartyByName,
  fmtTime,
  isSoloTable,
  npcHue,
  sceneBackdropOf,
  resolveActorKind,
  selectCombatLog,
  selectCombatResult,
  splitOOC,
  stripCommandTags,
} from '@/features/game-session/derive'
import {
  assertAllLogHandled,
  assertAllNonLogHandled,
  categoryOf,
  type LogEventType,
  type NonLogEventType,
  type RoomEventType,
} from '@/lib/roomEvents'
import { useSessionStore, type ChatMessage } from '../stores/sessionStore'
import { CharacterPanel } from '../components/character/CharacterPanel'
import { PartyRoster } from '../components/game/PartyRoster'
import { SeatIcon, type SeatKind } from '../components/game/SeatIcon'
import { DiceRoller, type DiceRollerHandle, type DiceSpec } from '../components/game/DiceRoller'
import { diceSpecsOf } from '../components/game/diceNotation'
import {
  CheckResultCard, hasCheckReadout, diceAccent,
  type CheckResultMeta,
} from '../components/game/CheckResultCard'
import { OnboardingCoach, hasSeenCoach } from '../components/game/OnboardingCoach'
import { buildCheckCaption } from '../components/game/diceNotation'
import { normalizeOpposedData } from '../components/game/opposedDice'
import { BurstCard, OpposedCard, type BurstData } from '../components/game/DiceContestCards'
import { ContextUsageBadge } from '../components/game/ContextUsageBadge'
import { RecapModal } from '../components/game/RecapModal'
import { GrowthModal } from '../components/game/GrowthModal'
import { InvestigationBoard } from '../components/game/InvestigationBoard'
import { HexSandbox } from '../components/game/HexSandbox'
import { ImprovisedNpcModal } from '../components/game/ImprovisedNpcModal'
import { loadHandledSuggestions, markSuggestionHandled } from '../lib/travelSuggest'
import { SessionStyleModal } from '../components/game/SessionStyleModal'
import { CombatStage, type CombatState, type PendingReaction, type CombatLogEntry, type CombatResultView } from '../components/game/CombatStage'
import { ChasePanel, type ChaseState } from '../components/game/ChasePanel'
import { HumanKpPanel } from '../components/game/HumanKpPanel'
import { Modal } from '../components/ui/modal'
import { GiReturnArrow, GiRollingDices, GiScrollUnfurled, GiTreasureMap, GiEnvelope, GiNewspaper, GiNotebook, GiPapers, GiUpgrade, GiCharacter, GiCrossedSwords, GiAncientRuins, GiMagnifyingGlass, GiQuillInk, GiTalk, GiTheaterCurtains } from 'react-icons/gi'
import { Copy, Bot, RotateCcw, Search, X, PanelRightOpen, PanelRightClose, HelpCircle, ChevronDown, Pencil, Trash2, Hexagon, Eye, EyeOff } from 'lucide-react'
import { ConfirmDialog } from '../components/ui/confirm-dialog'
import { parseChaseState, parseCombatState, parsePendingReaction } from '../lib/liveState'
import { useRepairableImage, type ModuleImageKind } from '../components/module/ModuleImage'
import { useSyncBackOnVisit } from '../features/characters/useSyncBack'

interface KnownLocation {
  id: string; name: string; current: boolean; visited: boolean
  connections?: string[]; party?: string[]
  clues?: { id: string; name: string; status: string }[]
  // 沙盘坐标与地貌。parent＝这一格属于哪个子沙盘（空＝顶层）；各层坐标空间彼此独立，
  // 丢了它，子层的格子会拿着子层坐标画进顶层，两层直接叠在一起。
  map?: { q: number; r: number; biome: string; parent?: string } | null
  known?: boolean                                        // KP 上帝视角下玩家是否已知
  image?: string                                         // 场景配图（氛围底的色调来源）
  sceneId?: string
  nodeKind?: 'scene' | 'terrain'
}
interface MapNodePayload {
  id: string; q: number; r: number; biome: string; scene_id?: string | null; parent?: string
}

// 距底多少像素以内算「已经在最新处」。留一点余量：markdown 渲染完成后高度会有零点几像素的
// 抖动，卡死 0 会让按钮在贴底时忽隐忽现。
const BOTTOM_SLACK = 120

// [MOVE]/[MAP_MARK] 地图功能已下线，但旧事件文本里可能残留标签 → 保留在剔除名单里

// KP 偶尔会在叙述里夹带 HTML 标签（如 <b>…</b>）。叙述用 ReactMarkdown 渲染但未开 rehype-raw
// （刻意不渲染 LLM 产出的原始 HTML，防 XSS），故这些标签会原样显示。这里把常见格式化标签剥掉，
// 保留标签内的正文（需要强调时 KP 应改用 markdown，如 **加粗**）。

// 手书（Handout）卡片：按 metadata.handout_kind 选矢量图标与中文标签（缺省 GiScrollUnfurled/文书）
const HANDOUT_ICONS: Record<string, typeof GiScrollUnfurled> = {
  letter: GiEnvelope,
  news: GiNewspaper,
  diary: GiNotebook,
  note: GiPapers,
}
const HANDOUT_KIND_LABELS: Record<string, string> = {
  letter: '信件',
  news: '报纸',
  diary: '日记',
  note: '便条',
}
// 信笺正文用衬线体（配合泛黄纸质感），本地打包 Noto Serif SC，中文回退宋体
const HANDOUT_SERIF = '"Noto Serif SC", Georgia, "Songti SC", "SimSun", serif'

// 手书配图（metadata.image）：加载完成淡入（尊重 prefers-reduced-motion），加载失败整体隐藏不留占位
function HandoutImage({ src }: { src: string }) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const reducedMotion = typeof window !== 'undefined'
    && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (failed) return null
  return (
    <img
      src={src}
      alt=""
      className="block w-full rounded mb-3"
      style={{
        border: '1px solid var(--color-border)',
        opacity: loaded ? 1 : 0,
        transition: reducedMotion ? undefined : 'opacity 0.6s ease',
      }}
      onLoad={() => setLoaded(true)}
      onError={() => setFailed(true)}
    />
  )
}

// 跑团事件配图/立绘：图片缺失时按事件 metadata 定位模组条目并只修复一次。
function LiveModuleImage({
  src,
  moduleId,
  kind,
  itemId,
  field,
  alt = '',
  className = '',
  onClick,
  onRegenerated,
  visualStateKey,
}: {
  src: string
  moduleId?: string
  kind?: string
  itemId?: string
  field?: string
  alt?: string
  className?: string
  onClick?: () => void
  onRegenerated?: (url: string) => void
  visualStateKey?: string
}) {
  const image = useRepairableImage({
    src,
    moduleId,
    kind: (kind || 'scene') as ModuleImageKind,
    itemId: itemId || '',
    field: (field || 'image') as 'image' | 'image_variant' | 'portrait' | 'encounter_image',
    visualStateKey,
    onRegenerated,
  })
  if (!image.imageUrl || image.status === 'failed') return null
  return (
    <img
      src={image.imageUrl}
      alt={alt}
      className={className}
      style={{ opacity: image.status === 'ready' ? 1 : 0.35, transition: 'opacity 0.4s ease' }}
      onLoad={image.onLoad}
      onError={image.onError}
      onClick={onClick}
    />
  )
}

// 配图卡（metadata.kind === 'illustration'）：按 icat 选矢量图标与类别标签
const ILLUST_ICONS: Record<string, typeof GiScrollUnfurled> = {
  scene: GiAncientRuins,
  clue: GiMagnifyingGlass,
  encounter: GiCrossedSwords,
}
const ILLUST_LABELS: Record<string, string> = {
  scene: '场景',
  clue: '线索',
  encounter: '遭遇',
  custom: 'KP 配图',
}

// NPC 气泡按角色名派生一个稳定色相（写入 --npc-hue），同一 NPC 颜色一致、不同 NPC 微有区分
// 行内 markdown：把加粗/斜体等渲染出来，但 p 退化为 span 以贴合气泡（不换行、不留段距）。
function InlineMd({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{ p: ({ children }) => <>{children}</> }}
    >
      {text}
    </ReactMarkdown>
  )
}

// diceAccent / outcomeLabel 已移到 CheckResultCard 同文件，供结果卡与对抗卡/连射卡共用。

interface EndVoteState {
  open: boolean
  voters: { character_id: string; name: string; agreed: boolean }[]
  agreed_count: number
  total: number
}

/** 连射结果卡：一轮多枪逐发列出（命中/伤害/换目标惩罚骰），整体一次性展示（不逐发 3D 骰）。 */

interface Character {
  id: string
  name: string
  module_id: string
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
}

interface ChunkPayload {
  /** 后端 room_events.py 登记的类型集合（经 OpenAPI 生成）。用具体联合而不是 string，
   *  下面的分发才能靠 switch 的 never 分支保证「新增事件必须被处理」。 */
  type: RoomEventType
  content?: string
  actor_name?: string
  actor_id?: string
  id?: string
  metadata?: Record<string, unknown>
}

/** GET /sessions/{id}/sync：sync 类状态的快照 + 事件水位线（见后端 room_sync.py）。 */
interface SyncSnapshot {
  seq: number
  generating: boolean
  systems: {
    combat?: { active: boolean; pending_reaction?: PendingReaction | null; started_seq?: number | null }
    chase?: { active: boolean }
    turn?: { confirmed_ids: string[]; total: number; ready: boolean }
  }
}


export function GameSessionPage() {
  // 参战结果写回本机角色卡：进入与离开各拉一次，见 features/characters/useSyncBack.ts。
  useSyncBackOnVisit()
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const isNew = (location.state as { isNew?: boolean })?.isNew
  const {
    currentSession, messages, addMessage, removeMessage, updateMessage, patchMessageMetadata, clearMessages,
    setCurrentSession, loadHistory, loadOlderEvents,
    hasMoreHistory, loadingOlder,
    startStreamMessage, appendToStream, endStream,
  } = useSessionStore()

  const [panelChar, setPanelChar] = useState<Character | null>(null)
  const [panelCharId, setPanelCharId] = useState<string | null>(null)
  const [refreshTick, setRefreshTick] = useState(0)
  // 新手引导：本机从未看过时自动弹一次（intro，从第一页走）；之后从顶栏那个问号重开时直接
  // 落在速查页（reference）——玩到一半回头查一条规则，不该先点三次「下一步」。
  // KP 席不弹——这几页讲的是玩家侧操作（输入语法/投骰/角色卡），对 KP 没用。
  const [showCoach, setShowCoach] = useState<false | 'intro' | 'reference'>(false)
  // 窄屏默认收起：角色卡在手机上是覆盖式抽屉，默认展开会挡住叙事流。
  const [showPanel, setShowPanel] = useState(
    () => !(typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches),
  )
  const [showBigMap, setShowBigMap] = useState(false)         // 大地图（已知地点前往）
  const [showRecap, setShowRecap] = useState(false)           // 战报 / 章节小结弹窗
  const [showGrowth, setShowGrowth] = useState(false)         // 成长结算弹窗
  const [portraitView, setPortraitView] = useState<string | null>(null)  // NPC 立绘放大查看

  const [showImprov, setShowImprov] = useState(false)         // 临场角色收编（房主专用）
  const [showStyle, setShowStyle] = useState(false)           // 本局文风/画风（房主专用）
  const [locations, setLocations] = useState<KnownLocation[]>([])
  const [mapNodes, setMapNodes] = useState<MapNodePayload[]>([])
  const [mapBackdrop, setMapBackdrop] = useState('')     // 沙盘氛围底图（模组里生成的那张俯视图）
  const [mapTab, setMapTab] = useState<'sandbox' | 'board'>('sandbox')  // 大地图页签：沙盘/调查板
  const [canGodView, setCanGodView] = useState(false)         // 真人 KP 席位（服务端判定）
  const [playerView, setPlayerView] = useState(false)         // KP 切「玩家视角」预览迷雾
  // 乐观 pending：check_request 刚到时 world_state.pending_checks 还没刷新（要等 done→refetch），
  // 若此时按 pending_checks 判定会先显示「已投骰」再翻成「投骰」按钮。用本地集先认它是待投，消除闪烁。
  const [optimisticPending, setOptimisticPending] = useState<Set<string>>(new Set())
  const [confirmTravel, setConfirmTravel] = useState<KnownLocation | null>(null)  // 前往二次确认
  // 已处理过的「要不要去」建议卡（同意或拒绝都算）：本机记，因为这是个人决定——
  // 多人同桌时同一张卡全桌可见，甲拒绝不该让乙的也消失。
  const [handledSuggests, setHandledSuggests] = useState<Set<string>>(() => new Set())
  const [splitView, setSplitView] = useState(true)            // 分头行动分栏（检测到多组时生效）
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(new Set())  // 被收起的分组
  const [combat, setCombat] = useState<CombatState | null>(null)  // 当前战斗态（非空时显示战斗面板）
  const [pendingReaction, setPendingReaction] = useState<PendingReaction | null>(null)  // 被 NPC 攻击时的反应提示
  const [chase, setChase] = useState<ChaseState | null>(null)  // 当前追逐态（非空时显示追逐面板）
  // 战斗日志抽屉的起点序号：新一场战斗开打（combat_start）时抬到「当前最大 seq」，
  // 抽屉只收本场（≥该序号）的 combat_log 行，避免上一场的机械结算窜进新面板。null=不设下限（重连恢复）。
  const [combatLogSince, setCombatLogSince] = useState<number | null>(null)

  const primaryId = currentSession?.player_character_id ?? null
  // KP 席是控制身份，不是玩家角色；同一 token 同时拥有 KP + 玩家席时，优先取角色席。
  const myPlayerSeat = currentSession?.participants?.find((p) => p.is_mine && p.role !== 'kp') ?? null
  // 无 participant 的旧单人会话才回退主角；KP-only 用户不能被误当成主角。
  const myCharId = myPlayerSeat?.character_id ?? (currentSession?.participants?.length ? null : primaryId)
  const isKp = currentSession?.kp_mode === 'human'
    && !!currentSession.participants?.some((p) => p.role === 'kp' && p.is_mine)
  // 独自开团 → 发送即推进（判定见 derive.isSoloTable）。KP 席不适用：他走右侧工作台。
  const soloTable = !isKp && !!currentSession && isSoloTable(currentSession.participants)
  const shownCharId = panelCharId ?? myCharId
  // 会话载入完、且确认自己是玩家（非 KP）后再决定弹不弹——KP 席不需要这套玩家侧操作说明。
  useEffect(() => {
    if (!currentSession || isKp) return
    if (!hasSeenCoach()) setShowCoach('intro')
  }, [currentSession, isKp])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  // 开局前置校验：是否已配置可用 AI（null=未知/检查中）。未配置时提示去设置，避免开场直接失败。
  const [aiConfigured, setAiConfigured] = useState<boolean | null>(null)
  // 生成已开始但还没吐出第一段内容（推理类模型先思考、此时无 token）→ 显示"KP 思考中"
  const [thinking, setThinking] = useState(false)
  // 叙事主流已停、仍在持锁收尾（滚动摘要/幕后推演）时的可读状态，别让玩家对无声脉冲点干等。
  const [tailNote, setTailNote] = useState('')
  // 生成持续过久（>15s）时浮现「打断并重新生成」——后端 cancel 能力已齐备，纯前端入口。
  const [showInterrupt, setShowInterrupt] = useState(false)
  // 历史检索：模糊搜索本局历史 + 跳转到对应消息
  const [showSearch, setShowSearch] = useState(false)
  // 回合确认制：本回合各真人的确认进度
  const [turnState, setTurnState] = useState<{ confirmed_ids: string[]; total: number; ready: boolean } | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)  // 消息流内容层，供 ResizeObserver 量高度变化
  const roRef = useRef<ResizeObserver | null>(null)
  // 「跳到最新」：贴底时不显示。ref 与 state 各留一份——自动滚底的副作用要同步读当前值，
  // 走 state 会读到上一帧的旧值。
  const atBottomRef = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  const [hasNewBelow, setHasNewBelow] = useState(false)
  const pinActive = useRef(false)   // 初次加载「持续钉底」窗口是否进行中（期间抑制平滑滚动，避免抢滚）
  const jumpInFlight = useRef(false)  // 「跳到最新」的平滑滚动进行中（期间不下调贴底状态）
  const jumpTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [waitSec, setWaitSec] = useState(0)   // 本次生成已等待秒数（长回合的进度感）
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const openingTriggered = useRef(false)
  const composingRef = useRef(false)
  const [typingName, setTypingName] = useState('')
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSent = useRef(0)
  const myName = myPlayerSeat?.character_name ?? null
  // 房主 = 我认领的席位是 0 号（房主锚点）；仅房主可收编临场角色
  const isHost = myPlayerSeat?.seat_order === 0
  const myNameRef = useRef<string | null>(null)
  myNameRef.current = myName
  const [liveConnected, setLiveConnected] = useState(true)

  // 入场动效：只对「新到达」的消息播放一次淡入/骰子弹入，历史与重连恢复的整批消息绝不逐条弹入。
  // 机制：renderedIds 记录已渲染过的消息 id；首屏（含刷新/重连的 loadHistory 整批）整批视为「已存在」不播入场。
  // 关键：渲染阶段只「读」不「写」ref（保持纯函数，兼容 StrictMode 双渲染）；所有 ref 变更放在 effect 里。
  const renderedIds = useRef<Set<string>>(new Set())
  const firstBatchDone = useRef(false)
  // 「本批之前见过的最大事件序号」——用于区分「实时新到达」（seq 更大，尾部追加）与
  // 「往前翻页 prepend 的历史事件」（seq 更小）。仅在 effect 里更新（渲染阶段只读上一提交的值）。
  const maxSeqSeen = useRef(-1)

  // 结束模组投票（全体真人共识）：进行中的公开投票态；无则 null。由 end_vote 广播与投票响应驱动。
  const [endVote, setEndVote] = useState<EndVoteState | null>(null)

  // —— 3D 骰子动画层 ——
  // diceRollerRef：命令式触发投掷；revealedDice：已「放行」显示结果卡的 dice 事件 id
  // （新到达的非暗投 dice 事件先播 3D 投掷、落定后才并入此集合 → 卡片随之显现，不先于动画蹦出）。
  const diceRollerRef = useRef<DiceRollerHandle>(null)
  // diceAnimating：正在（或即将）播 3D 投掷的 dice id → 结果卡先隐藏；revealedDice：已放行显示的 id。
  // 两者都是 state，且在 layout effect 里同步置入（paint 前），确保结果卡不会先于动画闪一下再消失。
  const [diceAnimating, setDiceAnimating] = useState<Set<string>>(new Set())
  const [revealedDice, setRevealedDice] = useState<Set<string>>(new Set())
  const animatedDiceIds = useRef<Set<string>>(new Set())   // 已处理过（播过或跳过）的 dice id，防重播
  // 从 /live 实时收到过的事件 id。骰子动画据此判定「是否本轮新到达」——
  // 不能靠 sequence_num 比较，见 handleLiveChunk 里的说明。
  const liveArrivedIds = useRef<Set<string>>(new Set())
  // 本轮「新到达」的 id 集合——纯派生，供渲染函数读取决定是否加 class。
  const enterIds = useMemo(() => {
    if (!firstBatchDone.current) return new Set<string>()   // 首屏整批不播入场
    const fresh = new Set<string>()
    for (const m of messages) {
      if (!m.id || m.id.startsWith('stream-') || renderedIds.current.has(m.id)) continue
      // 往前翻页 prepend 的历史事件：seq 不高于「本批之前的最大 seq」→ 不是实时新到达，
      // 不播入场/骰子动画（无 seq 的乐观/流式消息一律视为实时，照常播）。
      //
      // 例外：**确实从 /live 收到过**的事件一律算新到达。resyncHistory 与 SSE 是两条
      // 独立请求，谁先到不确定（联机走隧道时 resync 常常更快）；resync 先到就会把
      // maxSeqSeen 抬过这颗骰子，随后到达的它被误判成历史、动画被跳过。
      if (
        m.sequence_num != null
        && m.sequence_num <= maxSeqSeen.current
        && !liveArrivedIds.current.has(m.id)
      ) continue
      fresh.add(m.id)
    }
    return fresh
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages])
  // 提交后：把本轮所有 id 并入「已渲染」，并标记首屏已完成。只在 effect 里写 ref → StrictMode 安全。
  useEffect(() => {
    for (const m of messages) if (m.id) renderedIds.current.add(m.id)
    // 运行最大 seq（历史 prepend 的低 seq 抬不高它，实时新事件才抬高）→ 下一批的「实时」基线。
    for (const m of messages) if (m.sequence_num != null && m.sequence_num > maxSeqSeen.current) maxSeqSeen.current = m.sequence_num
    if (!firstBatchDone.current && messages.length > 0) firstBatchDone.current = true
  }, [messages])
  // 开局前置校验：进页时查一次「是否已配置可用 AI」。未配置时前端给出引导，
  // 不至于让玩家撞上开场直接失败还找不到原因。
  useEffect(() => {
    let alive = true
    api.get<{ configured: boolean }>('/settings/ai/status')
      .then((s) => { if (alive) setAiConfigured(!!s.configured) })
      .catch(() => { if (alive) setAiConfigured(null) })
    return () => { alive = false }
  }, [])

  // 生成超过 15s 才浮现「打断并重新生成」；生成结束即隐藏并复位。
  useEffect(() => {
    if (!streaming) { setShowInterrupt(false); return }
    const timer = setTimeout(() => setShowInterrupt(true), 15000)
    return () => clearTimeout(timer)
  }, [streaming])

  // 会话切换：重置动效追踪，新会话的首屏历史同样不逐条弹入。
  useEffect(() => {
    renderedIds.current = new Set()
    firstBatchDone.current = false
    maxSeqSeen.current = -1
    animatedDiceIds.current = new Set()
    liveArrivedIds.current = new Set()
    setDiceAnimating(new Set())
    setRevealedDice(new Set())
  }, [sessionId])

  // 3D 骰子投掷触发：新到达（enterIds）的非暗投、带 metadata.dice 的 dice 事件先隐藏结果卡并播 3D 投掷，
  // 落定后再放行结果卡（revealedDice）。历史/重连整批（firstBatchDone 前）不在 enterIds 里 → 绝不重播。
  // 暗投（blind）/无 metadata.dice（旧事件）/reduced-motion/无 WebGL：不播动画，直接显示结果卡。
  // 用 useLayoutEffect + state：paint 前同步把需播动画的骰子标进 diceAnimating，结果卡首帧即隐藏、不闪现。
  useLayoutEffect(() => {
    const toAnimate: { id: string; specs: DiceSpec[] }[] = []
    for (const m of messages) {
      if (!m.id || m.type !== 'dice') continue
      if (animatedDiceIds.current.has(m.id)) continue      // 已处理过，防重播
      if (!enterIds.has(m.id)) continue                    // 非本轮新到达（历史/重连）→ 直接显示
      animatedDiceIds.current.add(m.id)
      const specs = diceSpecsOf(m.metadata)
      const blind = !!m.metadata?.blind
      // 暗投 / 无骰子数据 / reduced-motion / 无 WebGL / 组件未就绪：不动画，直接显示（不进 diceAnimating）。
      const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
      if (blind || !specs.length || reduced || !diceRollerRef.current) continue
      toAnimate.push({ id: m.id, specs })
    }
    if (toAnimate.length === 0) return
    // 同步隐藏这些结果卡（state 更新在 paint 前生效）。
    setDiceAnimating((prev) => { const next = new Set(prev); for (const t of toAnimate) next.add(t.id); return next })
    // 逐条播动画（覆盖层同一时刻只播一个，队列串行；落定后放行对应结果卡）。
    void (async () => {
      for (const t of toAnimate) {
        // 一条事件可能有多段骰（理智检定＝先判成败、再掷损失），依次播完才放行结果卡。
        for (const spec of t.specs) {
          try { await diceRollerRef.current?.roll(spec) } catch { /* 降级：直接放行 */ }
        }
        setRevealedDice((prev) => new Set(prev).add(t.id))
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, enterIds])

  // 场景切换转场：current_scene_id 变化时放一层 600ms 暗幕淡入淡出（翻页感）。
  // 首帧/切会话只记基线，不放暗幕。
  const [sceneVeil, setSceneVeil] = useState(false)
  const prevSceneId = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    const sid = currentSession?.current_scene_id ?? null
    const prev = prevSceneId.current
    prevSceneId.current = sid
    if (prev === undefined) return           // 首帧：记基线不转场
    if (prev === sid || !sid) return          // 场景未变 / 无场景：不转场
    setSceneVeil(true)
    const t = setTimeout(() => setSceneVeil(false), 620)
    return () => clearTimeout(t)
  }, [currentSession?.current_scene_id])
  // 切会话时重置场景基线，避免跨会话误触发暗幕
  useEffect(() => { prevSceneId.current = undefined; setSceneVeil(false) }, [sessionId])

  // 场景氛围底：拿当前场景那张配图当底色来源（重度模糊压暗后铺满，见 index.css）。选图逻辑
  // 在 derive.sceneBackdropOf；这里只负责开关、拼服务器前缀，以及告诉 CSS 底铺上没有。
  const [backdropOn, setBackdropOn] = useState(() => getSceneBackdropEnabled())
  useEffect(() => {
    const sync = () => setBackdropOn(getSceneBackdropEnabled())
    window.addEventListener('focus', sync)          // 从设置页切回来即时生效
    return () => window.removeEventListener('focus', sync)
  }, [])
  const sceneBackdrop = useMemo(() => {
    if (!backdropOn) return ''
    const raw = sceneBackdropOf(messages, locations)
    if (!raw) return ''
    return /^https?:\/\//i.test(raw) ? raw : `${getServerUrl()}${raw}`
  }, [backdropOn, messages, locations])
  // 沙盘氛围底图：模组里生成的那张俯视图，局内大地图同样垫在网格之下。
  const sandboxBackdropUrl = useMemo(() => {
    if (!mapBackdrop) return undefined
    return /^https?:\/\//i.test(mapBackdrop) ? mapBackdrop : `${getServerUrl()}${mapBackdrop}`
  }, [mapBackdrop])
  // 氛围底真的铺上了才给正文加护读底：没图（未配生图 / 还在生成）时保持原有的透明观感。
  useEffect(() => {
    const root = document.documentElement
    if (sceneBackdrop) root.dataset.sceneBackdrop = 'on'
    else delete root.dataset.sceneBackdrop
    return () => { delete root.dataset.sceneBackdrop }
  }, [sceneBackdrop])

  // 角色名 → 归属（用于消息前的身份图标：我 / AI 队友 / 其他真人 / NPC）
  const partyByName = useMemo(
    () => buildPartyByName(currentSession?.participants),
    [currentSession?.participants],
  )
  const actorKind = (name?: string, isPlayer?: boolean): SeatKind =>
    resolveActorKind(partyByName, name, isPlayer)

  // 战斗日志抽屉内容：从消息流里筛出带 combat_log 标记的机械结算行（dice/system），
  // 按 combatLogSince 下限只留本场（重连时 since=null → 全收，与落库历史一致）。
  // 派生自 messages，故实时/历史/重连三路统一，无需单独维护日志数组。
  const combatLog = useMemo<CombatLogEntry[]>(
    () => selectCombatLog(messages, combatLogSince),
    [messages, combatLogSince],
  )

  // 战斗态下「本场最近一次掷骰结算」：钉在战斗面板顶，玩家无需收起面板即可看到本次成败/对抗双方数值。
  // 从后往前找最近一条已揭示的 dice（对抗有 metadata.opposed；命中有 combat_log/hit；也含战斗中的普通检定）；
  // 3D 投掷未落定的先跳过（避免结果先于动画蹦出）；越过本场起点（seq≤combatLogSince）即停，不显示上一场/开战前的旧结果。
  const combatResult = useMemo<CombatResultView | null>(
    () => selectCombatResult({
      combat, messages, since: combatLogSince, diceAnimating, revealedDice,
    }),
    [combat, messages, combatLogSince, diceAnimating, revealedDice],
  )

  // —— 沉浸战斗布局 ——
  // 战斗激活 + 宽视口 + 用户未切回经典时，页面主体从单列聊天切成「战场（左，约 2/3）+ 聊天侧栏（右，约 1/3）」。
  // 偏好本局内记住（useState 不持久化）：默认沉浸；切回经典后后续战斗沿用，直到再次手动切换。
  const [battleLayout, setBattleLayout] = useState<'immersive' | 'classic'>('immersive')
  // 响应式兜底：视口宽度不足 1100px 时不启用双栏，维持现有嵌入式面板。
  const [wideViewport, setWideViewport] = useState(() => window.matchMedia('(min-width: 1100px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1100px)')
    const onChange = (e: MediaQueryListEvent) => setWideViewport(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  const immersiveOn = !!combat && battleLayout === 'immersive' && wideViewport
  // 聊天侧栏折叠：折叠后战场更大；期间到达的新消息计入未读徽标（展开即清）。
  const [chatCollapsed, setChatCollapsed] = useState(false)
  // KP 控制台侧栏折叠：跨会话记住偏好（KP 常年开着，玩家看不到这一栏）
  const [kpCollapsed, setKpCollapsed] = useState(
    () => localStorage.getItem('trpg_kp_panel_collapsed') === '1',
  )
  useEffect(() => {
    localStorage.setItem('trpg_kp_panel_collapsed', kpCollapsed ? '1' : '0')
  }, [kpCollapsed])
  const chatBaseCount = useRef(0)   // 折叠那一刻的可见消息数（未读 = 当前数 − 它）
  useEffect(() => { if (!immersiveOn) setChatCollapsed(false) }, [immersiveOn])
  // 沉浸战斗 + KP 控制台成栏后是「战场 | 聊天 | 控制台」三栏，中段棋盘会被压到放不下。
  // 进入沉浸战斗那一刻替 KP 把聊天收成窄条（战场面板自带战斗日志，聊天此时基本冗余），
  // 之后 KP 手动展开就不再干预——只在转场瞬间做一次，不持续抢控制权。
  const kpCollapsedRef = useRef(kpCollapsed)
  kpCollapsedRef.current = kpCollapsed
  useEffect(() => {
    if (immersiveOn && isKp && !kpCollapsedRef.current) setChatCollapsed(true)
  }, [immersiveOn, isKp])
  // 进出沉浸布局时放一次短暗幕转场（复用场景转场 scene-veil 的观感；reduced-motion 下全局禁用）。
  const [battleVeil, setBattleVeil] = useState(false)
  const prevImmersiveOn = useRef(immersiveOn)
  useEffect(() => {
    if (prevImmersiveOn.current === immersiveOn) return
    prevImmersiveOn.current = immersiveOn
    setBattleVeil(true)
    const t = setTimeout(() => setBattleVeil(false), 620)
    return () => clearTimeout(t)
  }, [immersiveOn])
  // 聊天侧栏未读：只数会出现在主流里的消息（combat_log 机械结算行不算，它进战斗日志抽屉）。
  const chatMsgCount = useMemo(() => messages.filter((m) => m.metadata?.combat_log !== true).length, [messages])
  const chatUnread = chatCollapsed ? Math.max(0, chatMsgCount - chatBaseCount.current) : 0
  const toggleChatCollapsed = () => setChatCollapsed((v) => {
    if (!v) chatBaseCount.current = chatMsgCount
    return !v
  })
  // 展开聊天侧栏后把消息流重新钉底（display:none 期间 scrollTop 会归零）。
  useEffect(() => {
    if (chatCollapsed) return
    requestAnimationFrame(() => { const el = scrollRef.current; if (el) el.scrollTop = el.scrollHeight })
  }, [chatCollapsed])

  const seenIds = useRef<Set<string>>(new Set())
  const liveTypeRef = useRef<string>('')
  const liveGroupRef = useRef<string>('')   // 当前流式 narration 所属分组（分头行动实时分栏）
  const myCharIdRef = useRef<string | null>(null)
  useEffect(() => { myCharIdRef.current = myCharId }, [myCharId])

  // 从数据库重新对齐历史（替换式），并重建去重集。用于：每次(重)连接、生成结束。
  const resyncHistory = useCallback(async () => {
    if (!sessionId) return
    await loadHistory(sessionId)
    const s = new Set<string>()
    for (const m of useSessionStore.getState().messages) if (m.id) s.add(m.id)
    seenIds.current = s
    liveTypeRef.current = ''
  }, [sessionId, loadHistory])

  // 从服务端重新对齐所有 sync 类状态（战斗/追逐/回合确认）。
  // 用于：进页 + **每次重连**。此前战斗/追逐只在进页时各拉一次、回合确认态更是连
  // 查询端点都没有——断线期间战斗开打或结束、别人确认了回合，HUD 会一直停在旧状态，
  // 直到下一次恰好有相关广播才纠正。
  const resyncState = useCallback(async () => {
    if (!sessionId) return
    try {
      const s = await api.get<SyncSnapshot>(`/sessions/${sessionId}/sync`)
      const combatSnap = s.systems.combat
      setCombat(combatSnap?.active ? (combatSnap as unknown as CombatState) : null)
      setPendingReaction(combatSnap?.active ? (combatSnap.pending_reaction ?? null) : null)
      setCombatLogSince(combatSnap?.active ? (combatSnap.started_seq ?? null) : null)
      const chaseSnap = s.systems.chase
      setChase(chaseSnap?.active ? (chaseSnap as unknown as ChaseState) : null)
      setTurnState(s.systems.turn ?? null)
    } catch {
      // 取不到就维持现状：宁可显示旧状态，也不要在网络抖动时把战斗面板清空
    }
  }, [sessionId])

  // 节流刷新会话（席位/在线变更用）：合并 400ms 内的连续 presence/seat，避免风暴
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refetchSession = useCallback(() => {
    if (!sessionId || refetchTimer.current) return
    refetchTimer.current = setTimeout(() => {
      refetchTimer.current = null
      api.get(`/sessions/${sessionId}`).then((s) => setCurrentSession(s as never)).catch(() => {})
    }, 400)
  }, [sessionId, setCurrentSession])

  // 沙盘/已知地点刷新（节流）：新地点可能因为一条旁白/一句对话/一张系统卡刚刚解锁，
  // 不必等整轮 done 才重拉 /locations；合并 400ms 内的连续日志事件，只发一次。
  const locationsRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const scheduleLocationsRefresh = useCallback(() => {
    if (!sessionId || locationsRefreshTimer.current) return
    locationsRefreshTimer.current = setTimeout(() => {
      locationsRefreshTimer.current = null
      setRefreshTick((x) => x + 1)
    }, 400)
  }, [sessionId])

  useEffect(() => () => {
    if (locationsRefreshTimer.current) clearTimeout(locationsRefreshTimer.current)
  }, [])

  // 处理一条房间实时事件（/live）。
  //
  // 结构对应 room_events.py 的三分类：`log` 类走「按 id 去重 → 收流 → 落消息」的公共
  // 路径（下半段），`stream`/`sync` 类各自独立处理、不参与去重（上半段）。两段各自以
  // never 分支收尾——后端新增一种事件而这里没处理，编译不过。
  // stream / sync 类：面板与流控状态，不进消息流、不参与 id 去重。
  const handleNonLogChunk = useCallback((chunk: ChunkPayload, t: NonLogEventType) => {
    if (t === 'ready') {
      setLiveConnected(true)
      // 权威同步生成态：ready 由服务端在 subscribe 之后捕获，据此校正 streaming，
      // 避免「生成在 GET /generating 与订阅之间结束 → 漏收 done → 卡在整理笔记、输入锁死」。
      const gen = !!(chunk.metadata as { generating?: boolean } | undefined)?.generating
      setStreaming(gen)
      if (!gen) { setThinking(false); setTailNote('') }
      return
    }
    if (t === 'generating') { setStreaming(true); setThinking(true); return }
    if (t === 'turn_state') { setTurnState((chunk.metadata as { confirmed_ids: string[]; total: number; ready: boolean }) || null); return }
    if (t === 'kp_request' || t === 'kp_turn_ready' || t === 'kp_roll_ready') {
      if (isKp && chunk.content) toast.info(chunk.content)
      return
    }
    if (t === 'kp_action') return
    if (t === 'combat_start') {
      // 新战斗：日志下限取后端透传的 started_seq（本场开打前最大 seq），抽屉只收本场结算、不掺上一场；
      // 后端未带时回退到客户端已见最大 seq。
      const meta = parseCombatState(chunk.metadata)
      setCombat(meta); setPendingReaction(null)
      setCombatLogSince(meta?.started_seq ?? maxSeqSeen.current)
      return
    }
    if (t === 'combat_state') { setCombat(parseCombatState(chunk.metadata)); setPendingReaction(null); setRefreshTick((x) => x + 1); return }  // 续跑广播新态 → 清反应提示 + 刷新角色卡 HP/状态
    if (t === 'combat_reaction_prompt') { setPendingReaction(parsePendingReaction(chunk.metadata)); return }  // NPC 攻击真人：弹反应按钮
    if (t === 'combat_end') { setCombat(null); setPendingReaction(null); return }  // 结果那句话已由后端落库为消息，不额外处理
    if (t === 'inventory_update') { setRefreshTick((x) => x + 1); return }  // 库存变更 → 刷新角色卡「道具」页
    if (t === 'character_update') { setRefreshTick((x) => x + 1); return }  // HP/SAN/状态变更 → 刷新角色卡数值
    if (t === 'chase_start' || t === 'chase_state') { setChase(parseChaseState(chunk.metadata)); return }
    if (t === 'chase_end') { setChase(null); return }  // 结果那句话已由后端落库为消息，不额外处理
    if (t === 'event_delete') { if (chunk.id) removeMessage(chunk.id); return }
    if (t === 'event_update') { if (chunk.id) updateMessage(chunk.id, chunk.content || ''); return }
    if (t === 'event_patch') {
      // 已渲染事件的 metadata 增量更新（如手书配图异步生成完成后补 image）：浅合并触发重渲，找不到则忽略
      const meta = chunk.metadata as { event_id?: string; patch?: Record<string, unknown> } | undefined
      if (meta?.event_id && meta.patch) patchMessageMetadata(String(meta.event_id), meta.patch)
      return
    }
    if (t === 'housekeeping') {
      // 叙事已停但仍在收尾（摘要/幕后）：脉冲点旁给出可读文案，输入仍锁但不再无解释
      setThinking(false); setTailNote(chunk.content || 'KP 正在整理笔记…')
      return
    }
    if (t === 'done') {
      endStream(); liveTypeRef.current = ''
      setStreaming(false); setThinking(false); setTailNote(''); setRefreshTick((x) => x + 1)
      setTurnState(null)  // 新回合开始：确认进度归零
      setOptimisticPending(new Set())  // done 后 pending_checks 由 refetch 权威刷新，清乐观集
      // 生成结束后从 DB 对齐：用持久化的最终叙述替换流式拼接的内容，
      // 同时兜住「刷新落在生成完成瞬间」时丢失的那段叙述。
      void resyncHistory()
      // 同步会话状态：刷新 world_state.pending_checks，使「投骰」按钮按待定检定增减。
      refetchSession()
      return
    }
    if (t === 'seat') {
      // 有人入座：刷新房间席位（更新队伍条与 is_mine），并提示一条系统消息
      refetchSession()
      endStream(); liveTypeRef.current = ''
      addMessage({ id: '', type: 'system', content: chunk.content || '有新成员入座', actor_name: chunk.actor_name })
      return
    }
    if (t === 'presence') {
      // 有人上/下线：刷新席位以更新队伍条上的在线点
      refetchSession()
      return
    }
    if (t === 'status') {
      // 会话状态变更（如结束模组达成共识）：刷新会话，成长/最终战报入口据 status 出现
      setEndVote(null)   // 投票已收束（结束或失效）
      refetchSession()
      if (chunk.content) addMessage({ id: '', type: 'system', content: chunk.content, actor_name: chunk.actor_name })
      return
    }
    if (t === 'end_vote') {
      // 结束模组投票进度：更新各端「已同意 N/M」提示；未进行中则收起
      const v = (chunk.metadata as { end_vote?: EndVoteState } | undefined)?.end_vote
      setEndVote(v && v.open ? v : null)
      return
    }
    if (t === 'typing') {
      if (chunk.actor_name && chunk.actor_name !== myNameRef.current) {
        setTypingName(chunk.actor_name)
        if (typingTimer.current) clearTimeout(typingTimer.current)
        typingTimer.current = setTimeout(() => setTypingName(''), 3000)
      }
      return
    }
    if (t === 'narration') {
      setThinking(false); setTailNote('')  // 有新叙述 token → 不再是"思考中/收尾中"
      // 分头行动按组生成时，narration chunk 带 metadata.group；切换分组要另起一条流式消息，
      // 否则多组叙述会被拼进同一条、实时分栏失效（done 后 resync 会再按落库分组对齐）。
      const grp = String((chunk.metadata as Record<string, unknown> | undefined)?.group || '')
      if (liveTypeRef.current !== 'narration' || liveGroupRef.current !== grp) {
        endStream()
        startStreamMessage('narration', 'KP', grp ? { group: grp } : undefined)
        liveTypeRef.current = 'narration'; liveGroupRef.current = grp
      }
      appendToStream(chunk.content || '')
      return
    }
    if (t === 'map_update') {
      // 局内 KP 拖动了场景位置：同时开着大地图的人跟着刷新，否则各自看到的位置不一致
      setRefreshTick((x) => x + 1)
      return
    }
    // 明确忽略：这两类是大厅页的事，游戏页收到也无事可做。
    // （由穷尽性检查逼出来的——此前它们只是无声地落进 if 链末尾。）
    //   lobby   —— 席位/准备态变化，游戏页的队伍条由 seat/presence 驱动
    //   started —— 开局广播，大厅页据此跳转；本页已经在局内
    if (t === 'lobby' || t === 'started') return
    // 走到这里说明有 stream/sync 类型没被处理——加了新事件就必须在上面补一支。
    assertAllNonLogHandled(t)
  }, [refetchSession, resyncHistory, addMessage, removeMessage, updateMessage, patchMessageMetadata, endStream, startStreamMessage, appendToStream, isKp])

  // log 类：进消息流的持久事件。公共前导 = 按 id 去重（与历史/重连对齐）+ 收流。
  const handleLogChunk = useCallback((chunk: ChunkPayload, t: LogEventType) => {
    if (chunk.id) {
      if (seenIds.current.has(chunk.id)) return
      seenIds.current.add(chunk.id)
    }
    setThinking(false)  // 任何具体内容（对话/检定/系统）到达 → 不再是"思考中"
    endStream(); liveTypeRef.current = ''
    // 旁白/对话/系统卡都可能解锁新地点（known_scene_ids 会扫这些事件正文），
    // 节流重拉 locations，沙盘不必等 done 或手动刷新。
    if (t === 'narration_full' || t === 'dialogue' || t === 'npc_dialogue' || t === 'action' || t === 'system') {
      scheduleLocationsRefresh()
    }
    const isPlayer = !!(myCharIdRef.current && chunk.actor_id === myCharIdRef.current)
    if (t === 'dialogue' || t === 'npc_dialogue') {
      addMessage({ id: chunk.id || '', type: 'dialogue', content: chunk.content || '', actor_name: chunk.actor_name, metadata: { ...(chunk.metadata || {}), is_player: isPlayer } })
    } else if (t === 'action') {
      addMessage({ id: chunk.id || '', type: 'action', content: chunk.content || '', actor_name: chunk.actor_name, metadata: { ...(chunk.metadata || {}), is_player: isPlayer } })
    } else if (t === 'narration_full') {
      addMessage({ id: chunk.id || '', type: 'narration', content: chunk.content || '', actor_name: 'KP' })
    } else if (t === 'dice' || t === 'system' || t === 'ooc') {
      // 战斗机械结算（metadata.combat_log）不灌主聊天流——它进 CombatStage 的折叠战斗日志抽屉。
      // 仍照常 addMessage（落库/复盘/重连一致），但渲染时按 combat_log 从主流剔除、只在抽屉里出现。
      addMessage({ id: chunk.id || '', type: t, content: chunk.content || '', actor_name: chunk.actor_name, metadata: chunk.metadata })
    } else if (t === 'check_request') {
      // 待定检定提示：作为系统消息存（metadata.check_request 携带 check_id），渲染时带「投骰」按钮
      addMessage({ id: chunk.id || '', type: 'system', content: chunk.content || '', actor_name: chunk.actor_name, metadata: { ...(chunk.metadata || {}), is_player: isPlayer } })
      // 乐观置为待投：后端已先落 pending_checks 再广播本 chunk，但前端 world_state 要等 done 才刷新，
      // 先本地认它待投，避免卡片先闪「已投骰」再翻成按钮。
      const cid = String((chunk.metadata as Record<string, unknown> | undefined)?.id ?? '')
      if (cid) setOptimisticPending((s) => new Set(s).add(cid))
    } else {
      // 走到这里说明有 log 类型没被渲染——加了新事件就必须在上面补一支。
      assertAllLogHandled(t)
    }
  }, [addMessage, endStream, scheduleLocationsRefresh])

  const handleLiveChunk = useCallback((chunk: ChunkPayload) => {
    const t = chunk.type
    // 记下「这条是从 /live 实时收到的」。骰子动画据此判断该不该播，而不是靠比较
    // sequence_num——`done` 会触发 resyncHistory，而 resync 是另开一条请求，可能
    // 比长连接上的推送先到（联机走隧道时尤其明显）。那时整批历史已经把
    // maxSeqSeen 抬过了这颗骰子，它就被误判成「历史事件」，动画被跳过、直接出结果。
    if (chunk.id) liveArrivedIds.current.add(chunk.id)
    if (categoryOf(t) === 'log') { handleLogChunk(chunk, t as LogEventType); return }
    handleNonLogChunk(chunk, t as NonLogEventType)
  }, [handleLogChunk, handleNonLogChunk])


  useEffect(() => {
    if (!sessionId) return
    const ac = new AbortController()
    let cancelled = false
    seenIds.current = new Set()
    liveTypeRef.current = ''
    const init = async () => {
      clearMessages()
      // 直接拉新鲜会话状态，不信缓存列表——否则刚从大厅开局过来时缓存还是 setup，
      // 会与大厅页的 active 跳转来回弹跳（疯狂刷新 / 参与者被弹回 /game）。
      let session
      try {
        session = await api.get<{ id: string; status: string }>(`/sessions/${sessionId}`)
      } catch {
        navigate('/game', { replace: true }); return
      }
      if (cancelled) return
      if (!session) { navigate('/game', { replace: true }); return }
      if (session.status === 'setup') { navigate(`/room/${sessionId}`, { replace: true }); return }
      setCurrentSession(session as never)

      if (isNew && !openingTriggered.current) {
        openingTriggered.current = true
        setStreaming(true)
        api.post(`/sessions/${sessionId}/opening`).catch(() => {})
      }

      // /live 常驻消费 + 自动重连。时序（先订阅后对齐、缓冲回放、每次重连都对齐）
      // 抽到 lib/liveSession.ts，那里有针对性的单测——这段逻辑的正确性全在顺序上，
      // 埋在组件里既没法测也容易看漏。
      // 生成态不再用独立 GET（与订阅有竞态）——由 /live 首个 ready 事件权威给出（见 handleLiveChunk）
      await runLiveSession<ChunkPayload>({
        connect: (signal) => connectSSE(`/sessions/${sessionId}/live`, signal) as AsyncIterable<ChunkPayload>,
        resync: async () => { await Promise.all([resyncHistory(), resyncState()]) },
        onEvent: handleLiveChunk,
        onDisconnect: () => setLiveConnected(false),  // → 显示「连接中…」，下次 ready 复位
        isCancelled: () => cancelled,
        wait: (ms) => new Promise((r) => setTimeout(r, ms)),
        signal: ac.signal,
      })
    }
    init()
    return () => { cancelled = true; ac.abort() }
  }, [sessionId])

  useEffect(() => {
    if (shownCharId) {
      api.get<Character>(`/characters/${shownCharId}`).then(setPanelChar)
    } else {
      setPanelChar(null)
    }
  }, [shownCharId, refreshTick])

  // 我的武器栏（战斗武器选择器用）：拳头默认由前端补，其余从角色卡 system_data.weapons 取。
  const [myWeapons, setMyWeapons] = useState<{ name: string; dam?: string }[]>([])
  useEffect(() => {
    if (!myCharId) { setMyWeapons([]); return }
    api.get<Character>(`/characters/${myCharId}`)
      .then((c) => {
        const ws = (c.system_data?.weapons as { name?: string; dam?: string }[] | undefined) || []
        setMyWeapons(ws.filter((w) => w?.name).map((w) => ({ name: String(w.name), dam: w.dam })))
      })
      .catch(() => setMyWeapons([]))
  }, [myCharId, refreshTick])

  // 已知地点：前往后/生成结束刷新。真人 KP 返回全图（god_view）。
  //
  // 原来只在展开大地图时才拉（`if (!showBigMap) return`），现在改为常驻——场景氛围底要靠
  // 它拿「**我自己**所在场景」及其配图：分头行动时各人所在地不同，会话的 current_scene_id
  // 只是房主锚点，照它渲染会让分头的队友都看到房主那间屋子。存量存档也只有这里取得到图
  // （配图回写在 scene.image，而聊天流里那条「抵达」插图消息可能不在已加载的分页里）。
  //
  // refreshTick 这个依赖是必需的、不能为了省一次请求删掉：客人自己「前往」时只改
  // party_locations，不动 current_scene_id（那只在主角移动时同步），光靠场景 id 变化
  // 这条副作用不会重跑，客人的背景会一直停在旧场景。抵达叙述结束时 refreshTick 会 +1。
  // 代价可接受：实测取全量事件 + 算地点在 70 条事件的会话上约 1ms，2000 条外推约 30ms，
  // 相对一回合几十秒的 LLM 调用可以忽略。
  useEffect(() => {
    if (!sessionId) return
    const q = myCharId ? `?char_id=${myCharId}` : ''
    api.get<{ locations: KnownLocation[]; map_nodes?: MapNodePayload[]; god_view?: boolean; map_backdrop?: string }>(`/sessions/${sessionId}/locations${q}`)
      .then((r) => {
        setLocations(r.locations || []); setMapNodes(r.map_nodes || [])
        setCanGodView(!!r.god_view); setMapBackdrop(r.map_backdrop || '')
      })
      .catch(() => { setLocations([]); setMapNodes([]); setCanGodView(false); setMapBackdrop('') })
  }, [sessionId, myCharId, currentSession?.current_scene_id, refreshTick])

  // 局内沙盘拖拽（真人 KP 专属）：单格移动立即落库并广播，撞格等非法情形由后端拒绝。
  // 走 PATCH /modules/{id}/scene-map 而不是整体保存模组——局内 KP 只该改位置，
  // 不该有整体改模组的权限；后端按 session_id 校验 KP 席位（远程 KP 也能拖）。
  const dragScene = useCallback(async (nodeId: string, q: number, r: number) => {
    const moduleId = currentSession?.module_id
    if (!moduleId || !sessionId) return
    const node = mapNodes.find((n) => n.id === nodeId)
    const sceneId = node?.scene_id || nodeId
    try {
      await api.patch(`/modules/${moduleId}/scene-map`, {
        scene_id: sceneId, q, r, session_id: sessionId,
      })
      setRefreshTick((x) => x + 1)
    } catch (reason: unknown) {
      toast.error(reason instanceof Error ? reason.message : '移动失败')
      setRefreshTick((x) => x + 1)   // 拉回权威位置，避免界面停在拖到一半的样子
    }
  }, [currentSession?.module_id, sessionId, mapNodes])

  const sandboxLocations = useMemo(() => {
    const scenesById = new Map(locations.map((loc) => [loc.id, loc]))
    // map 是整体覆盖 loc.map 的，parent 必须显式带上——漏了它，子沙盘里的场景与地面会
    // 拿着子层坐标一起画进顶层：《鬼屋》的街区内景正好压在疗养院与礼拜堂那两格上，
    // 把它们盖成不可点的空地（透明命中区吃掉点击，而地貌格本身不响应「前往」）。
    // 层级以**场景**为准（map_nodes 只同步坐标与地貌），地貌节点则用它自己身上的 parent。
    const sceneNodes = mapNodes.filter((node) => node.scene_id && scenesById.has(node.scene_id)).map((node) => {
      const loc = scenesById.get(node.scene_id!)!
      return {
        ...loc, id: node.id, sceneId: loc.id, nodeKind: 'scene' as const,
        map: { q: node.q, r: node.r, biome: node.biome, parent: loc.map?.parent ?? node.parent },
      }
    })
    const included = new Set(sceneNodes.map((node) => node.sceneId))
    const fallbackScenes = locations.filter((loc) => !included.has(loc.id)).map((loc) => ({ ...loc, sceneId: loc.id, nodeKind: 'scene' as const }))
    const terrainNodes = mapNodes.filter((node) => !node.scene_id).map((node) => ({
      id: node.id, name: '', current: false, visited: true, known: true,
      nodeKind: 'terrain' as const,
      map: { q: node.q, r: node.r, biome: node.biome, parent: node.parent },
    }))
    return [...sceneNodes, ...fallbackScenes, ...terrainNodes]
  }, [locations, mapNodes])
  const playerSandboxLocations = useMemo(() => {
    if (!canGodView || !playerView) return sandboxLocations
    return sandboxLocations.map((node) => node.nodeKind === 'scene' && node.known === false
      ? { ...node, name: '', sceneId: undefined, nodeKind: 'terrain' as const, connections: undefined, known: true }
      : node)
  }, [canGodView, playerView, sandboxLocations])

  const travelTo = async (sceneId: string) => {
    if (!currentSession || streaming) return
    try {
      // 暂存模式：把「前往」加入本回合（与发言同批），推进本回合时随之执行位置同步 + 抵达叙述，
      // 不再单独触发一次生成、也不必先说一句再手动点图。
      await api.post(`/sessions/${currentSession.id}/travel`,
        { scene_id: sceneId, acting_character_id: myCharId, stash: true })
      setShowBigMap(false)
      toast.success('已把「前往」加入本回合，点「推进本回合」后一起执行')
    } catch { /* 已在该地点 / 不可前往 等，由后端校验 */ }
    finally { setConfirmTravel(null) }
  }

  // 「要不要去某处」建议卡：同意 → 走的就是暂存式「前往」那条路（与大地图点选完全同一条），
  // 拒绝 → 只把卡片标记为已处理，不产生任何事件，正是「不同意则无事发生」。
  const acceptTravelSuggest = async (eventId: string, sceneId: string, sceneName: string) => {
    if (!currentSession || streaming) {
      if (streaming) toast.error('KP 正在叙事，请稍候')
      return
    }
    try {
      await api.post(`/sessions/${currentSession.id}/travel`,
        { scene_id: sceneId, acting_character_id: myCharId, stash: true })
      setHandledSuggests(markSuggestionHandled(currentSession.id, eventId))
      toast.success(`已把「前往${sceneName}」加入本回合，点「推进本回合」后一起执行`)
    } catch (e: unknown) {
      // 与大地图不同，这里要把失败讲出来：卡片是系统主动递到玩家面前的，
      // 点了没反应会让人以为是卡住了。
      toast.error(e instanceof Error ? e.message : '暂时无法前往该地点')
    }
  }
  useEffect(() => {
    // 换局/刷新时载入本机记录，免得已经点过的卡片又冒出来一次
    setHandledSuggests(currentSession?.id ? loadHandledSuggestions(currentSession.id) : new Set())
  }, [currentSession?.id])

  const declineTravelSuggest = (eventId: string) => {
    if (!currentSession) return
    setHandledSuggests(markSuggestionHandled(currentSession.id, eventId))
  }

  // 初次加载/刷新落底：此刻 markdown 等内容布局会在随后一段时间里持续膨胀，单次（甚至两帧）
  // 滚动都会朝偏小的 scrollHeight 落在半空。改为在一个有限窗口内「持续钉底」——每帧把主区与各
  // 分栏列都钉到底，直到用户主动滚动或窗口结束，内容再怎么回流也能兜住。用 hasMessages（布尔）
  // 驱动：只在「消息从无到有」时启动一次，加载期间消息增多不会重跑本副作用、也就不会中断钉底。
  const hasMessages = messages.length > 0
  useEffect(() => {
    if (!hasMessages) return
    const el = scrollRef.current
    if (!el) return
    pinActive.current = true
    let raf = 0
    const deadline = performance.now() + 1200
    const pin = () => {
      const e = scrollRef.current
      if (e) {
        e.scrollTop = e.scrollHeight
        e.querySelectorAll<HTMLElement>('[data-scene-col]').forEach((c) => { c.scrollTop = c.scrollHeight })
      }
      if (pinActive.current && performance.now() < deadline) raf = requestAnimationFrame(pin)
      else pinActive.current = false
    }
    raf = requestAnimationFrame(pin)
    const stop = () => { pinActive.current = false }  // 用户一动就停，别打断他往上翻
    el.addEventListener('wheel', stop, { passive: true })
    el.addEventListener('touchmove', stop, { passive: true })
    return () => {
      pinActive.current = false
      if (raf) cancelAnimationFrame(raf)
      el.removeEventListener('wheel', stop)
      el.removeEventListener('touchmove', stop)
    }
  }, [hasMessages])

  // 后续新消息：平滑到底（初次钉底窗口期间交给钉底循环，避免两者抢滚）。
  // 但**只在用户本来就贴着底**时才滚——他要是正往上翻看之前的线索，
  // 新叙事把他一把拽回底部是很恼人的；此时改为亮出「跳到最新」提示，由他自己决定。
  useEffect(() => {
    if (!hasMessages || pinActive.current) return
    if (!atBottomRef.current) {
      setHasNewBelow(true)
      return
    }
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages.length])

  // 粘底：只要用户贴着底，内容长高就立刻跟到底。
  //
  // 光靠上面那个 [messages.length] 的副作用是不够的，它漏掉了两类最常见的增长：
  // ① **流式续写**——KP 叙事逐段写进同一条消息，条数不变，副作用一次都不触发，玩家眼睁睁
  //    看着文字往下长出可视区；
  // ② **迟到的布局膨胀**——场景配图加载完、markdown 排版回流，都发生在那一次 scrollTo 之后，
  //    于是滚动落在半空（实测「有新内容」点下去只挪了 94px，容器还剩 1276px 可滚）。
  // ResizeObserver 直接盯内容层高度，谁把它撑高都能兜住。用瞬时赋值不用 smooth：流式期间
  // 每秒好几次增长，平滑动画会互相打断，反而永远追不上。
  //
  // 走 **callback ref** 而不是 useEffect + ref.current：组件在 currentSession 为空时先返回
  // 「加载中」，那一轮根本没有滚动容器，effect 里的 ref.current 是 null 直接早退，而依赖数组
  // 又不变 → 永远不会重挂。文件里 handleScroll 上方那段注释记着的正是同一个坑。
  const attachContent = useCallback((node: HTMLDivElement | null) => {
    contentRef.current = node
    roRef.current?.disconnect()
    roRef.current = null
    if (!node) return
    const ro = new ResizeObserver(() => {
      const el = scrollRef.current
      if (!el || !atBottomRef.current) return   // 用户正在往上翻，别把他拽回来
      el.scrollTop = el.scrollHeight
      el.querySelectorAll<HTMLElement>('[data-scene-col]')
        .forEach((c) => { c.scrollTop = c.scrollHeight })
    })
    ro.observe(node)
    roRef.current = ro
  }, [])
  useEffect(() => () => { roRef.current?.disconnect() }, [])

  // 等待秒数：规划 + 工具循环 + 叙事串起来，一个回合跑一分多钟很正常，而屏幕上只有一句不动的
  // 「守秘人正在判读局势…」和三个点——玩家会开始怀疑它卡死了。给个在走的数字，等待就有了底。
  useEffect(() => {
    if (!streaming) { setWaitSec(0); return }
    const started = Date.now()
    const t = setInterval(() => setWaitSec(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(t)
  }, [streaming])

  const jumpToLatest = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // 先把「贴底」置真，这样上面的 ResizeObserver 会接手后续的迟到膨胀（图片加载完等），
    // 不会像原来那样只滚一次、落在半空还把提示消掉了。
    atBottomRef.current = true
    setAtBottom(true)
    setHasNewBelow(false)
    // 平滑滚动途中每一帧都会触发 onScroll，而途中离底还很远 → handleScroll 会把 atBottomRef
    // 打回 false，粘底当场失效。挂个飞行标记，让 handleScroll 在落地前别下调这个状态。
    // 兜底超时：用户在动画途中自己往上翻时不会有「落地」那一下，标记不能就此卡死。
    jumpInFlight.current = true
    if (jumpTimer.current) clearTimeout(jumpTimer.current)
    jumpTimer.current = setTimeout(() => { jumpInFlight.current = false }, 1200)
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [])

  // 分头行动的每个场景列是各自独立滚动的容器，主页面的「滚到底」管不到它们。初次钉底窗口
  // 期间由上面的钉底循环负责；窗口结束后（如实时新增的分栏内容）在此把各列各自滚到底。
  useLayoutEffect(() => {
    if (!splitView || pinActive.current) return
    const snap = () => scrollRef.current
      ?.querySelectorAll<HTMLElement>('[data-scene-col]')
      .forEach((el) => { el.scrollTop = el.scrollHeight })
    snap()
    requestAnimationFrame(snap)
  }, [messages.length, splitView])

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // 先记「是否贴底」——这段不能被下面加载旧史的早退挡掉，否则翻到顶部时状态就不更新了。
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_SLACK
    // 「跳到最新」的平滑滚动途中不下调贴底状态：途中每一帧都离底很远，照单全收会在落地前
    // 就把粘底关掉，于是又停在半空。滚到底了才解除飞行标记。
    if (jumpInFlight.current && !nearBottom) return
    jumpInFlight.current = false
    atBottomRef.current = nearBottom
    setAtBottom(nearBottom)
    if (nearBottom) setHasNewBelow(false)   // 自己滚回底部即消掉新内容提示

    if (!sessionId || loadingOlder || !hasMoreHistory) return
    if (el.scrollTop < 80) {
      const prevHeight = el.scrollHeight
      loadOlderEvents(sessionId).then(() => {
        requestAnimationFrame(() => {
          if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight - prevHeight
          }
        })
      })
    }
  }, [sessionId, loadingOlder, hasMoreHistory, loadOlderEvents])

  // 滚动监听走 JSX 的 onScroll，不要再用 effect + addEventListener 手动挂。
  //
  // 原来那版是：useEffect(() => { const el = scrollRef.current; if (!el) return; ... }, [handleScroll])
  // 组件在 currentSession 为空时会先 return 一个「加载中」，此时挂载的 DOM 里根本没有滚动容器，
  // scrollRef.current 是 null、effect 直接早退；而 handleScroll 的依赖
  // （sessionId/loadingOlder/hasMoreHistory/loadOlderEvents）在会话载入前后并不变，
  // 它的函数标识因此始终不变 → effect 永不重跑 → 监听器一次都没挂上。
  // 结果是「滚到顶部加载更早历史」其实一直是失效的，只是没人注意到。
  // 交给 React 的 onScroll 就没有这个时序问题：容器一挂载就带着监听。

  // 玩家「申请」检定：只报技能，难度交 KP 裁定（玩家不指定）；intent 是顺带说明的检定目标，
  // 场景里同时有多条线索/多个可疑点时，光报技能名 KP 猜不出玩家具体想查什么。
  const rollCheck = async (skill: string, intent: string) => {
    if (!currentSession || streaming) {
      if (streaming) toast.error('KP 正在叙事，请稍候')
      return
    }
    // 暂存模式（与大地图「前往」一致）：申请进本回合暂存，和这一轮的台词、行动一起交给 KP。
    // 「一边说话一边动手查」本就是一个回合内的事——各自单独触发一次生成既慢，也让 KP 看不到
    // 完整上下文（先跑的那次没有后写的台词）。暂存后还能像别的暂存发言一样改或删。
    try {
      await api.post(`/sessions/${currentSession.id}/check`, {
        skill, intent, acting_character_id: myCharId, stash: true,
      })
      toast.success('已把检定申请加入本回合，点「推进本回合」后与发言一起交给 KP')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '检定申请失败')
    }
  }

  // 重新生成最新一轮 KP：打断卡住的生成 → 回滚上一轮 KP 叙事 → 用玩家/队友的既有输入重跑
  // （保留已定骰子）。高风险，仅供生成卡住或结果明显有问题时用，经二次确认后才会走到这里。
  const regenerate = async () => {
    if (!currentSession) return
    try {
      setStreaming(true)
      setThinking(true)   // 立即进入「KP 思考中」
      await api.post(`/sessions/${currentSession.id}/regenerate`, {})
      // 后端此时已回滚上一轮 KP 叙事（DB 也已删除），立刻按最新历史对齐——
      // 旧叙事从界面上「消失」，只剩思考中，随后 /live 流式推入重生成的内容。
      await resyncHistory()
    } catch (e: unknown) {
      setStreaming(false)
      setThinking(false)
      toast.error(e instanceof Error ? e.message : '重新生成失败')
    }
  }

  // 重试开场：开场生成失败（或刷新后 state 丢失、从未成功）时的重入口。POST /opening 幂等，
  // 已有正式叙事则后端只收尾、不重复；输出经已开着的 /live 流推入。
  const retryOpening = async () => {
    if (!currentSession) return
    try {
      setStreaming(true)
      setThinking(true)
      await api.post(`/sessions/${currentSession.id}/opening`, {})
    } catch (e: unknown) {
      setStreaming(false)
      setThinking(false)
      toast.error(e instanceof Error ? e.message : '重试开场失败')
    }
  }

  // 已抵达模组结局：规划器判定玩家的行动命中了某条 endings.when 时后端落的账。
  // 只做提示——结束与否仍由玩家自己投票决定（KP 没有结束模组的权力）。
  const endingReached = (currentSession?.world_state as Record<string, unknown> | undefined)
    ?.ending_reached as { id?: string; name?: string } | undefined

  // 结束模组（全体真人共识）：任一真人发起/同意；全票通过后端置 ended 并广播 status，各端刷新
  // → 成长结算与最终战报入口出现。未满票则更新投票进度提示，待其他玩家确认。
  const voteEndModule = async () => {
    if (!currentSession) return
    try {
      const r = await api.post<{ ended: boolean; vote: EndVoteState }>(
        `/sessions/${currentSession.id}/end-vote`,
        myCharId ? { acting_character_id: myCharId } : {},
      )
      if (r.ended) {
        setEndVote(null); refetchSession()
        toast.success('全体一致同意，本模组已结束，可进行成长结算')
      } else {
        setEndVote(r.vote.open ? r.vote : null)
        toast.success(`已同意结束（${r.vote.agreed_count}/${r.vote.total}），待其他玩家确认`)
      }
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '结束投票失败')
    }
  }
  const cancelEndVote = async () => {
    if (!currentSession) return
    try {
      const r = await api.delete<{ vote: EndVoteState }>(
        `/sessions/${currentSession.id}/end-vote`,
        myCharId ? { acting_character_id: myCharId } : undefined,
      )
      setEndVote(r.vote.open ? r.vote : null)
      toast.success('已撤销结束投票')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '撤销失败')
    }
  }

  // 删除自己本回合尚未推进的暂存发言。
  const deleteEvent = async (id: string) => {
    if (!currentSession) return
    try {
      await api.delete(`/sessions/${currentSession.id}/events/${id}?acting_character_id=${encodeURIComponent(myCharId ?? '')}`)
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  // 保存对暂存发言的改写。
  const saveEdit = async (id: string) => {
    const text = editText.trim()
    if (!currentSession || !text) { setEditingId(null); return }
    try {
      await api.patch(`/sessions/${currentSession.id}/events/${id}`, { content: text, acting_character_id: myCharId })
      setEditingId(null); setEditText('')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '修改失败')
    }
  }

  // 回合确认制：点「推进本回合」——记录本人确认；所有真人都确认后由后端整批交 KP。
  const advanceTurn = async () => {
    if (!currentSession || streaming) return
    try {
      await api.post(`/sessions/${currentSession.id}/advance`, { acting_character_id: myCharId })
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '推进失败')
    }
  }

  // 房主强制推进：跳过未确认者（掉线/挂机），直接交 KP。掉线豁免的兜底出口。
  const forceAdvance = async () => {
    if (!currentSession || streaming) return
    try {
      await api.post(`/sessions/${currentSession.id}/force-advance`, {})
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '强制推进失败')
    }
  }

  // 跳转到某条历史：若未加载则不断向前翻页直到出现，再滚动居中并短暂高亮。
  const jumpToEvent = async (eventId: string) => {
    if (!currentSession) return
    setShowSearch(false)
    const find = () => document.querySelector<HTMLElement>(`[data-mid="${eventId}"]`)
    let el = find()
    let guard = 0
    while (!el && useSessionStore.getState().hasMoreHistory && guard < 80) {
      await loadOlderEvents(currentSession.id)
      await new Promise((res) => requestAnimationFrame(() => res(null)))
      el = find()
      guard++
    }
    if (!el) { toast.error('未能定位到该记录'); return }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('search-hit')
    setTimeout(() => el?.classList.remove('search-hit'), 2200)
  }

  // 玩家点「投骰」：对一个待定检定掷骰。
  const submitRoll = (checkId: string) => {
    if (!currentSession || streaming) {
      if (streaming) toast.error('KP 正在叙事，请稍候')
      return
    }
    setStreaming(true)
    // fire-and-forget（与 sendMessage 同一模式）：骰子结果经 /live 广播回来渲染，
    // 不需要等这个响应。
    //
    // **不能 await**：后端广播骰子之后还要等上一轮 housekeeping 收尾才返回，等最后
    // 一位玩家投完更要接着跑 KP 叙事。await 会让浏览器一直占着一条连接，SSE 推送
    // 排在它后面——表现就是「自己点的骰子，自己这边过好几秒才出动画，别人那边却
    // 立刻就播了」。
    api.post(`/sessions/${currentSession.id}/roll`, { check_id: checkId }).catch((e: unknown) => {
      setStreaming(false)
      toast.error(e instanceof Error ? e.message : '投骰失败')
    })
  }

  const sendMessage = async () => {
    if (!input.trim() || !currentSession || streaming) return
    const text = input.trim()
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'

    // fire-and-forget：不做本地乐观回显，自己的消息同样经 /live 广播回来渲染，
    // 保证与其他成员看到的内容/顺序一致。
    const { inChar } = splitOOC(text)
    const body = { content: text, acting_character_id: myCharId }
    try {
      if (!inChar) {
        await api.post(`/sessions/${currentSession.id}/ooc`, body)
      } else {
        // 回合确认制：发言只进入「本回合暂存」（不进 streaming），等点「推进」且所有真人确认后才交 KP。
        //
        // 独自开团时也照走这一步、**不做发送即推进**：一个人同样会写完想改，暂存期正是唯一能
        // 改的窗口。为省一次点击把它换掉，是拿真实需要的能力去换一个便利，划不来。
        await api.post(`/sessions/${currentSession.id}/chat`, body)
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '发送失败'
      toast.error(msg)
    }
  }

  if (!currentSession) {
    return <div className="flex items-center justify-center h-full text-sm" style={{ color: 'var(--color-text-secondary)' }}>加载中...</div>
  }

  // 分头行动：带 metadata.group 的消息属于某个场景列，无组的是共享主线（全宽）。
  // 出现 ≥2 个场景组时提供分栏（各场景并排，主线内容按时间顺序穿插其间）。
  const sceneGroups: string[] = []
  for (const m of messages) {
    const g = String(m.metadata?.group || '').trim()
    if (g && !sceneGroups.includes(g)) sceneGroups.push(g)
  }
  const splitAvailable = sceneGroups.length >= 2
  const toggleGroup = (g: string) => setHiddenGroups((prev) => {
    const next = new Set(prev)
    if (next.has(g)) next.delete(g); else next.add(g)
    return next
  })

  return (
    <div className="game-session-layout flex h-full gap-4">
      {sceneBackdrop && (
        <div className="scene-backdrop" aria-hidden="true">
          <div className="scene-backdrop__img" style={{ backgroundImage: `url("${sceneBackdrop}")` }} />
        </div>
      )}
      {sceneVeil && <div className="scene-veil" aria-hidden="true" />}
      {battleVeil && <div className="scene-veil" aria-hidden="true" />}
      {/* 沉浸战斗布局：战斗激活时战场占左侧约 2/3（棋盘居中放大、参战卡环绕、动作区钉底），
          聊天整列收成右侧栏（组件不动、仅收窄），完全可用；战斗结束自动回到单列。 */}
      {immersiveOn && combat && (
        <div className="battle-stage-pane flex-1 min-w-0 flex flex-col min-h-0">
          <CombatStage
            combat={combat}
            myCharId={myCharId}
            sessionId={currentSession.id}
            pendingReaction={pendingReaction}
            log={combatLog}
            result={combatResult}
            myWeapons={myWeapons}
            layout="immersive"
            onToggleLayout={() => setBattleLayout('classic')}
          />
        </div>
      )}
      {/* 聊天侧栏折叠后的把手：一颗圆 token（与大厅聊天同一套视觉语言），
          从前是一条 2.4rem 的窄板 + lucide 面板图标，既看不出是什么、也和全站不搭。 */}
      {immersiveOn && chatCollapsed && (
        <div className="side-token-rail">
          <button onClick={toggleChatCollapsed} className="side-token" title="展开聊天侧栏">
            <GiTalk size={19} aria-hidden="true" />
            {chatUnread > 0 && <span className="side-token-dot">{chatUnread > 99 ? '99+' : chatUnread}</span>}
          </button>
        </div>
      )}
      <div
        className={`flex-col relative ${immersiveOn
          ? (chatCollapsed ? 'hidden' : 'flex flex-shrink-0 chat-side-pane')
          : 'flex flex-1 min-w-0'}`}
        style={immersiveOn && !chatCollapsed ? { width: 'clamp(280px, 25vw, 420px)' } : undefined}
      >
        {/* 3D 骰子投掷覆盖层：portal 到 body、fixed 铺满视口，挂载位置不影响呈现 */}
        <DiceRoller ref={diceRollerRef} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pb-2 mb-2 border-b" style={{ borderColor: 'var(--color-border)' }}>
          <button
            onClick={() => navigate('/game')}
            className="btn-secondary btn-sm flex items-center gap-1 flex-shrink-0 whitespace-nowrap"
          >
            <GiReturnArrow /> 返回列表
          </button>
          <span className="text-sm font-semibold whitespace-nowrap" style={{ color: 'var(--color-text-accent)' }}>
            {currentSession.module_title || '游戏中'}
          </span>
          {currentSession.kp_mode === 'human' && (
            <span className="badge text-xs" style={{ color: 'var(--color-text-accent)' }}>真人 KP</span>
          )}
          {currentSession.room_code && (
            <button
              onClick={() => { navigator.clipboard?.writeText(currentSession.room_code || ''); toast.success(`房间码 ${currentSession.room_code} 已复制`) }}
              className="chip chip-btn font-mono flex-shrink-0"
              title="点击复制房间码，分享给队友加入"
            >
              房间码 {currentSession.room_code} <Copy size={11} />
            </button>
          )}
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <ContextUsageBadge sessionId={currentSession.id} refreshKey={messages.length} paused={streaming} />
            <button
              onClick={() => setShowSearch((v) => !v)}
              className="btn-secondary btn-xs flex items-center gap-1"
              title="检索本局历史记录"
            >
              <Search size={12} /> 检索
            </button>
            <button
              onClick={() => setShowRecap(true)}
              className="btn-secondary btn-xs flex items-center gap-1"
              title="战报 / 章节小结：把本局经历浓缩成结构化小结"
            >
              <GiScrollUnfurled size={13} /> 战报
            </button>
            {myCharId && currentSession.status !== 'ended' && (
              endVote?.open ? (
                // 投票进行中：显示进度；本人未同意则给「同意」，任何人可「撤销」。
                <span
                  className="text-xs inline-flex items-center gap-1.5 px-2 py-0.5 rounded border"
                  style={{ borderColor: 'var(--color-accent)', color: 'var(--color-text-secondary)' }}
                  title={endVote.voters.map((v) => `${v.name}：${v.agreed ? '已同意' : '待确认'}`).join('\n')}
                >
                  <GiUpgrade size={13} /> 结束投票 {endVote.agreed_count}/{endVote.total}
                  {!endVote.voters.find((v) => v.character_id === myCharId)?.agreed && (
                    <button onClick={voteEndModule} className="underline"
                      style={{ color: 'var(--color-text-accent)' }}>同意</button>
                  )}
                  <button onClick={cancelEndVote} className="underline"
                    style={{ color: 'var(--color-text-secondary)' }}>撤销</button>
                </span>
              ) : (
                <ConfirmDialog
                  title="结束本模组"
                  description="发起结束投票：需全体真人玩家一致同意，才会把本局标记为已结束（之后可进行成长结算与最终战报，但不再继续跑团）。"
                  confirmLabel="发起结束投票"
                  onConfirm={voteEndModule}
                >
                  {(open) => (
                    <button
                      onClick={open}
                      className={`btn-xs flex items-center gap-1 ${endingReached ? 'btn-primary' : 'btn-secondary'}`}
                      title={endingReached
                        ? `已抵达结局：${endingReached.name || ''}。全体真人玩家一致同意即可结束本局`
                        : '结束本模组：需全体真人玩家一致同意'}
                    >
                      <GiUpgrade size={13} /> {endingReached ? '结束模组（已抵达结局）' : '结束模组'}
                    </button>
                  )}
                </ConfirmDialog>
              )
            )}
            {myCharId && currentSession.status === 'ended' && (
              <button
                onClick={() => setShowGrowth(true)}
                className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
                title="成长结算：本局成功用过的技能做成长检定（模组结束后可用）"
              >
                <GiUpgrade size={13} /> 成长
              </button>
            )}
            <button
              onClick={() => { setConfirmTravel(null); setShowBigMap((v) => !v) }}
              className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
              title="大地图：前往已知地点"
            >
              <GiTreasureMap size={13} /> {showBigMap ? '收起大地图' : '大地图'}
            </button>
            {isHost && (
              <button
                onClick={() => setShowImprov(true)}
                className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
                title="临场角色：把 KP 临时添加的出彩龙套收编为正式配角（房主）"
              >
                <GiCharacter size={13} /> 临场角色
              </button>
            )}
            {isHost && (
              <button
                onClick={() => setShowStyle(true)}
                className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
                title="本局的文风与画风：几档预设或自己写一段，留空则跟随模组（房主）"
              >
                <GiQuillInk size={13} /> 风格
              </button>
            )}
            {!isKp && (
              <button
                onClick={() => setShowCoach('reference')}
                className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
                title="操作速查：怎么读骰子、暗投是什么、怎么申请检定"
              >
                <HelpCircle size={13} />
              </button>
            )}
            {!immersiveOn && !(showPanel && panelChar) && (
              <button
                onClick={() => setShowPanel(true)}
                className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
                title="展开角色卡"
              >
                <PanelRightOpen size={13} />
              </button>
            )}
            {immersiveOn && (
              <button
                onClick={toggleChatCollapsed}
                className="text-xs btn-secondary !px-2 !py-0.5 flex items-center gap-1"
                title="收起聊天侧栏（战场更大；有新消息时侧条会显示未读徽标）"
              >
                <PanelRightClose size={13} />
              </button>
            )}
          </div>
        </div>
        {showRecap && <RecapModal sessionId={currentSession.id} onClose={() => setShowRecap(false)} />}
        {showGrowth && myCharId && (
          <GrowthModal sessionId={currentSession.id} characterId={myCharId} onClose={() => setShowGrowth(false)} />
        )}
        {showImprov && <ImprovisedNpcModal sessionId={currentSession.id} onClose={() => setShowImprov(false)} />}
        {showStyle && (
          <SessionStyleModal
            sessionId={currentSession.id}
            narrativeStyle={currentSession.narrative_style || ''}
            imageStyle={currentSession.image_style || ''}
            onSaved={() => void refetchSession()}
            onClose={() => setShowStyle(false)}
          />
        )}
        {portraitView && (
          // NPC 立绘放大查看：复用通用 Modal（Esc / 点遮罩关闭），点图本身也可关闭
          <Modal onClose={() => setPortraitView(null)} widthClass="max-w-md">
            <img src={portraitView} alt="" className="block w-full cursor-pointer" onClick={() => setPortraitView(null)} />
          </Modal>
        )}
        {showSearch && currentSession && (
          <HistorySearchModal
            sessionId={currentSession.id}
            onClose={() => setShowSearch(false)}
            onJump={jumpToEvent}
          />
        )}
        {currentSession.participants && currentSession.participants.length > 1 && (
          <div className="pb-2 mb-2 border-b" style={{ borderColor: 'var(--color-border)' }}>
            <PartyRoster
              participants={currentSession.participants}
              selectedId={shownCharId}
              onSelect={(id) => { setPanelCharId(id); setShowPanel(true) }}
            />
          </div>
        )}
        {showBigMap && (
          <Modal onClose={() => { setConfirmTravel(null); setShowBigMap(false) }} widthClass="max-w-4xl" padded>
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-semibold inline-flex items-center gap-1" style={{ color: 'var(--color-text-accent)' }}>
                  <GiTreasureMap size={14} /> 大地图 · 前往已知地点
                </span>
                <div className="flex items-center gap-2">
                  <div className="flex items-center rounded-md overflow-hidden" style={{ border: '1px solid var(--color-border)' }}>
                    {([['sandbox', '沙盘'], ['board', '调查板']] as const).map(([key, label]) => (
                      <button key={key} onClick={() => setMapTab(key)}
                        className="px-2 py-0.5 text-xs inline-flex items-center gap-1"
                        style={{
                          background: mapTab === key ? 'var(--color-bg-secondary)' : 'transparent',
                          color: mapTab === key ? 'var(--color-text-accent)' : 'var(--color-text-secondary)',
                        }}>
                        {key === 'sandbox' ? <Hexagon size={11} /> : <GiMagnifyingGlass size={11} />}{label}
                      </button>
                    ))}
                  </div>
                  {canGodView && mapTab === 'sandbox' && (
                    <button onClick={() => setPlayerView((v) => !v)}
                      className="px-2 py-0.5 text-xs inline-flex items-center gap-1 rounded-md"
                      title={playerView ? '当前为玩家视角（迷雾预览）' : '当前为上帝视角（全图）'}
                      style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}>
                      {playerView ? <EyeOff size={11} /> : <Eye size={11} />}
                      {playerView ? '玩家视角' : '上帝视角'}
                    </button>
                  )}
                  <button onClick={() => { setConfirmTravel(null); setShowBigMap(false) }} title="关闭（Esc）" style={{ color: 'var(--color-text-secondary)' }}><X size={16} /></button>
                </div>
              </div>
              {mapTab === 'sandbox' ? (
                <HexSandbox
                  locations={playerSandboxLocations}
                  // 底图相对地址（/api/images/xxx）：本机同源，客人模式要拼房主地址；
                  // 已是绝对地址就原样用（与 sceneBackdrop 同一处理）
                  backdropUrl={sandboxBackdropUrl}
                  disabled={streaming}
                  revealUnknownTokens={canGodView && !playerView}
                  editable={canGodView && !playerView}
                  onMoveScene={dragScene}
                  onPick={(loc) => setConfirmTravel(locations.find((item) => item.id === (loc.sceneId || loc.id)) || null)}
                  height="clamp(320px, 58vh, 560px)" />
              ) : (
                /* 调查板保持「玩家已知」语义：KP 上帝视角只属于沙盘，未知地点不混入线索板 */
                <InvestigationBoard locations={locations.filter((l) => l.known !== false)} disabled={streaming} onPick={setConfirmTravel} height="clamp(320px, 58vh, 560px)" />
              )}
              {confirmTravel ? (
                <div className="mt-2 rounded-md px-3 py-2 text-xs flex items-center gap-3 flex-wrap"
                  style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-accent)' }}>
                  <span style={{ color: 'var(--color-text-primary)' }}>
                    确定前往「{confirmTravel.name}」？{confirmTravel.visited ? '' : '（你尚未去过此地）'}
                  </span>
                  <div className="flex items-center gap-2 ml-auto">
                    <button onClick={() => travelTo(confirmTravel.id)} disabled={streaming}
                      className="btn-primary !px-2.5 !py-1 inline-flex items-center gap-1"
                      style={streaming ? { opacity: 0.5 } : undefined}>
                      <GiReturnArrow size={12} style={{ transform: 'scaleX(-1)' }} /> 确认前往
                    </button>
                    <button onClick={() => setConfirmTravel(null)}
                      className="btn-secondary !px-2.5 !py-1">取消</button>
                  </div>
                </div>
              ) : (
                <p className="text-[11px] mt-2" style={{ color: 'var(--color-text-secondary)', opacity: 0.7 }}>
                  只显示你已知晓的地点；前往后由 KP 叙述抵达见闻。
                </p>
              )}
            </div>
          </Modal>
        )}
        {!liveConnected && (
          <div className="text-center text-xs py-1 mb-1 rounded" style={{ color: 'var(--color-text-secondary)', background: 'var(--color-bg-tertiary)' }}>
            与房间连接中断，正在重连…
          </div>
        )}
        {splitAvailable && (
          <div className="flex items-center gap-1.5 px-1 pb-1 mb-1 text-xs flex-wrap" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <span style={{ color: 'var(--color-text-secondary)' }}>分头行动：</span>
            <button
              onClick={() => setSplitView((v) => !v)}
              className="px-2 py-0.5 rounded border"
              style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-accent)' }}
            >{splitView ? '合并为单列' : '分栏显示'}</button>
            {splitView && sceneGroups.map((g) => {
              const on = !hiddenGroups.has(g)
              return (
                <button key={g} onClick={() => toggleGroup(g)} className="px-2 py-0.5 rounded border"
                  style={{
                    borderColor: on ? 'var(--color-accent)' : 'var(--color-border)',
                    background: on ? 'var(--color-accent)' : 'transparent',
                    color: on ? 'var(--color-on-accent)' : 'var(--color-text-secondary)',
                  }}>{g}</button>
              )
            })}
          </div>
        )}
        <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-auto pb-4 chat-scroll game-info">
          {/* 内容包一层专供 ResizeObserver 量高度：容器自身的盒子不随内容变，观察它量不到
              「流式续写把叙事撑长了」「场景图片刚加载完把下面顶下去了」这两件事。 */}
          <div ref={attachContent}>
          {loadingOlder && (
            <div className="text-center py-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              加载更早的记录...
            </div>
          )}
          {(() => {
          // 是否可增删改：自己本回合尚未推进的暂存发言（action/dialogue + pending_turn + 本人）。
          // 单人局同样适用——一个人也会写完想改，这是唯一能改的窗口。
          const canEditMsg = (m: ChatMessage) =>
            !streaming && !!m.id && (m.type === 'action' || m.type === 'dialogue')
            && !!m.metadata?.pending_turn && m.metadata?.is_player === true
          // 这条消息是否「本轮新到达」（决定是否播一次入场动效）。历史/重连整批为 false。
          // enterIds 已排除流式中的临时 stream-* 消息（其内容逐段增长，动效会与流式节奏打架；
          // 待 done 后 resync 落库为正式 id，那条正式叙事再淡入一次）。
          const isFresh = (m: ChatMessage) => !!m.id && enterIds.has(m.id)
          // NPC 台词错开入场：同一批新到达的 NPC 气泡按出现次序各加 50ms 延迟（最多 4 档，防长队列滞后）。
          const npcStaggerDelay = new Map<string, number>()
          {
            let n = 0
            for (const m of messages) {
              if (m.id && isFresh(m) && m.type === 'dialogue' && !m.metadata?.is_player) {
                npcStaggerDelay.set(m.id, Math.min(n, 4) * 50)
                n++
              }
            }
          }
          // 每条消息外包一层带 data-mid 的容器（供检索跳转定位）；自己的暂存发言叠加编辑/删除。
          const renderRow = (msg: ChatMessage) => {
            if (editingId && editingId === msg.id) {
              return (
                <div key={msg.id} data-mid={msg.id} className="px-3 py-2">
                  <textarea
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    rows={2}
                    autoFocus
                    className="input w-full text-sm"
                    style={{ resize: 'none' }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(msg.id!) }
                      if (e.key === 'Escape') { setEditingId(null); setEditText('') }
                    }}
                  />
                  <div className="flex gap-2 mt-1 justify-end">
                    <button onClick={() => { setEditingId(null); setEditText('') }} className="btn-secondary text-xs !px-2 !py-0.5">取消</button>
                    <button onClick={() => saveEdit(msg.id!)} className="text-xs px-3 py-0.5 rounded font-semibold cursor-pointer" style={{ background: 'var(--color-text-accent)', color: 'var(--color-on-accent)' }}>保存</button>
                  </div>
                </div>
              )
            }
            return (
              <div key={msg.id || `s${msg.sequence_num ?? ''}`} data-mid={msg.id || undefined} className="msg-row relative">
                {renderOne(msg)}
                {canEditMsg(msg) && (
                  <div className="msg-actions absolute top-1 right-1 flex gap-1">
                    <button
                      onClick={() => { setEditingId(msg.id!); setEditText(msg.content) }}
                      title="编辑这条暂存发言"
                      className="p-1 rounded hover:opacity-80"
                      style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={() => deleteEvent(msg.id!)}
                      title="删除这条暂存发言"
                      className="p-1 rounded hover:opacity-80"
                      style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)', color: 'var(--color-danger)' }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                )}
              </div>
            )
          }
          const renderOne = (msg: ChatMessage) => {
            const isPlayer = !!msg.metadata?.is_player
            const showLabel = msg.actor_name && (msg.type === 'dialogue' || msg.type === 'action')
            if (msg.type === 'ooc') {
              return (
                <div key={msg.id} className="chat-msg chat-msg--ooc py-1">
                  <span
                    className="text-xs italic"
                    style={{ color: 'var(--color-text-secondary)', opacity: 0.85 }}
                  >
                    （场外·{msg.actor_name || '玩家'}）{msg.content}
                  </span>
                </div>
              )
            }
            if (msg.type === 'dice') {
              // 3D 投掷进行中：这条骰子正在播动画且尚未放行 → 先隐藏结果卡，落定后再显现。
              // diceAnimating 在 layout effect 里于 paint 前置入，故结果卡首帧即隐藏、绝不先于动画闪现。
              if (msg.id && diceAnimating.has(msg.id) && !revealedDice.has(msg.id)) {
                return <div key={msg.id} className="chat-msg py-1" style={{ minHeight: 0 }} />
              }
              // 对抗卡：命中/反击/闪避这类攻守对抗 → 两边并排 + VS + 高亮胜方（参考 BG3 对抗判定）
              const opposedData = normalizeOpposedData(msg.metadata)
              if (opposedData) {
                return <OpposedCard key={msg.id} data={opposedData}
                         fresh={isFresh(msg)} ts={fmtTime(msg.ts)} />
              }
              // 连射卡：一轮多枪逐发结果
              if (msg.metadata?.combat_burst) {
                return <BurstCard key={msg.id} data={msg.metadata as unknown as BurstData}
                         fresh={isFresh(msg)} ts={fmtTime(msg.ts)} />
              }
              // 暗投/暗骰：结果对玩家隐藏 → 用中性灰、不按成败着色
              const blind = !!msg.metadata?.blind
              const accent = blind
                ? 'var(--color-text-secondary)'
                : diceAccent(String(msg.metadata?.outcome ?? ''))
              // 去掉历史数据里可能残留的旧 🎲 前缀，统一用矢量骰子图标
              const diceText = msg.content.replace(/^🎲\s*/, '')
              // 伤害骰注记（贯穿/燃烧/晕）：贯穿走金色高亮，其余血色
              const diceFlags = ((msg.metadata?.dice as { flags?: string[] } | undefined)?.flags) || []
              // 奖励/惩罚骰标注：本次 check 含额外十位骰时标出（暗投不揭示，避免泄露隐藏结果）。
              const checkCap = (!blind && msg.metadata?.dice)
                ? buildCheckCaption(msg.metadata.dice as DiceSpec) : null
              // 入场动效（仅新到达的骰子卡）：大成功金光脉冲、大失败血红震颤，其余普通弹入；一次不循环。
              const oc = String(msg.metadata?.outcome ?? '')
              let diceAnim = ''
              if (isFresh(msg)) {
                if (!blind && (oc.includes('critical') || oc.includes('大成功'))) diceAnim = 'dice-critical'
                else if (!blind && (oc.includes('fumble') || oc.includes('大失败'))) diceAnim = 'dice-fumble'
                else diceAnim = 'dice-enter'
              }
              // 奖惩骰注记与伤害标志（贯穿/燃烧/晕）——两种卡型共用
              const noteChips = (
                <>
                  {checkCap && (
                    // 理由直接写进标签，不藏在 tooltip 里：奖惩骰实打实地改了成败概率，
                    // 「凭什么」得和「有」一样显眼——触屏上根本没有 hover 这一说。
                    <span className="chip font-semibold"
                      title={`${checkCap.rule}；${checkCap.breakdown} → 结果 ${checkCap.result}`}
                      style={{
                        color: checkCap.kind === 'bonus' ? 'var(--color-dice-gold)' : 'var(--color-dice-fumble)',
                        borderColor: checkCap.kind === 'bonus' ? 'var(--color-dice-gold)' : 'var(--color-dice-fumble)',
                      }}>
                      {checkCap.title}
                      {checkCap.reasons.length > 0 && (
                        <span className="font-normal" style={{ opacity: 0.85 }}>
                          {' · '}{checkCap.reasons.join('、')}
                        </span>
                      )}
                    </span>
                  )}
                  {diceFlags.map((f) => {
                    const gold = f === '贯穿'
                    const col = gold ? 'var(--color-dice-gold)' : 'var(--color-danger)'
                    return (
                      <span key={f} className="chip font-semibold" style={{ color: col, borderColor: col }}>
                        {gold ? '贯穿!' : f}
                      </span>
                    )
                  })}
                </>
              )
              // 元数据齐备（技能检定）→ 结构化读数卡；否则（追逐/自定义骰/旧事件）回落散文行。
              const checkMeta = msg.metadata as CheckResultMeta | undefined
              if (hasCheckReadout(checkMeta)) {
                return (
                  <div key={msg.id} className="chat-msg py-1">
                    <CheckResultCard
                      meta={checkMeta as CheckResultMeta}
                      blind={blind}
                      animClass={diceAnim}
                      chips={noteChips}
                      ts={fmtTime(msg.ts)}
                    />
                  </div>
                )
              }
              return (
                <div key={msg.id} className="chat-msg py-1">
                  <div className={`dice-card rounded-md px-3 py-2 text-sm flex items-start gap-2 ${diceAnim}`}
                    style={{ borderLeft: `3px solid ${accent}`, width: 'fit-content', maxWidth: '100%' }}>
                    <GiRollingDices style={{ color: accent, fontSize: '1.1rem', flexShrink: 0, marginTop: '0.1rem' }} />
                    <span className="whitespace-pre-wrap">{diceText}</span>
                    {noteChips}
                    {fmtTime(msg.ts) && <span className="msg-ts self-end" style={{ flexShrink: 0 }}>{fmtTime(msg.ts)}</span>}
                  </div>
                </div>
              )
            }
            if (msg.type === 'system') {
              // 「要不要去某处」建议卡：KP 主动建议、或玩家嘴上说了要去却没点地图时挂出来。
              // 同意 → 前往进本回合暂存（和大地图点选同一条路）；不同意 → 什么都不发生。
              if (msg.metadata?.kind === 'travel_suggest') {
                const sceneId = String(msg.metadata?.scene_id || '')
                const sceneName = String(msg.metadata?.scene_name || '')
                // 已点过、或人已经到了那儿（自己走过去了）→ 卡片退成一行历史记录
                const done = (msg.id ? handledSuggests.has(msg.id) : false)
                  || currentSession?.current_scene_id === sceneId
                return (
                  <div key={msg.id} className={`chat-msg py-1 flex justify-center ${isFresh(msg) ? 'anim-enter' : ''}`}>
                    <div className="rounded-md px-3 py-2 text-sm flex items-center gap-3 flex-wrap"
                      style={{ background: 'var(--color-bg-tertiary)', borderLeft: '3px solid var(--color-accent)', maxWidth: '100%' }}>
                      <GiTreasureMap style={{ color: 'var(--color-accent)', fontSize: '1.1rem', flexShrink: 0 }} />
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                      {done ? (
                        <span className="text-xs flex-shrink-0" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>已处理</span>
                      ) : (
                        <span className="flex items-center gap-1.5 flex-shrink-0">
                          <button
                            onClick={() => void acceptTravelSuggest(msg.id || '', sceneId, sceneName)}
                            disabled={streaming || !myCharId}
                            className="btn-primary text-xs !px-2.5 !py-1 flex items-center gap-1"
                            style={streaming || !myCharId ? { opacity: 0.5 } : undefined}
                            title="把「前往」加入本回合暂存，和你这一轮的发言一起交给 KP"
                          >
                            <GiTreasureMap size={13} /> 前往
                          </button>
                          <button
                            onClick={() => declineTravelSuggest(msg.id || '')}
                            className="btn-secondary text-xs !px-2.5 !py-1"
                            title="不去（什么都不会发生）"
                          >
                            不去
                          </button>
                        </span>
                      )}
                    </div>
                  </div>
                )
              }
              // 手书卡（Handout）：KP 发放的信件/报纸/日记/便条原文，渲染成信笺样式卡片
              if (msg.metadata?.kind === 'handout') {
                const hk = String(msg.metadata?.handout_kind || '')
                const title = String(msg.metadata?.title || '手书')
                const KindIcon = HANDOUT_ICONS[hk] || GiScrollUnfurled
                const kindLabel = HANDOUT_KIND_LABELS[hk] || '文书'
                // 配图相对 URL（如 /api/images/xxx.jpg）：本机走同源（vite 代理），客人模式拼房主地址
                const handoutImg = String(msg.metadata?.image || '')
                return (
                  <div key={msg.id} className="chat-msg py-2 flex justify-center">
                    <div className="rounded-sm px-5 py-4 max-w-2xl w-full"
                      style={{
                        background: 'linear-gradient(180deg, color-mix(in srgb, var(--color-bg-tertiary) 84%, #b08d57), var(--color-bg-tertiary))',
                        border: '1px solid color-mix(in srgb, var(--color-border) 50%, #b08d57)',
                        boxShadow: 'inset 0 0 0 4px color-mix(in srgb, transparent 88%, #b08d57)',
                      }}>
                      <div className="flex items-center gap-2 pb-2 mb-3"
                        style={{ borderBottom: '1px dashed color-mix(in srgb, var(--color-border) 45%, #b08d57)', color: 'var(--color-text-accent)' }}>
                        <KindIcon style={{ fontSize: '1.2rem', flexShrink: 0 }} />
                        <span className="font-semibold" style={{ fontFamily: HANDOUT_SERIF }}>{title}</span>
                        <span className="text-xs ml-auto flex-shrink-0" style={{ color: 'var(--color-text-secondary)' }}>{kindLabel}</span>
                      </div>
                      {handoutImg && <HandoutImage src={`${getServerUrl()}${handoutImg}`} />}
                      <div className="text-sm whitespace-pre-wrap leading-relaxed"
                        style={{ color: 'var(--color-text-primary)', fontFamily: HANDOUT_SERIF }}>
                        {msg.content}
                      </div>
                      {fmtTime(msg.ts) && (
                        <div className="msg-ts text-right mt-2" style={{ color: 'var(--color-text-secondary)' }}>{fmtTime(msg.ts)}</div>
                      )}
                    </div>
                  </div>
                )
              }
              // 配图卡：场景首入 / 线索发现 / 遭遇战的插画卡片——卡先出，图生成完经 event_patch 补挂淡入
              if (msg.metadata?.kind === 'illustration') {
                const icat = String(msg.metadata?.icat || '')
                const title = String(msg.metadata?.title || '')
                const illustImg = String(msg.metadata?.image || '')
                const IllustIcon = ILLUST_ICONS[icat] || GiScrollUnfurled
                return (
                  <div key={msg.id} className="chat-msg py-2 flex justify-center">
                    <div className="rounded-md px-4 py-3 max-w-2xl w-full"
                      style={{ background: 'var(--surface-2)', border: '1px solid var(--color-border-strong)' }}>
                      <div className="flex items-center gap-2 mb-2" style={{ color: 'var(--color-text-accent)' }}>
                        <IllustIcon style={{ fontSize: '1.1rem', flexShrink: 0 }} />
                        <span className="font-semibold text-sm">{title || msg.content}</span>
                        {ILLUST_LABELS[icat] && (
                          <span className="text-xs ml-auto flex-shrink-0" style={{ color: 'var(--color-text-secondary)' }}>{ILLUST_LABELS[icat]}</span>
                        )}
                      </div>
                      {msg.content && title && (
                        <div className="text-xs mb-2" style={{ color: 'var(--color-text-secondary)' }}>{msg.content}</div>
                      )}
                      {illustImg ? (
                        <LiveModuleImage
                          src={illustImg}
                          moduleId={currentSession.module_id}
                          kind={String(msg.metadata?.image_kind || '')}
                          itemId={String(msg.metadata?.image_item_id || '')}
                          field={String(msg.metadata?.image_field || '')}
                          className="block w-full rounded mb-3"
                          onRegenerated={(url) => msg.id && patchMessageMetadata(msg.id, { image: url })}
                          visualStateKey={String(msg.metadata?.visual_state_key || '') || undefined}
                        />
                      ) : isFresh(msg) ? (
                        // 图片尚未生成完：低调一行小字占位（若最终没图，这行也只在新鲜卡片上出现）
                        <div className="text-xs" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>配图生成中…</div>
                      ) : null}
                    </div>
                  </div>
                )
              }
              // 背景导语卡：开场前展示模组类型/年代/难度等公开元信息 + 一句话前提，给玩家定位
              if (msg.metadata?.kind === 'module_intro') {
                const title = String(msg.metadata?.title || '模组')
                const meta = String(msg.metadata?.meta || '')
                return (
                  <div key={msg.id} className="chat-msg py-2 flex justify-center">
                    <div className="rounded-lg px-4 py-3 max-w-2xl w-full"
                      style={{ background: 'var(--surface-2)', border: '1px solid var(--color-border-strong)' }}>
                      <div className="flex items-center gap-2 mb-1" style={{ color: 'var(--color-text-accent)' }}>
                        <GiScrollUnfurled />
                        <span className="font-semibold">{title}</span>
                      </div>
                      {meta && <div className="text-xs mb-2" style={{ color: 'var(--color-text-secondary)' }}>{meta}</div>}
                      {msg.content && <div className="text-sm whitespace-pre-wrap" style={{ color: 'var(--color-text-primary)' }}>{msg.content}</div>}
                    </div>
                  </div>
                )
              }
              // 待定检定提示：携带 check_request 元数据时，渲染成带「投骰」按钮的卡片
              const checkId = msg.metadata?.check_request ? String(msg.metadata?.id ?? '') : ''
              if (checkId) {
                const pending = (currentSession?.turn_state as Record<string, unknown> | undefined)?.pending_checks as Record<string, unknown> | undefined
                // 权威（pending_checks）∪ 乐观（刚到、尚未 refetch）——消除「已投骰→投骰」闪烁
                const stillPending = (!!pending && checkId in pending) || optimisticPending.has(checkId)
                const mine = !msg.metadata?.char_id || msg.metadata?.char_id === myCharId
                // 待我投骰时呼吸态提示可点；已投/非我则静止。新到达的提示卡再叠一次入场淡入。
                const pendingAnim = stillPending && mine ? 'dice-pending' : ''
                return (
                  <div key={msg.id} className={`chat-msg py-1 flex justify-center ${isFresh(msg) ? 'anim-enter' : ''}`}>
                    <div className={`rounded-md px-3 py-2 text-sm flex items-center gap-3 ${pendingAnim}`}
                      style={{ background: 'var(--color-bg-tertiary)', borderLeft: '3px solid var(--color-accent)', maxWidth: '100%' }}>
                      <GiRollingDices style={{ color: 'var(--color-accent)', fontSize: '1.1rem', flexShrink: 0 }} />
                      <span className="whitespace-pre-wrap">{msg.content}</span>
                      {stillPending && mine && (
                        <button onClick={() => submitRoll(checkId)} disabled={streaming}
                          className="btn-primary text-xs !px-2.5 !py-1 flex items-center gap-1 flex-shrink-0"
                          style={streaming ? { opacity: 0.5 } : undefined}>
                          <GiRollingDices size={13} /> 投骰
                        </button>
                      )}
                      {!stillPending && <span className="text-xs flex-shrink-0" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>已投骰</span>}
                    </div>
                  </div>
                )
              }
              return (
                <div key={msg.id} className="chat-msg py-1 text-center">
                  <span className="inline-block text-xs px-2.5 py-1 rounded whitespace-pre-wrap"
                    style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}>
                    {msg.content}
                  </span>
                </div>
              )
            }
            const kind = showLabel ? actorKind(msg.actor_name, isPlayer) : 'npc'
            // 入场：新到达的对话/行动/叙事淡入上移一次；NPC 台词按次序错开 50ms。
            const fresh = isFresh(msg)
            const staggerMs = msg.id ? npcStaggerDelay.get(msg.id) : undefined
            const enterCls = !fresh ? '' : staggerMs != null ? 'anim-enter-stagger' : 'anim-enter'
            const enterStyle: CSSProperties = staggerMs != null ? { '--enter-delay': `${staggerMs}ms` } as CSSProperties : {}
            return (
              <div key={msg.id} className={`chat-msg chat-msg--${msg.type} ${enterCls}`} style={enterStyle}>
                {showLabel && (
                  <div className={`flex items-center gap-1 ${isPlayer ? 'justify-end chat-actor-player' : 'chat-actor'}`}>
                    {kind !== 'npc' && <SeatIcon kind={kind} size={12} />}
                    {msg.actor_name}
                    {fmtTime(msg.ts) && <span className="msg-ts" style={{ marginLeft: 6 }}>{fmtTime(msg.ts)}</span>}
                  </div>
                )}
                {isPlayer && msg.type === 'dialogue' ? (
                  <div className="chat-player">
                    <span className="chat-bubble-player">{msg.content}</span>
                  </div>
                ) : !isPlayer && msg.type === 'dialogue' ? (
                  // NPC 气泡：有立绘（metadata.portrait，缓存秒挂或生成后 event_patch 补挂）时在气泡旁放小圆头像。
                  // 头像顶对齐气泡，紧跟在名字下面：底对齐时 30px 的头像会被推到气泡底部，
                  // 气泡越长名字离头像越远（单行 18px、四行就 73px），两者看着不像一伙的。
                  <div className="flex items-start gap-2">
                    {msg.metadata?.portrait ? (
                      <LiveModuleImage
                        src={String(msg.metadata.portrait)}
                        moduleId={currentSession.module_id}
                        kind={String(msg.metadata?.image_kind || 'npc')}
                        itemId={String(msg.metadata?.image_item_id || '')}
                        field={String(msg.metadata?.image_field || 'portrait')}
                        alt={msg.actor_name || ''}
                        className="block flex-shrink-0 w-[30px] h-[30px] object-cover rounded-full overflow-hidden cursor-pointer p-0"
                        onClick={() => setPortraitView(`${getServerUrl()}${String(msg.metadata?.portrait)}`)}
                        onRegenerated={(url) => msg.id && patchMessageMetadata(msg.id, { portrait: url })}
                      />
                    ) : null}
                    <span className="chat-bubble-npc" style={{ '--npc-hue': npcHue(msg.actor_name) } as CSSProperties}><InlineMd text={msg.content} /></span>
                  </div>
                ) : msg.type === 'action' ? (
                  <div className={isPlayer ? 'chat-player' : ''}>
                    <span className="chat-bubble-action">{isPlayer ? msg.content : <InlineMd text={msg.content} />}</span>
                  </div>
                ) : msg.type === 'narration' ? (
                  <div className="chat-content markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {stripCommandTags(msg.content)}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <div className="chat-content">
                    <span className="whitespace-pre-wrap">{msg.content}</span>
                  </div>
                )}
              </div>
            )
          }
          // 分头行动：带 group 的消息进入场景列，无组消息是共享主线（全宽）。
          // 按时间顺序把消息切成「主线段（全宽）」与「分栏段（连续的场景组并排）」，
          // 这样每个场景列＝该场景的「玩家行动 + KP 叙事」自成一体，主线穿插其间保序。
          const sceneOf = (m: ChatMessage) => String(m.metadata?.group || '').trim()
          // 战斗机械结算（combat_log）不进主聊天流——它由 CombatStage 的日志抽屉展示。
          const visibleMessages = messages.filter((m) => m.metadata?.combat_log !== true)
          if (!splitView || sceneGroups.length < 2) {
            return visibleMessages.map(renderRow)
          }
          type Seg = { split: boolean; msgs: ChatMessage[] }
          const segments: Seg[] = []
          for (const m of visibleMessages) {
            const isSplit = !!sceneOf(m)
            const last = segments[segments.length - 1]
            if (!last || last.split !== isSplit) segments.push({ split: isSplit, msgs: [m] })
            else last.msgs.push(m)
          }
          return segments.map((seg, i) => {
            if (!seg.split) return <div key={`s${i}`}>{seg.msgs.map(renderRow)}</div>
            const labels: string[] = []
            for (const m of seg.msgs) { const g = sceneOf(m); if (!labels.includes(g)) labels.push(g) }
            const shown = labels.filter((g) => !hiddenGroups.has(g))
            return (
              <div key={`c${i}`} className="flex gap-3 overflow-x-auto items-start my-1">
                {shown.map((g) => (
                  // 每个场景列各自独立滚动：长短不一时互不牵连，可单独翻看某一条线
                  <div key={g} data-scene-col className="flex-1 min-w-[280px] overflow-y-auto chat-scroll pr-1"
                    style={{ borderLeft: '2px solid var(--color-border)', paddingLeft: 10, maxHeight: 'calc(100vh - 230px)' }}>
                    <div className="text-xs font-semibold mb-1 sticky top-0 z-10 py-1"
                      style={{ color: 'var(--color-text-accent)', background: 'var(--color-bg-primary)' }}>
                      {g}
                    </div>
                    {seg.msgs.filter((m) => sceneOf(m) === g).map(renderRow)}
                  </div>
                ))}
              </div>
            )
          })
          })()}
          {streaming && (
            <div className="chat-loading flex items-center gap-2">
              <span className="dot-pulse" />
              {(thinking || tailNote) && (
                <span className="text-xs italic" style={{ color: 'var(--color-text-secondary)' }}>
                  {tailNote || 'KP 正在思考……'}
                </span>
              )}
              {/* 满 5 秒才出现：短回合不必看见秒表，长回合才需要「它还在跑」的凭据 */}
              {waitSec >= 5 && (
                <span className="text-xs tabular-nums" style={{ color: 'var(--color-text-secondary)', opacity: 0.6 }}>
                  {waitSec}s
                </span>
              )}
              {showInterrupt && messages.some((m) => m.type === 'action') && (
                <button
                  onClick={regenerate}
                  title="打断当前生成并用本轮既有输入重新生成（生成卡住时用）"
                  className="ml-2 inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded transition-colors hover:opacity-80"
                  style={{ color: 'var(--color-danger)' }}
                >
                  <RotateCcw size={11} /> 打断并重新生成
                </button>
              )}
            </div>
          )}
          </div>
        </div>

        {combat && !immersiveOn && (
          <CombatStage
            combat={combat} myCharId={myCharId} sessionId={currentSession.id}
            pendingReaction={pendingReaction} log={combatLog} result={combatResult} myWeapons={myWeapons}
            layout="inline"
            onToggleLayout={wideViewport ? () => setBattleLayout('immersive') : undefined}
          />
        )}
        {chase && (
          <ChasePanel chase={chase} sessionId={currentSession.id} />
        )}
        {typingName && (
          <div className="px-3 pb-1 text-xs italic" style={{ color: 'var(--color-text-secondary)' }}>
            {typingName} 正在输入…
          </div>
        )}
        {aiConfigured === false && currentSession.kp_mode !== 'human' && (
          <div
            className="mx-3 mb-1 flex items-center justify-between gap-2 rounded px-3 py-2 text-xs"
            style={{
              background: 'var(--color-bg-tertiary)',
              border: '1px solid var(--color-border-strong)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <span>尚未配置可用的 AI 模型，KP 开场与叙事将无法生成。</span>
            <button
              onClick={() => navigate('/settings')}
              className="btn-secondary !px-2 !py-1"
            >
              去设置
            </button>
          </div>
        )}
        {/* KP 控制台已移到右侧独立栏（见布局末尾），不再内联挤占叙事流的竖向空间。
            真人 KP 不扮演角色：下面这整块（提交行动、重新生成 AI 叙事）与再往下的
            发言框都是玩家侧操作，他走右侧工作台推进，不该同时看到两套入口。 */}
        {!isKp && !streaming && (
          <div className="px-3 pb-1 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button
                onClick={advanceTurn}
                disabled={!!(turnState && myCharId && turnState.confirmed_ids.includes(myCharId))}
                className="text-xs px-3 py-1 rounded font-semibold transition-colors cursor-pointer"
                style={{
                  background: 'var(--color-text-accent)',
                  color: 'var(--color-on-accent)',
                  opacity: (turnState && myCharId && turnState.confirmed_ids.includes(myCharId)) ? 0.5 : 1,
                }}
                title={currentSession.kp_mode === 'human'
                  ? '所有真人都提交后，真人 KP 会收到本回合行动'
                  : soloTable
                    ? '把本回合暂存的发言交给 KP；交出去之前都还能改'
                    : '所有真人都点「推进」后，本回合发言才整批交给 KP'}
              >
                {turnState && myCharId && turnState.confirmed_ids.includes(myCharId)
                  ? (soloTable ? '已提交' : '已提交 · 等待其他人')
                  : currentSession.kp_mode === 'human' ? '提交给 KP' : '推进本回合'}
              </button>
              {/* 「已确认 0/1」对独自开团的人毫无信息量——房里就他一个 */}
              {turnState && turnState.total > 1 && (
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  已确认 {turnState.confirmed_ids.length}/{turnState.total}
                </span>
              )}
              {isHost && turnState && turnState.total > 1 && turnState.confirmed_ids.length < turnState.total && (
                <button
                  onClick={forceAdvance}
                  title="房主强制推进：跳过未确认者（掉线/挂机），直接交 KP"
                  className="text-xs px-2 py-1 rounded transition-colors hover:opacity-80"
                  style={{ color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
                >
                  强制推进
                </button>
              )}
            </div>
            {messages.some((m) => m.type === 'narration') ? (
              <ConfirmDialog
                title="重新生成最新一轮"
                description="将删除最新一轮 KP 的叙事（旁白与 NPC 台词），用本轮玩家与队友的既有输入重新生成；已投出的骰子结果会保留、不重掷。此操作会打断当前生成、可能明显改变剧情走向——仅在生成卡住或结果明显有问题时使用。"
                confirmLabel="重新生成"
                onConfirm={regenerate}
              >
                {(open) => (
                  <button
                    onClick={open}
                    title="重新生成最新一轮 KP 叙事（高风险，慎用）"
                    className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors hover:opacity-80"
                    style={{ color: 'var(--color-text-secondary)' }}
                  >
                    <RotateCcw size={12} /> 重新生成
                  </button>
                )}
              </ConfirmDialog>
            ) : (
              // 尚无任何 KP 叙事（开场未成功 / 刷新后 state 丢失）→ 提供开场重入口
              <button
                onClick={retryOpening}
                title="（重新）生成开场叙事"
                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors hover:opacity-80"
                style={{ color: 'var(--color-text-accent)' }}
              >
                <RotateCcw size={12} /> 重试开场
              </button>
            )}
          </div>
        )}
        {/* 跳到最新：只在用户离开底部时出现；期间来了新内容则改为高亮提示。
            贴底时整颗按钮不渲染，不占位、也不挡叙事。 */}
        {!atBottom && (
          <button
            type="button"
            onClick={jumpToLatest}
            className={`jump-latest ${hasNewBelow ? 'jump-latest--new' : ''}`}
            title={hasNewBelow ? '下面有新内容，点击跳转' : '回到最新内容'}
          >
            <ChevronDown size={14} />
            {hasNewBelow ? '有新内容' : '最新'}
          </button>
        )}
        {isKp ? (
          <div className="chat-input-bar chat-input-bar--kp">
            用右侧 KP 工作台推进本回合。你是 KP，不占玩家席，因此没有发言框。
          </div>
        ) : (
        <div className="chat-input-bar">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
              // 节流上报"正在输入"给同房间其他人
              const now = Date.now()
              if (currentSession && e.target.value && now - lastTypingSent.current > 2000) {
                lastTypingSent.current = now
                api.post(`/sessions/${currentSession.id}/typing`).catch(() => {})
              }
            }}
            onCompositionStart={() => { composingRef.current = true }}
            onCompositionEnd={() => { composingRef.current = false }}
            onKeyDown={(e) => {
              if (composingRef.current || e.nativeEvent.isComposing) return
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                sendMessage()
              }
            }}
            placeholder={streaming
              ? 'KP 叙事中，可先打草稿，稍后发送…'
              : '输入行动；用「」或""括住要说出口的台词，（圆括号）内为场外'}
            className="input flex-1"
            rows={1}
            style={{ resize: 'none' }}
          />
          <button onClick={sendMessage} disabled={streaming || !input.trim()} className="btn-primary">
            发送
          </button>
        </div>
        )}
      </div>

      {/* 角色卡侧栏：沉浸战斗布局下暂时隐藏（参战卡已带 HP/状态，屏幕让给战场与聊天）。
          窄屏改为覆盖式抽屉 —— 此前窄屏直接 display:none，玩家在手机上完全看不到
          自己的 HP/SAN/技能/道具，「展开角色卡」按钮点了也没反应。 */}
      {!immersiveOn && showPanel && panelChar && (
        <div
          className="char-panel-scrim"
          onClick={() => setShowPanel(false)}
          aria-hidden="true"
        />
      )}
      {!immersiveOn && showPanel && panelChar && (
        <aside
          className="game-character-panel w-64 flex-shrink-0 border-l overflow-y-auto game-info"
          style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}
        >
          <div
            className="flex items-center justify-between px-3 py-1.5 text-xs border-b"
            style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
          >
            <span className="inline-flex items-center gap-1">
              {shownCharId !== myCharId ? (<><Bot size={12} /> 其他角色卡</>) : '角色卡'}
            </span>
            <span className="inline-flex items-center gap-1">
              {shownCharId !== myCharId && (
                <button
                  onClick={() => setPanelCharId(null)}
                  className="btn-secondary !px-2 !py-0.5"
                >
                  看我的角色
                </button>
              )}
              <button
                onClick={() => setShowPanel(false)}
                title="收起角色卡"
                className="p-0.5 rounded hover:opacity-80"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                <PanelRightClose size={15} />
              </button>
            </span>
          </div>
          <CharacterPanel
            character={panelChar}
            sessionId={currentSession.id}
            refreshKey={refreshTick}
            onSkillCheck={shownCharId === myCharId ? rollCheck : undefined}
            inventoryActions={shownCharId === myCharId && myCharId ? {
              sessionId: currentSession.id,
              charId: myCharId,
              teammates: (currentSession.participants || [])
                .filter((p) => p.character_id && p.character_id !== myCharId)
                .map((p) => ({ id: p.character_id as string, name: p.character_name || '队友' })),
            } : undefined}
          />
        </aside>
      )}

      {/* KP 控制台侧栏：与叙事流并列而非争抢竖向空间。
          收起后留一颗圆 token（与聊天侧栏同一套交互与视觉），偏好存 localStorage。 */}
      {isKp && kpCollapsed && (
        <div className="side-token-rail">
          <button
            onClick={() => setKpCollapsed(false)}
            className="side-token"
            title="展开 KP 控制台"
          >
            <GiTheaterCurtains size={19} aria-hidden="true" />
            <span className="side-token-label">KP</span>
          </button>
        </div>
      )}
      {showCoach && (
        <OnboardingCoach
          startAtReference={showCoach === 'reference'}
          onClose={() => setShowCoach(false)}
        />
      )}
      {isKp && !kpCollapsed && (
        <aside className="kp-console-pane flex-shrink-0">
          <HumanKpPanel
            sessionId={currentSession.id}
            turnReady={turnState?.ready === true}
            variant="sidebar"
            onCollapse={() => setKpCollapsed(true)}
          />
        </aside>
      )}
    </div>
  )
}
