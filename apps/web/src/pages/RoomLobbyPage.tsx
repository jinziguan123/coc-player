import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useSyncBackOnVisit } from '@/features/characters/useSyncBack'
import { api, connectSSE, getServerUrl, localApi } from '../api/client'
import type { SessionParticipant } from '../stores/sessionStore'
import { CharacterPanel } from '../components/character/CharacterPanel'
import { CharacterPortrait } from '../components/character/CharacterPortrait'
import { SeatIcon, seatKind } from '../components/game/SeatIcon'
import { LobbyChatDock, type ChatLine } from '../components/game/LobbyChatDock'
import { AiTeammateDialog } from '../components/game/AiTeammateDialog'
import { QuickCharacterCreateDialog } from '../components/game/QuickCharacterCreateDialog'
import {
  CharacterSelectStage,
  type CharacterPick,
  type LobbyCharacter,
} from '../components/game/CharacterSelectStage'
import { CharacterGuidanceCard } from '../components/module/CharacterGuidanceCard'
import { hasGuidance, type CharacterGuidance } from '@/stores/moduleStore'
import { GiReturnArrow } from 'react-icons/gi'
import { parsePlayerRange } from '@/lib/module'
import { ageOf, occupationOf, topSkills, vitalOf } from '@/lib/characterDigest'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Copy, Sparkles, Check, Eye, ScanSearch, Trash2, UserPlus } from 'lucide-react'

type Character = LobbyCharacter

interface RoomData {
  id: string
  module_id: string
  status: string
  room_code?: string | null
  module_title?: string
  kp_mode?: 'ai' | 'human'
  identity_version?: number
  participants: SessionParticipant[]
}

/** 大厅聊天最多留这么多条：房间挂一晚上，DOM 不能跟着无限涨。 */
const MAX_CHAT_LINES = 200

interface CharacterEvaluation {
  compatible: boolean
  warnings: string[]
  suggestions: string[]
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Chunk = { type: string; id?: string; content?: string; actor_name?: string }

export function RoomLobbyPage() {
  // 补漏：上次可能是异常退出（关窗口/掉线/房主先退），来不及同步。
  useSyncBackOnVisit()
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [room, setRoom] = useState<RoomData | null>(null)
  const [moduleDesc, setModuleDesc] = useState('')
  /** 本模组的车卡建议：挑角色/写生成提示词时都要看它，此前只挂在角色页上。 */
  const [guidance, setGuidance] = useState<CharacterGuidance | null>(null)
  /** 本模组推荐的玩家人数区间；模组未标注时 parsePlayerRange 给 1–6 的默认档。 */
  const [seatRange, setSeatRange] = useState<{ min: number; max: number }>({ min: 1, max: 6 })
  /** 房间里可选的全部角色卡（我名下的 + 无主的，且没被别的会话占用）。
      不再按 is_player 分成「调查员池 / 队友池」——一张卡给真人演还是给 AI 演，
      是**席位**的事，不是卡的属性。 */
  const [myChars, setMyChars] = useState<Character[]>([])
  const [localChars, setLocalChars] = useState<Character[]>([])
  /** 角色库搜索词：卡多了得能找，而不是滚半天。只在超过 6 张时才露出输入框。 */
  const [charFilter, setCharFilter] = useState('')
  const [chat, setChat] = useState<ChatLine[]>([])
  const [busy, setBusy] = useState(false)
  /** 「快速创建角色卡」对话框：复用角色页的编辑弹窗，从一张空白草稿开始。 */
  const [quickCreateOpen, setQuickCreateOpen] = useState(false)
  const [quickDraft, setQuickDraft] = useState<Character | null>(null)
  const [quickCreating, setQuickCreating] = useState(false)
  /** 同一个对话框，为「我自己这一席」打开：生成的是玩家调查员，确认后走认领。 */
  const [genSelf, setGenSelf] = useState(false)
  const [panelChar, setPanelChar] = useState<Character | null>(null)
  /**
   * 正在预览、尚未入座的候选角色。
   *
   * 从前点一下角色 chip 就**直接入座**了——玩家只看到一个名字，卡上有什么技能、什么职业、
   * 什么背景一概不知，等于盲选。现在点 chip 只是把它放到右栏看，确认之后才真的坐下。
   * ``source`` 决定确认走哪条路：本机角色要先同步一份副本给房主（见 importAndClaim）。
   */
  const [preview, setPreview] = useState<CharacterPick | null>(null)
  const [evaluation, setEvaluation] = useState<CharacterEvaluation | null>(null)
  const [evaluating, setEvaluating] = useState(false)
  const [typingName, setTypingName] = useState('')
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSent = useRef(0)
  const myNameRef = useRef<string | null>(null)
  const myPlayerSeat = room?.participants.find((p) => p.is_mine && p.role !== 'kp') ?? null
  const myKpSeat = room?.participants.find((p) => p.is_mine && p.role === 'kp') ?? null
  const myChatSeat = room?.participants.find(
    (p) => p.is_mine && (p.role === 'human' || p.role === 'kp'),
  ) ?? null
  myNameRef.current = myPlayerSeat?.character_name ?? null

  const mySeat = myPlayerSeat
  const needsCharacter = !!mySeat && mySeat.role === 'human' && !mySeat.character_id
  // 已入座的人点「更换角色」后，重新回到选人舞台。开局后不再允许换：
  // 消息与战斗状态都绑着角色，后端也会拒绝。
  const [changingChar, setChangingChar] = useState(false)
  // 旧 human-KP 房间可能让同一 token 同时拥有 KP/玩家席，保留玩家操作区，避免升级后失去权限。
  const strictKpIdentity = !!myKpSeat && (room?.identity_version ?? 1) >= 2
  const amHost = !!room?.participants.some((p) => p.is_host && p.is_mine)

  const gaps = useCallback((r: RoomData): string[] => {
    const out: string[] = []
    const playerSeats = r.participants.filter((p) => p.role !== 'kp')
    const empty = r.kp_mode === 'human' && r.identity_version && r.identity_version >= 2
      ? playerSeats.filter((p) => p.role === 'ai' && !p.character_id).length
      : playerSeats.filter((p) => !p.character_id).length
    if (empty) out.push(`还有 ${empty} 个空席未填角色`)
    const notReady = playerSeats.filter((p) => p.character_id && p.role === 'human' && !p.ready).length
    if (notReady) out.push(`还有 ${notReady} 名玩家未准备`)
    if (!playerSeats.some((p) => p.role === 'human' && p.character_id)) {
      // 真人 KP 新模型：KP 即真人，玩家席可全 AI——只要求至少 1 个已入座角色（与后端一致）
      if (r.kp_mode === 'human' && (r.identity_version ?? 1) >= 2) {
        if (!playerSeats.some((p) => p.character_id)) out.push('至少需要 1 个已入座的角色（真人认领或 AI 队友）')
      } else {
        out.push('至少需要 1 名真人玩家')
      }
    }
    return out
  }, [])

  const refreshRoom = useCallback(async () => {
    if (!sessionId) return
    const r = await api.get<RoomData>(`/sessions/${sessionId}`)
    if (r.status === 'active') { navigate(`/game/${sessionId}`, { replace: true }); return }
    setRoom(r)
  }, [sessionId, navigate])

  /**
   * 重新拉取可选角色池。
   *
   * `?available=true` 是**服务端**按「当前是否已被某个会话占用」算的，所以它一坐一退都会变。
   * 从前这三个池子只在 init 里拉一次：把林知微指派给 2 号 AI 席、刷新页面、再删掉那个席位，
   * 她已经空出来了，可 3/4 号席的下拉里没有她——因为那份列表是刷新那一刻拉的，那时她还占着座。
   * 再刷一次页面又冒出来，正是「只在挂载时拉一次」的典型症状。
   */
  const refreshCharPools = useCallback(async () => {
    try {
      setMyChars(await api.get<Character[]>('/characters?available=true&mine=true'))
    } catch { /* 拉不到就维持现状，不影响房间其余功能 */ }
    if (getServerUrl()) {
      try {
        setLocalChars(await localApi.get<Character[]>('/characters?available=true&mine=true'))
      } catch { /* 本机后端不可用时仍可使用房主主机上的角色 */ }
    }
  }, [])

  useEffect(() => {
    if (!sessionId) return
    const ac = new AbortController()
    let cancelled = false

    const handleChunk = (c: Chunk) => {
      if (c.type === 'started') { navigate(`/game/${sessionId}`, { replace: true }); return }
      if (c.type === 'lobby' || c.type === 'seat' || c.type === 'presence') {
        void refreshRoom()
        // 席位一动，「谁还空着」就变了——别人入座/退座同样要反映到我这边的候选里。
        // presence 只是在线心跳，不影响占用，不必跟着刷。
        if (c.type !== 'presence') void refreshCharPools()
        return
      }
      if (c.type === 'typing') {
        if (c.actor_name && c.actor_name !== myNameRef.current) {
          setTypingName(c.actor_name)
          if (typingTimer.current) clearTimeout(typingTimer.current)
          typingTimer.current = setTimeout(() => setTypingName(''), 3000)
        }
        return
      }
      if (c.type === 'ooc') {
        setChat((prev) => prev.some((x) => x.id === c.id)
          ? prev
          : [...prev, { id: c.id || `${Date.now()}`, name: c.actor_name || '玩家', content: c.content || '' }]
            .slice(-MAX_CHAT_LINES))
      }
    }

    const init = async () => {
      const r = await api.get<RoomData>(`/sessions/${sessionId}`)
      if (cancelled) return
      if (r.status === 'active') { navigate(`/game/${sessionId}`, { replace: true }); return }
      // 通过房间码进入后，先把 token 写入一个真人席；这样切换页面仍能在游戏列表中找到房间。
      // 若 KP 席也空着，先让用户选择加入身份；否则默认按真人玩家预留席位。
      const hasOpenKpSeat = r.participants.some((p) => p.role === 'kp' && !p.claimed)
      const joined = r.status === 'setup' && !hasOpenKpSeat
        ? await api.post<RoomData>(`/sessions/${sessionId}/join`)
        : r
      if (cancelled) return
      setRoom(joined)
      const mods = await api.get<{
        id: string; description: string; world_setting?: Record<string, unknown> | null
        character_guidance?: CharacterGuidance | null
      }[]>('/modules')
      if (cancelled) return
      const mod = mods.find((m) => m.id === joined.module_id)
      setModuleDesc(mod?.description || '')
      setGuidance(mod?.character_guidance ?? null)
      // 座位数按本模组的推荐人数约束——模组既然已经选定了，人数就不该再是任意的。
      setSeatRange(parsePlayerRange(mod?.world_setting))
      await refreshCharPools()
      if (cancelled) return
      const ev = await api.get<{ events: { id: string; event_type: string; actor_name: string; content: string }[] }>(`/sessions/${sessionId}/events`)
      if (cancelled) return
      setChat(ev.events
        .filter((e) => e.event_type === 'ooc')
        .map((e) => ({ id: e.id, name: e.actor_name, content: e.content }))
        .slice(-MAX_CHAT_LINES))

      while (!cancelled) {
        try {
          for await (const chunk of connectSSE(`/sessions/${sessionId}/live`, ac.signal)) {
            if (cancelled) break
            handleChunk(chunk as Chunk)
          }
        } catch { /* dropped */ }
        if (cancelled) break
        await new Promise((res) => setTimeout(res, 1500))
      }
    }
    init().catch(() => navigate('/game', { replace: true }))
    return () => { cancelled = true; ac.abort() }
  }, [sessionId, navigate, refreshRoom, refreshCharPools])

  const claimWithChar = async (charId: string, notify = true): Promise<string | null> => {
    if (!room) return '房间状态尚未加载'
    // 已经有自己的玩家席就用它——无论是补选角色还是换个角色。此前这里要求
    // `!mySeat.character_id`，于是选过一次之后就只会去找空席，坐满了就再也换不了。
    const seat = (mySeat && mySeat.role === 'human')
      ? mySeat
      : room.participants.find((p) => p.role === 'human' && !p.character_id && !p.claimed)
    if (!seat) {
      const message = '房间已满，没有空席'
      if (notify) toast.error(message)
      setBusy(false)
      return message
    }
    setBusy(true)
    try {
      await api.post(`/sessions/${room.id}/claim`, { seat_order: seat.seat_order, character_id: charId })
      await refreshRoom()
      setChangingChar(false)
      return null
    } catch (e) {
      const message = e instanceof Error ? e.message : '入座失败'
      if (notify) toast.error(message)
      return message
    } finally { setBusy(false) }
  }

  const importAndClaim = async (character: Character) => {
    if (!room) return
    setBusy(true)
    try {
      const imported = await api.post<Character>('/characters', {
        name: character.name,
        module_id: room.module_id,
        // 记住血缘：本局的 HP/SAN/成长/物品只落在这份副本上，客人靠这个 id
        // 把结果写回自己库里的原件，见 features/characters/syncBack.ts。
        origin_character_id: character.id,
        rule_system: character.rule_system || 'coc',
        base_attributes: character.base_attributes,
        skills: character.skills,
        system_data: character.system_data,
        backstory: character.backstory,
      })
      const claimError = await claimWithChar(imported.id, false)
      if (claimError) {
        setMyChars((prev) => prev.some((c) => c.id === imported.id) ? prev : [...prev, imported])
        toast.error(`角色已同步给房主，但入座失败：${claimError}。可在上方角色列表里重试`)
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '同步角色到房主失败')
    } finally { setBusy(false) }
  }

  const claimKp = async () => {
    if (!room) return
    const seat = room.participants.find((p) => p.role === 'kp' && !p.claimed)
    if (!seat) { toast.error('没有空的 KP 席位'); return }
    setBusy(true)
    try {
      await api.post(`/sessions/${room.id}/claim`, { seat_order: seat.seat_order, character_id: null })
      await refreshRoom()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加入 KP 席失败')
    } finally { setBusy(false) }
  }

  const toggleReady = async () => {
    if (!room || !mySeat?.character_id) return
    try {
      await api.post(`/sessions/${room.id}/ready`, { ready: !mySeat.ready })
      await refreshRoom()
    } catch (e) { toast.error(e instanceof Error ? e.message : '操作失败') }
  }

  const startGame = async () => {
    if (!room) return
    setBusy(true)
    try {
      await api.post(`/sessions/${room.id}/start`)
      navigate(`/game/${room.id}`, { replace: true })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '开始失败')
      setBusy(false)
    }
  }

  const sendChat = async (text: string) => {
    if (!room || !myChatSeat) return
    try {
      await api.post(`/sessions/${room.id}/ooc`, {
        content: text,
        ...(myChatSeat.character_id ? { acting_character_id: myChatSeat.character_id } : {}),
      })
    } catch (e) { toast.error(e instanceof Error ? e.message : '发送失败') }
  }

  /** 「正在输入」广播：每 2 秒最多一次，别把敲键盘变成刷接口。 */
  const notifyTyping = () => {
    if (!room || !myChatSeat) return
    const now = Date.now()
    if (now - lastTypingSent.current > 2000) {
      lastTypingSent.current = now
      api.post(`/sessions/${room.id}/typing`).catch(() => {})
    }
  }

  const kick = async (seatOrder: number) => {
    if (!room) return
    setBusy(true)
    try {
      const updated = await api.post<RoomData>(`/sessions/${room.id}/kick/${seatOrder}`)
      setRoom(updated)
      await refreshCharPools()
      toast.success('玩家已移出房间')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '移出失败')
    } finally { setBusy(false) }
  }

  /* ── 座位管理：人数与 AI 队友都在房间里配 ──
     建房那一屏只回答「跑哪个本子、谁当 KP」，其余全在这里。 */

  const addSeat = async (role: 'ai' | 'human') => {
    if (!room) return
    setBusy(true)
    try {
      setRoom(await api.post<RoomData>(`/sessions/${room.id}/seats`, { role }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '添加席位失败')
    } finally { setBusy(false) }
  }

  const removeSeat = async (seatOrder: number) => {
    if (!room) return
    setBusy(true)
    try {
      setRoom(await api.delete<RoomData>(`/sessions/${room.id}/seats/${seatOrder}`))
      // 删座位会把那张卡放回候选池——不刷新的话它在其余席位的下拉里就此消失
      await refreshCharPools()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除席位失败')
    } finally { setBusy(false) }
  }

  const assignAiChar = async (seatOrder: number, characterId: string | null) => {
    if (!room) return
    setBusy(true)
    try {
      setRoom(await api.post<RoomData>(
        `/sessions/${room.id}/seats/${seatOrder}/character`, { character_id: characterId },
      ))
      // 指派占用一张、清空释放一张，两个方向都要让其余席位的下拉跟上
      await refreshCharPools()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '指派角色失败')
    } finally { setBusy(false) }
  }

  const viewSeat = async (charId: string | null) => {
    if (!charId) return
    try {
      setEvaluation(null)
      setPanelChar(await api.get<Character>(`/characters/${charId}`))
    } catch { /* ignore */ }
  }

  const evaluateSeat = async (charId: string | null) => {
    if (!charId || !room) return
    setEvaluating(true)
    try {
      const character = await api.get<Character>(`/characters/${charId}`)
      setPanelChar(character)
      const systemData = character.system_data || {}
      const result = await api.post<CharacterEvaluation>('/characters/evaluate', {
        module_id: room.module_id,
        name: character.name,
        occupation: String(systemData.occupation || systemData.profession || ''),
        backstory: character.backstory,
      })
      setEvaluation(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '角色适配评估失败')
    } finally { setEvaluating(false) }
  }

  /** 角色列表里的一张卡：头像首字 + 姓名 + 职业/年龄 + HP/SAN + 最擅长的几项技能。
      从前这里只有一颗写着名字的药丸，看不出这是「角色列表」，更判断不了该选谁。 */
  const renderCharCard = (c: Character, source: 'mine' | 'local') => {
    const on = preview?.char.id === c.id
    const meta = [occupationOf(c), ageOf(c)].filter(Boolean).join(' · ')
    const hp = vitalOf(c, 'hitPoints')
    const san = vitalOf(c, 'sanity')
    return (
      <button
        key={`${source}-${c.id}`}
        onClick={() => setPreview({ char: c, source })}
        disabled={busy}
        className={`char-card${on ? ' char-card--on' : ''}`}
        title={source === 'local'
          ? '点击查看这张卡。确认入座时会同步一份副本给房主，不会进入他的角色库'
          : '点击查看这张卡，确认后再入座'}
      >
        <span className="char-card-avatar" aria-hidden="true">{c.name.slice(0, 1)}</span>
        <span className="char-card-body">
          <span className="char-card-name">{c.name}</span>
          {meta && <span className="char-card-meta">{meta}</span>}
          {(hp || san) && (
            <span className="char-card-vitals">
              {hp && <span>HP {hp}</span>}
              {san && <span>SAN {san}</span>}
            </span>
          )}
          {topSkills(c).length > 0 && (
            <span className="char-card-skills">{topSkills(c).join(' · ')}</span>
          )}
        </span>
      </button>
    )
  }

  /** 按姓名或职业筛角色卡；搜索词为空时原样返回。 */
  const matchChars = (list: Character[]) => {
    const q = charFilter.trim().toLowerCase()
    if (!q) return list
    return list.filter((c) =>
      c.name.toLowerCase().includes(q) || occupationOf(c).toLowerCase().includes(q))
  }

  const copyCode = () => {
    if (room?.room_code) { navigator.clipboard?.writeText(room.room_code); toast.success('房间码已复制') }
  }

  if (!room) {
    return <div className="flex items-center justify-center h-full text-sm" style={{ color: 'var(--color-text-secondary)' }}>加载房间中…</div>
  }

  const seatGaps = gaps(room)
  const seats = room.participants.filter((p) => p.role !== 'kp').sort((a, b) => a.seat_order - b.seat_order)
  const kpSeats = room.participants.filter((p) => p.role === 'kp').sort((a, b) => a.seat_order - b.seat_order)
  const openKpSeat = kpSeats.find((p) => !p.claimed)
  /** 真人玩家正在挑/换角色时，整页进入「选人舞台」而不是在席位卡底部塞一排小卡。 */
  const selectingCharacter = !strictKpIdentity && (!mySeat || needsCharacter || changingChar)
  const asideOpen = selectingCharacter || !!preview || !!panelChar

  const confirmPreview = () => {
    if (!preview) return
    const { char, source } = preview
    setPreview(null)
    if (source === 'local') void importAndClaim(char)
    else void claimWithChar(char.id)
  }

  /** 右侧档案抬头用的摘要——比完整面板先回答「TA 是谁、还能不能打」。 */
  const previewDigest = preview
    ? {
        meta: [occupationOf(preview.char), ageOf(preview.char)].filter(Boolean).join(' · '),
        hp: vitalOf(preview.char, 'hitPoints'),
        san: vitalOf(preview.char, 'sanity'),
        skills: topSkills(preview.char),
      }
    : null

  /** 快速创建：草稿由页面在点击时创建（不在弹窗 effect 里发请求，StrictMode 会双跑）。 */
  const openQuickCreate = async () => {
    if (!room) return
    setQuickDraft(null)
    setQuickCreateOpen(true)
    setQuickCreating(true)
    try {
      // 固定落在本机角色库；连主机时新卡作为「本机角色」参与候选，入座时再同步副本。
      const created = await localApi.post<Character>('/characters', {
        name: '新调查员',
        module_id: getServerUrl() ? null : room.module_id,
        rule_system: 'coc',
        base_attributes: {},
        skills: {},
        system_data: {},
        backstory: '',
      })
      setQuickDraft(created)
    } catch (e) {
      setQuickCreateOpen(false)
      toast.error(e instanceof Error ? e.message : '创建角色草稿失败')
    } finally {
      setQuickCreating(false)
    }
  }

  const closeQuickCreate = () => {
    setQuickCreateOpen(false)
    setQuickDraft(null)
    setQuickCreating(false)
  }

  /** 快速创建的卡保存后：刷新候选池，并直接放进右侧预览等待入座。 */
  const handleQuickCreated = async (characterId: string) => {
    closeQuickCreate()
    // 快速创建固定落在本机角色库：连主机时它是「本机角色」，确认入座再同步副本；
    // 本机直连时它就是房主自己的卡，直接走 mine 池。
    const remote = !!getServerUrl()
    try {
      const full = await (remote ? localApi : api).get<Character>(`/characters/${characterId}`)
      await refreshCharPools()
      setEvaluation(null)
      setPreview({ char: full, source: remote ? 'local' : 'mine' })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '读取新建角色失败')
    }
  }

  return (
    // 整页锁在视口高度里：头部（返回/房间码）和底部（开始游戏）钉住，只有中段滚。
    // 从前这一层没有 min-h-0、中段也没有 overflow，内容一路外溢到 AppShell 的 main 去滚，
    // 于是角色卡一多，「开始游戏」就被顶到折线以下（实测已入座时内容 793px > 视口 720px）。
    // relative：聊天是浮层（absolute 右侧居中），它的定位原点就是这一层
    <div className="relative flex h-full min-h-0 gap-0">
      <div className="flex flex-col flex-1 min-w-0 min-h-0 max-w-3xl mx-auto w-full">
        <div className="flex flex-shrink-0 items-center gap-3 pb-2 mb-3 border-b" style={{ borderColor: 'var(--color-border)' }}>
          <button onClick={() => navigate('/game')} className="btn-secondary flex items-center gap-1 !px-2 !py-1 text-sm">
            <GiReturnArrow /> 返回
          </button>
          <span className="text-sm font-semibold" style={{ color: 'var(--color-text-accent)' }}>
            房间大厅 · {room.module_title || '未知模组'}
            {room.kp_mode === 'human' && <span className="badge ml-2 text-xs">真人 KP</span>}
          </span>
          {room.room_code && (
            <button onClick={copyCode} className="ml-auto badge shrink-0" title="点击复制房间码">
              <span className="inline-flex items-center gap-1 whitespace-nowrap">
                房间码 {room.room_code} <Copy size={11} />
              </span>
            </button>
          )}
        </div>

        {/* 可滚中段：选人舞台置顶，其次才是模组简介与席位管理。
            真人玩家在挑角色时，第一眼看到的是「选人界面」，而不是埋在席位卡底部的表单。 */}
        <div className="flex-1 min-h-0 overflow-y-auto pr-1">
          {selectingCharacter && (
            <CharacterSelectStage
              mine={myChars}
              local={localChars}
              preview={preview}
              busy={busy}
              changingChar={changingChar}
              currentCharName={mySeat?.character_name}
              moduleTitle={room.module_title}
              guidance={guidance}
              allowKpClaim={!!openKpSeat}
              onPick={(char, source) => {
                setEvaluation(null)
                setPreview({ char, source })
              }}
              onGenerate={() => setGenSelf(true)}
              onQuickCreate={() => void openQuickCreate()}
              onClaimKp={() => void claimKp()}
              onCancelChange={() => setChangingChar(false)}
            />
          )}
          {moduleDesc && (
            <div className="card mb-3">
              <h3 className="card-title">模组简介</h3>
              <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>{moduleDesc}</p>
            </div>
          )}

          {/* 席位 */}
          <div className={`card mb-3${selectingCharacter ? ' seat-card--selecting' : ''}`}>
            <h3 className="card-title">玩家席位（{seats.filter((s) => s.character_id).length}/{seats.length}）</h3>
            {kpSeats.length > 0 && (
              <div className="mb-2 space-y-1.5">
                {kpSeats.map((p) => (
                  <div key={p.seat_order} className="flex items-center gap-2 px-2 py-1.5 rounded" style={{ background: 'var(--surface-2)' }}>
                    <SeatIcon kind={p.is_host ? 'host' : 'human'} size={15} />
                    <span className="text-sm flex-1" style={{ color: p.is_mine ? 'var(--color-text-accent)' : 'var(--color-text-primary)' }}>
                      {p.claimed ? '真人 KP' : '空席 · 等待真人 KP 加入'}
                      {p.is_host ? ' · 房主' : ''}{p.is_mine ? '（我）' : ''}
                    </span>
                    {p.claimed && <span className="text-xs" style={{ color: 'var(--color-success)' }}>已加入</span>}
                  </div>
                ))}
              </div>
            )}
            <div className="space-y-1.5">
              {seats.map((p) => {
                const readyBadge = p.role === 'human' && p.character_id
                  ? (p.ready ? '已准备' : '待准备')
                  : (p.character_id ? '就绪' : '')
                const showDot = p.role === 'human' && p.character_id
                // 新身份模型中一号玩家不等于房主；房主可以移出除自己外的任意真人玩家。
                const canKick = amHost && p.claimed && p.role === 'human' && !p.is_mine
                const seatLabel = p.character_id
                  ? p.character_name || '未知角色'
                  : p.claimed
                    ? (p.is_mine ? '已加入，等待选择角色' : '已预留 · 等待玩家选择角色')
                    : '空席 · 等待真人加入'
                return (
                  <div key={p.seat_order} className="flex items-center gap-2 px-2 py-1.5 rounded" style={{ background: 'var(--surface-2)' }}>
                    <SeatIcon kind={seatKind(p)} size={15} />
                    {showDot && (
                      <span
                        title={p.is_online ? '在线' : '离线'}
                        style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                          background: p.is_online ? 'var(--color-success)' : 'var(--color-border)' }}
                      />
                    )}
                    {/* AI 席由房主直接指派队友卡（真人席只能本人认领，走 /claim） */}
                    {/* 用项目自己的 Select（Radix + 主题变量）。原生 <select> 的弹层由系统绘制，
                        在这套羊皮纸配色里会突兀地弹出一片深蓝系统菜单，和站内其它下拉也不一致。 */}
                    {p.role === 'ai' && amHost ? (
                      <Select
                        value={p.character_id || '__none'}
                        onValueChange={(v) => void assignAiChar(p.seat_order, v === '__none' ? null : v)}
                        disabled={busy}
                      >
                        <SelectTrigger className="flex-1" aria-label={`AI 队友 ${p.seat_order + 1} 的角色`}>
                          <SelectValue placeholder="— 选择 AI 队友角色 —" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none">— 未指派 —</SelectItem>
                          {p.character_id && !myChars.some((c) => c.id === p.character_id) && (
                            <SelectItem value={p.character_id}>{p.character_name || '当前角色'}</SelectItem>
                          )}
                          {myChars.map((c) => (
                            <SelectItem key={c.id} value={c.id}>
                              {c.name}
                              {occupationOf(c) && (
                                <span style={{ color: 'var(--color-text-secondary)' }}>（{occupationOf(c)}）</span>
                              )}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                    {!(p.role === 'ai' && amHost) && (
                      <button
                        onClick={() => viewSeat(p.character_id)}
                        disabled={!p.character_id}
                        className="text-sm text-left flex-1 disabled:cursor-default"
                        style={{ color: p.is_mine ? 'var(--color-text-accent)' : 'var(--color-text-primary)' }}
                      >
                        {seatLabel}
                        {p.is_host ? ' · 房主' : ''}{p.is_mine ? '（我）' : ''}
                      </button>
                    )}
                    {/* 唯一的「看卡」入口：AI 席也走它。从前 AI 席另有一颗一模一样的眼睛
                        （理由是下拉盲选、房主看不到队友卡），可这颗本来就对所有有角色的席位
                        都渲染，于是房主视角下 AI 席并排两颗眼睛、点哪颗都一样。 */}
                    {p.character_id && (
                      <button
                        onClick={() => viewSeat(p.character_id)}
                        className="btn-secondary !px-1.5 !py-1"
                        title={p.role === 'ai' ? '查看这张队友卡' : '查看角色卡'}
                        aria-label="查看角色卡"
                      >
                        <Eye size={13} />
                      </button>
                    )}
                    {p.character_id && p.role === 'human' && (myKpSeat || (room.kp_mode === 'human' && amHost)) && (
                      <button
                        onClick={() => void evaluateSeat(p.character_id)}
                        disabled={evaluating}
                        className="btn-secondary !px-1.5 !py-1"
                        title="评估角色是否适合本模组"
                        aria-label="评估角色适配性"
                      >
                        <ScanSearch size={13} />
                      </button>
                    )}
                    {readyBadge && (
                      <span className="text-xs inline-flex items-center gap-0.5" style={{ color: p.ready || p.role !== 'human' ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
                        {(p.ready || p.role !== 'human') && <Check size={12} />}{readyBadge}
                      </span>
                    )}
                    {canKick && (
                      <button
                        onClick={() => kick(p.seat_order)}
                        disabled={busy}
                        className="text-xs px-1.5 py-0.5 rounded"
                        style={{ color: 'var(--color-danger)', border: '1px solid var(--color-danger)' }}
                        title="移出该玩家（席位回到空席）"
                      >移出</button>
                    )}
                    {/* 删座位：房主专用；不能删自己，也不能低于模组推荐下限（后端另有硬拦） */}
                    {amHost && !p.is_mine && seats.length > Math.max(seatRange.min, 1) && (
                      <button
                        onClick={() => void removeSeat(p.seat_order)}
                        disabled={busy}
                        className="btn-secondary !px-1.5 !py-1"
                        title="删除该席位"
                        aria-label="删除该席位"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>

            {/* 人数在房间里调，但按本模组的推荐区间约束——模组既然已经选定，人数就不该再是任意的 */}
            {amHost && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  onClick={() => void addSeat('ai')}
                  disabled={busy || seats.length >= seatRange.max}
                  className="btn-secondary inline-flex items-center gap-1 !px-2 !py-1 text-xs disabled:opacity-40"
                >
                  <UserPlus size={12} /> 加 AI 队友
                </button>
                <button
                  onClick={() => void addSeat('human')}
                  disabled={busy || seats.length >= seatRange.max}
                  className="btn-secondary inline-flex items-center gap-1 !px-2 !py-1 text-xs disabled:opacity-40"
                  title="留一个空席，把房间码发给朋友来认领"
                >
                  <UserPlus size={12} /> 留真人空席
                </button>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  本模组推荐 {seatRange.min}–{seatRange.max} 人
                  {seats.length >= seatRange.max ? ' · 已达上限' : ''}
                  　AI 席选好角色即就绪；真人空席要本人加入后自己点准备
                </span>
              </div>
            )}

            {/* 我的操作区 */}
            <div className="lobby-my-actions mt-3 pt-3 border-t" style={{ borderColor: 'var(--color-border)' }}>
              {strictKpIdentity ? (
                <div className="flex items-center gap-3">
                  <span className="text-sm" style={{ color: 'var(--color-text-accent)' }}>你已作为真人 KP 加入</span>
                  {myKpSeat.is_host && <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>房主权限独立于玩家席</span>}
                </div>
              ) : !mySeat || needsCharacter || changingChar ? (
                <div>
                  {changingChar && (
                    <div className="mb-2 flex items-center gap-2">
                      <span className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                        选一张新的角色卡替换「{mySeat?.character_name}」：
                      </span>
                      <button
                        onClick={() => setChangingChar(false)}
                        className="btn-secondary !px-2 !py-0.5 text-xs"
                      >
                        取消
                      </button>
                    </div>
                  )}
                  {openKpSeat && (
                    <div className="mb-3 flex items-center gap-2">
                      <button onClick={claimKp} disabled={busy} className="btn-primary !px-2.5 !py-1 text-sm">
                        以真人 KP 身份加入
                      </button>
                      <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>以 KP 身份加入后，就不再占用玩家席位</span>
                    </div>
                  )}
                  {/* 车卡建议钉在挑角色之前：本模组适合什么、忌讳什么，正是此刻要用的判据。
                      此前它只挂在角色页——玩家进了大厅才决定演谁，那时已经看不到它了。
                      放在这个分支里（而不是页面顶部）是因为它只在「我正在挑」时才有用，
                      挑完就是噪音，也不该把席位挤下去。 */}
                  {hasGuidance(guidance) && (
                    <div className="mb-2">
                      <CharacterGuidanceCard guidance={guidance!} moduleTitle={room.module_title} />
                    </div>
                  )}
                  {/* 两件事，两块区域，不再挤在同一排：
                      「挑一张现成的」和「现场造一张」是不同的操作，从前它们同在一个
                      flex-wrap 里视觉同级；更别扭的是那个提示词输入框——它只服务「AI 生成」，
                      却被放在最前面，中间还隔着一堆已有角色，读下来逻辑是断的。 */}
                  <div className="seat-pick">
                    <div className="seat-pick-head">选一张已有的角色卡</div>
                    {myChars.length === 0 && localChars.length === 0 && (
                      <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                        还没有可用的角色卡，用下面的方式现场生成一张。
                      </p>
                    )}
                    {/* 卡多了不能一路铺下去：限高滚动 + 搜索。角色库上二十张是常态，
                        全平铺会把席位、聊天、开始按钮全挤出屏幕。 */}
                    {(myChars.length + localChars.length > 6) && (
                      <input
                        value={charFilter}
                        onChange={(e) => setCharFilter(e.target.value)}
                        placeholder={`在 ${myChars.length + localChars.length} 张卡里找：姓名或职业`}
                        className="input mb-2 w-full !py-1 text-sm"
                      />
                    )}
                    {matchChars(myChars).length > 0 && (
                      <div className="char-grid char-grid--scroll">
                        {matchChars(myChars).map((c) => renderCharCard(c, 'mine'))}
                      </div>
                    )}
                    {myChars.length > 0 && matchChars(myChars).length === 0 && (
                      <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                        没有匹配「{charFilter}」的角色卡
                      </p>
                    )}
                    {localChars.length > 0 && (
                      <div className="mt-2">
                        <div className="seat-pick-sub">
                          本机角色 · 入座时会同步一份副本给房主，你自己这份不受影响
                        </div>
                        <div className="char-grid char-grid--scroll">
                          {matchChars(localChars).map((c) => renderCharCard(c, 'local'))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* 和 AI 席那颗 ✨ 走同一个对话框：写提示词 → 生成 → 过目/编辑 → 决定去留。
                      从前这里是「填提示词 → 一键生成并入座」，等了一分多钟直接就坐下了，
                      连看一眼的机会都没有——而旁边 AI 席反倒有得看，两处凭什么不一样。 */}
                  <div className="seat-pick">
                    <div className="seat-pick-head">或让 AI 现场生成一张</div>
                    <p className="mb-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                      写一句想扮演的人物，生成后可以先过目、编辑，再决定要不要入座。
                    </p>
                    <button onClick={() => setGenSelf(true)} disabled={busy}
                      className="btn-secondary inline-flex items-center gap-1 !px-2 !py-1 text-xs">
                      <Sparkles size={12} /> 生成我的调查员
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <button onClick={toggleReady} className={mySeat.ready ? 'btn-secondary' : 'btn-primary'}>
                    {mySeat.ready ? '取消准备' : '准备'}
                  </button>
                  <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                    你已入座为「{mySeat.character_name}」
                  </span>
                  <button
                    onClick={() => setChangingChar(true)}
                    className="btn-secondary !px-2 !py-0.5 text-xs"
                    title="开局前可以换一张角色卡"
                  >
                    更换角色
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* 开局条钉在底部：无论上面配到多少张角色卡，「开始游戏」永远在屏幕上 */}
        <div className="card flex-shrink-0 mt-3">
          {amHost ? (
            <div className="flex items-center gap-3">
              <button onClick={startGame} disabled={busy || seatGaps.length > 0} className="btn-primary">
                开始游戏
              </button>
              <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                {seatGaps.length > 0 ? seatGaps.join('；') : '所有人就绪，可以开始'}
              </span>
            </div>
          ) : (
            <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              {seatGaps.length > 0 ? `等待中：${seatGaps.join('；')}` : '所有人就绪，等待房主开始…'}
            </p>
          )}
        </div>
      </div>

      {/* 右栏常驻：选人模式下它永远在场，像格斗/英雄游戏的选人详情栏——
          未选时是提示，点选后是「角色登场 + 完整档案 + 确认入座」。 */}
      {asideOpen && (
        <aside className={`w-80 flex-shrink-0 border-l overflow-y-auto flex flex-col${selectingCharacter ? ' hero-dossier' : ''}`} style={{ borderColor: 'var(--color-border)', background: 'var(--color-bg-card)' }}>
          <div className="flex items-center justify-between px-3 py-1.5 text-xs border-b" style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}>
            <span>{selectingCharacter ? (preview ? '已选择 · 等待入座' : '选择你的调查员') : (preview ? '预览 · 尚未入座' : '角色卡')}</span>
            {(preview || panelChar) && (
              <button
                onClick={() => { setPreview(null); setPanelChar(null) }}
                className="btn-secondary !px-2 !py-0.5"
              >{selectingCharacter ? '清空' : '关闭'}</button>
            )}
          </div>

          {/* 确认区钉在顶部：看完卡就在原地决定，不必滚回去找按钮。
              选人模式下做成英雄登场卡——大纹章、姓名、职业与 HP/SAN 一目了然。 */}
          {preview && (
            <div className="hero-dossier-pick">
              <div className="hero-dossier-halo" aria-hidden="true">
                <CharacterPortrait
                  name={preview.char.name}
                  avatarUrl={preview.char.avatar_url}
                  size="lg"
                  className="hero-dossier-portrait"
                />
              </div>
              <div className="hero-dossier-name">{preview.char.name}</div>
              {previewDigest?.meta && <div className="hero-dossier-meta">{previewDigest.meta}</div>}
              {(previewDigest?.hp || previewDigest?.san) && (
                <div className="hero-dossier-vitals">
                  {previewDigest.hp && <span className="hero-vital hero-vital--hp">HP <b>{previewDigest.hp}</b></span>}
                  {previewDigest.san && <span className="hero-vital hero-vital--san">SAN <b>{previewDigest.san}</b></span>}
                </div>
              )}
              {previewDigest?.skills.length ? (
                <div className="hero-dossier-skills">
                  {previewDigest.skills.map((skill) => <span key={skill}>{skill}</span>)}
                </div>
              ) : null}
              {preview.source === 'local' && (
                <p className="hero-dossier-note">
                  本机角色 · 入座时同步一份副本给房主，你自己这份不受影响
                </p>
              )}
              <div className="hero-dossier-actions">
                <button
                  onClick={confirmPreview}
                  disabled={busy}
                  className="btn-primary flex-1 !px-3 !py-1.5 text-sm"
                >
                  {busy ? '处理中…' : '用这张卡入座'}
                </button>
                <button
                  onClick={() => setPreview(null)}
                  className="btn-secondary !px-2 !py-1 text-xs"
                >再看看</button>
              </div>
            </div>
          )}

          {selectingCharacter && !preview && !panelChar && (
            <div className="hero-dossier-empty">
              <ScanSearch size={22} aria-hidden="true" />
              <p>在左侧舞台中点选一名调查员，这里会展开完整档案与入座确认。</p>
            </div>
          )}

          {evaluation && (
            <div className="mx-3 mt-3 rounded border p-2 text-xs" style={{ borderColor: evaluation.compatible ? 'var(--color-success)' : 'var(--color-danger)' }}>
              <div className="font-semibold mb-1" style={{ color: evaluation.compatible ? 'var(--color-success)' : 'var(--color-danger)' }}>
                {evaluation.compatible ? '适合本模组' : '存在适配风险'}
              </div>
              {evaluation.warnings.length > 0 && <div className="mb-1">问题：{evaluation.warnings.join('；')}</div>}
              {evaluation.suggestions.length > 0 && <div>建议：{evaluation.suggestions.join('；')}</div>}
            </div>
          )}

          {(preview || panelChar) && (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <CharacterPanel character={preview?.char ?? panelChar!} />
            </div>
          )}
        </aside>
      )}

      {/* 快速创建：先落空白草稿，再复用角色页的编辑弹窗；保存后直接进入右侧预览。 */}
      {quickCreateOpen && room && (
        <QuickCharacterCreateDialog
          open
          draft={quickDraft}
          creating={quickCreating}
          onClose={closeQuickCreate}
          onCreated={(characterId) => void handleQuickCreated(characterId)}
        />
      )}

      {/* 写提示词 → 生成 → 过目/编辑 → 决定去留。卡在「过目」这一步已经落库但**未入座**，
          玩家弃用就连卡一起删掉；点「保留并入座」才走到认领。 */}
      {genSelf && room && (
        <AiTeammateDialog
          open
          forPlayer
          moduleId={room.module_id}
          guidance={guidance}
          onClose={() => setGenSelf(false)}
          onConfirm={async (char) => { await claimWithChar(char.id) }}
        />
      )}

      {/* 聊天是右侧浮层：收起成一颗圆 token，展开是一张悬浮卡。它不占布局宽度、
          也不参与页面高度——「聊得再多也不会把大厅顶长」。 */}
      <LobbyChatDock
        lines={chat}
        typingName={typingName}
        canSpeak={!!myChatSeat}
        onSend={(text) => void sendChat(text)}
        onTyping={notifyTyping}
        shifted={asideOpen}
        compact={selectingCharacter}
      />
    </div>
  )
}
