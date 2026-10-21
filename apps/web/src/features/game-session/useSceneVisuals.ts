/**
 * 局内的三张图，来源与用途各不相同，容易混：
 *
 * - **场景氛围底**（scene.image）：这地方「看起来什么样」。重度模糊压暗后铺满整页当底色，
 *   纯氛围，可在设置里关掉；
 * - **战场底图**（world_state.battle_maps）：打起来时「脚下的地面」，铺在方格战场之下，
 *   俯视正交，不带透视也不带人物；
 * - **沙盘氛围底**（模组的俯视总图）：大地图里垫在六边形网格之下。
 *
 * 三者共通的只有「相对路径要补服务器前缀」——联机走隧道时前端与后端不同源。抽在一处，
 * 免得日后再加第四张图时又在页面里多长出一段一模一样的 useMemo。
 */
import { useEffect, useMemo, useState } from 'react'

import { getServerUrl } from '@/api/client'
import { getSceneBackdropEnabled } from '@/lib/theme'

import { battleMapOf, sceneBackdropOf } from './derive'
import type { KnownLocation } from './useSessionResources'

/** 相对路径补服务器前缀；已是绝对地址的原样返回。 */
function absolutize(raw: string): string {
  return /^https?:\/\//i.test(raw) ? raw : `${getServerUrl()}${raw}`
}

interface MessageLike {
  metadata?: Record<string, unknown> | null
}

export function useSceneVisuals(input: {
  messages: MessageLike[]
  locations: KnownLocation[]
  mapBackdrop: string
  /** 对话框模式：场景图清晰铺满当舞台，不受「氛围底」开关管 */
  clear?: boolean
}) {
  const { messages, locations, mapBackdrop, clear = false } = input

  // 氛围底的开关在设置页，从那儿切回来要即时生效，所以听 focus 而不是只读一次。
  const [backdropOn, setBackdropOn] = useState(() => getSceneBackdropEnabled())
  useEffect(() => {
    const sync = () => setBackdropOn(getSceneBackdropEnabled())
    window.addEventListener('focus', sync)
    return () => window.removeEventListener('focus', sync)
  }, [])

  const sceneBackdrop = useMemo(() => {
    if (!backdropOn && !clear) return ''
    const raw = sceneBackdropOf(messages as never, locations as never)
    return raw ? absolutize(raw) : ''
  }, [backdropOn, clear, messages, locations])

  const battleMap = useMemo(() => battleMapOf(locations, getServerUrl()), [locations])

  const sandboxBackdropUrl = useMemo(
    () => (mapBackdrop ? absolutize(mapBackdrop) : undefined),
    [mapBackdrop],
  )

  return { sceneBackdrop, battleMap, sandboxBackdropUrl }
}
