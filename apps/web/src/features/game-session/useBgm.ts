/**
 * 背景音乐播放引擎：按氛围选曲、交叉淡入淡出、自动播放受阻时等第一次交互再起。
 *
 * 曲库从后端 /bgm/tracks 取（按目录扫描，用户自己放的曲子也在里面），本 hook 只管「放」：
 * - 氛围变了才换曲；新氛围的曲目列表里本就含正在放的那首（比如两边都回落到 explore），
 *   就接着放，不要无缘无故从头再来一遍；
 * - 一个氛围多首曲子时放完一首换下一首；只有一首就单曲循环；
 * - 浏览器不允许无交互自动出声，play() 被拒时挂一个一次性的 pointerdown/keydown 监听，
 *   玩家随便点一下页面就接上，而不是要他去找哪个按钮。
 *
 * 开关与音量是**每台设备**的偏好（同桌的人各自调），存 localStorage，不进会话。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, getServerUrl } from '@/api/client'

import { deriveBgmMood, pickTrack, tracksForMood } from './bgm'

const ENABLED_KEY = 'trpg_bgm_enabled'
const VOLUME_KEY = 'trpg_bgm_volume'
const DEFAULT_VOLUME = 0.4
const FADE_MS = 1800
const FADE_STEP_MS = 50

function readEnabled(): boolean {
  try { return localStorage.getItem(ENABLED_KEY) !== '0' } catch { return true }
}

function readVolume(): number {
  try {
    const v = Number(localStorage.getItem(VOLUME_KEY))
    return localStorage.getItem(VOLUME_KEY) !== null && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : DEFAULT_VOLUME
  } catch { return DEFAULT_VOLUME }
}

function absolutize(raw: string): string {
  return /^https?:\/\//i.test(raw) ? raw : `${getServerUrl()}${raw}`
}

/** 把一个 audio 的音量在 FADE_MS 内线性拉到 target；返回取消函数。到 0 时顺手暂停。 */
function fadeTo(audio: HTMLAudioElement, target: number, onDone?: () => void): () => void {
  const start = audio.volume
  const steps = Math.max(1, Math.round(FADE_MS / FADE_STEP_MS))
  let i = 0
  const timer = window.setInterval(() => {
    i += 1
    audio.volume = Math.min(1, Math.max(0, start + ((target - start) * i) / steps))
    if (i >= steps) {
      window.clearInterval(timer)
      if (target === 0) audio.pause()
      onDone?.()
    }
  }, FADE_STEP_MS)
  return () => window.clearInterval(timer)
}

export interface BgmState {
  /** 此刻的氛围（控件显示用） */
  mood: string
  enabled: boolean
  volume: number
  /** 浏览器拦了自动播放，正等玩家第一次交互 */
  blocked: boolean
  /** 曲库里一首曲子都没有（控件据此提示「去哪放曲子」） */
  empty: boolean
  setEnabled: (on: boolean) => void
  setVolume: (v: number) => void
}

function useBgm(mood: string | null): Omit<BgmState, 'mood'> {
  const [enabled, setEnabledState] = useState(readEnabled)
  const [volume, setVolumeState] = useState(readVolume)
  const [blocked, setBlocked] = useState(false)
  const [tracks, setTracks] = useState<Record<string, string[]> | null>(null)

  const currentRef = useRef<{ audio: HTMLAudioElement; url: string; cancelFade?: () => void } | null>(null)
  const volumeRef = useRef(volume)
  volumeRef.current = volume

  useEffect(() => {
    let stale = false
    api.get<{ tracks: Record<string, string[]> }>('/bgm/tracks')
      .then((r) => { if (!stale) setTracks(r.tracks || {}) })
      .catch(() => { if (!stale) setTracks({}) })
    return () => { stale = true }
  }, [])

  const stop = useCallback(() => {
    const cur = currentRef.current
    if (!cur) return
    currentRef.current = null
    cur.cancelFade?.()
    fadeTo(cur.audio, 0, () => { cur.audio.src = '' })
  }, [])

  // 起播一首：新曲从 0 淡入，旧曲同时淡出。play() 被浏览器拒绝就记下 blocked，等交互重试。
  const start = useCallback((url: string, loop: boolean, onEnded: () => void) => {
    stop()
    const audio = new Audio(absolutize(url))
    audio.loop = loop
    audio.volume = 0
    // 只有「当前这首」放完才接下一首：正在淡出的旧曲恰好放到结尾时不能再拉起一首
    audio.addEventListener('ended', () => { if (currentRef.current?.audio === audio) onEnded() })
    const entry: { audio: HTMLAudioElement; url: string; cancelFade?: () => void } = { audio, url }
    currentRef.current = entry
    audio.play()
      .then(() => {
        setBlocked(false)
        if (currentRef.current === entry) entry.cancelFade = fadeTo(audio, volumeRef.current)
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'NotAllowedError') setBlocked(true)
      })
  }, [stop])

  // 选曲：氛围或曲库变化时决定是否换曲。
  const list = tracks && mood ? tracksForMood(tracks, mood) : []
  const listKey = list.join('\n')
  useEffect(() => {
    if (!enabled || !list.length) { stop(); return }
    const cur = currentRef.current
    if (cur && list.includes(cur.url)) {          // 正在放的这首仍属新氛围：接着放
      cur.audio.loop = list.length === 1
      return
    }
    const playNext = (prev: string | null) => {
      const next = pickTrack(list, prev)
      if (next) start(next, list.length === 1, () => playNext(next))
    }
    playNext(null)
    // list 以 listKey 代表其内容；start/stop 是稳定回调
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, listKey, start, stop])

  // 音量即时生效（淡入中的那一次也会以新音量为终点重拉）
  useEffect(() => {
    const cur = currentRef.current
    if (!cur || cur.audio.paused) return
    cur.cancelFade?.()
    cur.audio.volume = volume
  }, [volume])

  // 自动播放被拦：等玩家第一次点/按键，再把当前这首拉起来
  useEffect(() => {
    if (!blocked) return
    const resume = () => {
      const cur = currentRef.current
      if (!cur) { setBlocked(false); return }
      cur.audio.play()
        .then(() => { setBlocked(false); cur.cancelFade = fadeTo(cur.audio, volumeRef.current) })
        .catch(() => {})
    }
    window.addEventListener('pointerdown', resume, { once: true })
    window.addEventListener('keydown', resume, { once: true })
    return () => {
      window.removeEventListener('pointerdown', resume)
      window.removeEventListener('keydown', resume)
    }
  }, [blocked])

  // 离开游戏页：立刻停（不淡出——页面都没了，没人听那 1.8 秒）
  useEffect(() => () => {
    const cur = currentRef.current
    currentRef.current = null
    if (cur) { cur.cancelFade?.(); cur.audio.pause(); cur.audio.src = '' }
  }, [])

  const setEnabled = useCallback((on: boolean) => {
    setEnabledState(on)
    try { localStorage.setItem(ENABLED_KEY, on ? '1' : '0') } catch { /* 隐私模式：只在本次生效 */ }
  }, [])
  const setVolume = useCallback((v: number) => {
    const clamped = Math.min(1, Math.max(0, v))
    setVolumeState(clamped)
    try { localStorage.setItem(VOLUME_KEY, String(clamped)) } catch { /* 同上 */ }
  }, [])

  const empty = tracks !== null && Object.values(tracks).every((l) => !l.length)
  return { enabled, volume, blocked, empty, setEnabled, setVolume }
}

/**
 * 游戏页用的入口：战斗/追逐压过场景氛围，场景氛围取「我自己」所在处（见 ./bgm.ts）。
 * 模组已结束就不再放——结局后的复盘页面不需要一首循环的调查曲。
 */
export function useSessionBgm(input: {
  combat: unknown
  chase: unknown
  locations: { current: boolean; mood?: string }[]
  ended: boolean
}): BgmState {
  const mood = deriveBgmMood({ inCombat: !!input.combat, inChase: !!input.chase, locations: input.locations })
  return { mood, ...useBgm(input.ended ? null : mood) }
}
