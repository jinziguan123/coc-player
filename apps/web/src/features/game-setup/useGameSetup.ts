import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api, getServerUrl, setServerIdentity, setServerUrl } from '@/api/client'
import { netlinkConnect } from '@/api/netlink'
import { PROTOCOL_VERSION } from '@/lib/roomEvents'
import { useModuleStore } from '@/stores/moduleStore'
import { useSessionStore } from '@/stores/sessionStore'
import {
  createCharacter,
  generateCharacter,
  listAvailableCharacters,
} from '@/features/characters/api'
import {
  createEmptyModuleFilters,
  filterModules,
  hasModuleFilters,
  moduleFilterOptions,
  parsePlayerRange,
} from './moduleFilters'
import type { ModuleFilters, SetupCharacter, SetupSeat } from './types'

interface RoomInfo {
  id: string
}

/** 自报名记在本地：每次加入都重填一遍太烦。 */
const GUEST_LABEL_KEY = 'trpg_guest_label'
/**
 * 上次用过的邀请码。房主重开应用后邀请码不变（身份已持久化），记住它，
 * 断线重连就只是点一下「加入」，不必再去找他要一遍。
 */
const LAST_INVITE_KEY = 'trpg_last_invite'

export function useGameSetup() {
  const { createSession, fetchSessions, sessions } = useSessionStore()
  const { modules, fetchModules } = useModuleStore()
  const navigate = useNavigate()
  const [heroes, setHeroes] = useState<SetupCharacter[]>([])
  const [allies, setAllies] = useState<SetupCharacter[]>([])
  const [moduleId, setModuleId] = useState('')
  const [kpMode, setKpModeState] = useState<'ai' | 'human'>('ai')
  const [seats, setSeats] = useState<SetupSeat[]>([])
  const [seatHints, setSeatHints] = useState<Record<number, string>>({})
  const [generatingSeat, setGeneratingSeat] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [joinCode, setJoinCode] = useState('')
  // 已连着就显示当前主机；否则回填上次的邀请码，好让断线后一键重连。
  const [hostAddr, setHostAddr] = useState(
    () => getServerUrl() || localStorage.getItem(LAST_INVITE_KEY) || '',
  )
  // 自报给房主看的名字。记在本地，下次不用重填——房主那边看到的是一串公钥，
  // 有个名字他才认得出是谁。
  const [guestLabel, setGuestLabel] = useState(
    () => localStorage.getItem(GUEST_LABEL_KEY) || '',
  )
  const [joinWaiting, setJoinWaiting] = useState(false)
  const [filters, setFilters] = useState<ModuleFilters>(createEmptyModuleFilters)

  const filteredModules = useMemo(
    () => filterModules(modules, filters),
    [filters, modules],
  )
  const filterOptions = useMemo(() => moduleFilterOptions(modules), [modules])
  const selectedModule = modules.find((module) => module.id === moduleId)
  const range = parsePlayerRange(selectedModule?.world_setting)
  const minSeats = Math.max(range.min, 1)
  const usedIds = seats.map((seat) => seat.charId).filter(Boolean)

  const refreshCharacters = useCallback(async () => {
    const [availableHeroes, availableAllies] = await Promise.all([
      listAvailableCharacters(true),
      listAvailableCharacters(false),
    ])
    setHeroes(availableHeroes)
    setAllies(availableAllies)
  }, [])

  useEffect(() => {
    void fetchModules()
    void fetchSessions()
    void refreshCharacters()
  }, [fetchModules, fetchSessions, refreshCharacters])

  const setFilter = (key: keyof ModuleFilters, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }))
  }

  const onSelectModule = (value: string) => {
    setModuleId(value)
    setError('')
    const moduleRange = parsePlayerRange(
      modules.find((module) => module.id === value)?.world_setting,
    )
    const count = Math.max(moduleRange.min, 1)
    setSeats(Array.from({ length: count }, (_, index) => ({
      role: index === 0 ? 'human' : 'ai',
      charId: '',
    })))
  }

  const changeSeatCount = (delta: number) => {
    setSeats((current) => {
      const target = Math.max(minSeats, Math.min(range.max, current.length + delta))
      const next = current.slice(0, target)
      while (next.length < target) next.push({ role: 'ai', charId: '' })
      // AI KP 模式 0 号席是创建者本人（必为真人）；真人 KP 模式所有玩家席自由选 AI/真人
      if (kpMode === 'ai' && next[0]) next[0] = { ...next[0], role: 'human' }
      return next
    })
  }

  const assignSeat = (index: number, characterId: string) => {
    setSeats((current) => current.map((seat, seatIndex) => (
      seatIndex === index ? { ...seat, charId: characterId } : seat
    )))
  }

  const seatOptions = (index: number): SetupCharacter[] => {
    const pool = seats[index].role === 'human' ? heroes : allies
    return pool.filter((character) => (
      character.id === seats[index].charId || !usedIds.includes(character.id)
    ))
  }

  const generateForSeat = async (index: number) => {
    if (!moduleId || generatingSeat !== null) return
    const isPlayer = seats[index].role === 'human'
    setGeneratingSeat(index)
    setError('')
    try {
      const draft = await generateCharacter<Record<string, unknown>>({
        module_id: moduleId,
        hint: (seatHints[index] || '').trim(),
        is_player: isPlayer,
      })
      const created = await createCharacter<SetupCharacter>({
        name: draft.name,
        module_id: moduleId,
        rule_system: (draft.rule_system as string) || 'coc',
        is_player: isPlayer,
        age: draft.age ?? 25,
        base_attributes: draft.base_attributes,
        skills: draft.skills,
        system_data: draft.system_data,
        backstory: draft.backstory ?? '',
      })
      if (isPlayer) setHeroes((current) => [created, ...current])
      else setAllies((current) => [created, ...current])
      assignSeat(index, created.id)
      setSeatHints((current) => ({ ...current, [index]: '' }))
      toast.success(
        `AI 生成「${created.name}」并填入${index === 0 ? '房主' : `队友${index}`}席位`,
      )
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'AI 生成角色失败')
    } finally {
      setGeneratingSeat(null)
    }
  }

  const joinRoom = async () => {
    setError('')
    const typed = hostAddr.trim()

    // 邀请码（trpg:…）走内置直连：先把隧道建起来，再照常连本机那一头。
    // 房间码可以由邀请码带来，所以这一步要在「房间码必填」的检查之前。
    let code = joinCode.trim().toUpperCase()
    if (typed.toLowerCase().startsWith('trpg:')) {
      // 首次加入要房主手动点同意，这一步可能卡上一两分钟，得让人知道在等什么。
      setJoinWaiting(true)
      try {
        const link = await netlinkConnect(typed, guestLabel.trim())
        const host = `http://127.0.0.1:${link.local_port}`
        setServerUrl(host)
        // 记住它：房主重开应用后这串码依然有效，断线后就能一键重连。
        localStorage.setItem(LAST_INVITE_KEY, typed)
        // 本地端口每次连接都变，token 必须跟着房主走，否则每次重连都掉席位。
        setServerIdentity(host, `netlink:${typed.split(':')[1] ?? typed}`)
        if (link.room_code) {
          code = link.room_code.toUpperCase()
          setJoinCode(code)
        }
      } catch (reason: unknown) {
        // Rust 侧对「被拒绝」「房主没回应」给的是明确原因，直接透出。
        setError(
          reason instanceof Error
            ? reason.message
            : '按邀请码连接失败，请确认房主已开启内置直连',
        )
        return
      } finally {
        setJoinWaiting(false)
      }
      if (!code) {
        setError('已连上房主，还需要填房间码')
        return
      }
      return await enterRoom(code)
    }

    if (!code) return
    let host = typed
    if (host && !/^https?:\/\//.test(host)) host = `http://${host}`
    if (host && !/:\d+$/.test(host)) host = `${host}:8000`
    setServerUrl(host)
    return await enterRoom(code)
  }

  /** 握手协议版本并进房。地址已经设好，这里只管「进得去进不去」。 */
  const enterRoom = async (code: string) => {
    try {
      // 先握手协议版本：房主与客人版本不一致时，有些事件类型对方根本不认，
      // 连进去只会表现成「界面莫名其妙不更新」。明说比半坏好。
      const health = await api.get<{ protocol_version?: number }>('/health')
      const hostProtocol = health.protocol_version ?? 0
      if (hostProtocol !== PROTOCOL_VERSION) {
        setServerUrl('')
        setError(
          `主机与本机版本不一致（主机协议 v${hostProtocol}，本机 v${PROTOCOL_VERSION}），` +
          '请双方升级到同一版本后再联机。',
        )
        return
      }
      const room = await api.get<RoomInfo>(`/sessions/by-code/${code}`)
      navigate(`/room/${room.id}`)
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : '加入房间失败（检查主机地址与房间码、确认同一局域网）',
      )
    }
  }

  const disconnectHost = () => {
    setServerUrl('')
    setHostAddr('')
    setError('')
    void fetchModules()
    void fetchSessions()
    void refreshCharacters()
  }

  const allSeatsFilled = seats.length > 0 && seats.every((seat, index) => {
    // 真人 KP：创建者只占 KP 席，玩家席自由——真人席留空待认领、AI 席需填角色。
    if (kpMode === 'human') return seat.role === 'human' ? true : Boolean(seat.charId)
    if (index === 0) return Boolean(seat.charId)   // AI KP：0 号是创建者自己，需选角色
    if (seat.role === 'human') return true
    return Boolean(seat.charId)
  })

  const setSeatRole = (index: number, role: 'human' | 'ai') => {
    setSeats((current) => current.map((seat, seatIndex) => (
      seatIndex === index
        ? { role, charId: role === 'human' ? '' : seat.charId }
        : seat
    )))
  }

  const startGame = async () => {
    if (!moduleId || !allSeatsFilled) return
    setError('')
    try {
      const participants = seats.map((seat, index) => ({
        character_id: seat.charId || null,
        role: seat.role,
        is_primary: index === 0,
      }))
      const session = await createSession(moduleId, participants, kpMode)
      if (session.status === 'setup') navigate(`/room/${session.id}`)
      else navigate(`/game/${session.id}`, { state: { isNew: true } })
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : '创建游戏失败')
    }
  }

  const setKpMode = (mode: 'ai' | 'human') => {
    setKpModeState(mode)
    if (mode === 'human') {
      // 清掉创建者之前选中的玩家角色，确保同一 token 不会同时占 KP/玩家席。
      // 默认留成真人空席，但此后每个席位（含 0 号）都可自由切 AI/真人。
      setSeats((current) => current.map((seat, index) => (
        index === 0 ? { ...seat, role: 'human', charId: '' } : seat
      )))
    } else {
      // 切回 AI KP：0 号席就是创建者本人，必为真人。
      setSeats((current) => current.map((seat, index) => (
        index === 0 ? { ...seat, role: 'human' } : seat
      )))
    }
  }

  const deleteSession = async (sessionId: string) => {
    try {
      await api.delete(`/sessions/${sessionId}`)
      await fetchSessions()
      await refreshCharacters()
      toast.success('游戏存档已删除')
    } catch {
      toast.error('删除失败')
    }
  }

  const activeSessions = sessions.filter((session) => (
    session.status === 'active' || session.status === 'paused' || session.status === 'setup'
  ))

  return {
    modules,
    filteredModules,
    filters,
    filterOptions,
    hasFilter: hasModuleFilters(filters),
    setFilter,
    resetFilters: () => setFilters(createEmptyModuleFilters()),
    moduleId,
    kpMode,
    setKpMode,
    selectedModule,
    range,
    minSeats,
    seats,
    seatHints,
    setSeatHint: (index: number, value: string) => {
      setSeatHints((current) => ({ ...current, [index]: value }))
    },
    generatingSeat,
    error,
    onSelectModule,
    changeSeatCount,
    assignSeat,
    seatOptions,
    generateForSeat,
    setSeatRole,
    allSeatsFilled,
    startGame,
    joinCode,
    setJoinCode,
    hostAddr,
    setHostAddr,
    guestLabel,
    setGuestLabel: (next: string) => {
      setGuestLabel(next)
      localStorage.setItem(GUEST_LABEL_KEY, next)
    },
    joinWaiting,
    connectedHost: getServerUrl(),
    joinRoom,
    disconnectHost,
    activeSessions,
    openSession: (session: { id: string; status: string }) => navigate(
      session.status === 'setup' ? `/room/${session.id}` : `/game/${session.id}`,
    ),
    deleteSession,
  }
}

export type GameSetupState = ReturnType<typeof useGameSetup>
