/**
 * 战场底图：调既有的生图链路，出一张**俯视**的战术底图铺在网格下面。
 *
 * 为什么值得做：这张战场上一格地形都没有（`blocked` / `cover` 全空），地面是真的空的。
 * VTT 之所以好看，底图占九成——没有底图，再怎么调光影和纹理都只是给空地叠特效。
 *
 * 安全性：走 `/kp/images/preview`。那个端点只生成图片并存盘，**不写事件、不碰存档**；
 * 真正落库的是 `/kp/images/publish`，原型页一次都不调它。所以这一页仍然是「不会改动
 * 正在跑的团」，只是会花掉一次生图调用。
 *
 * 结果存 localStorage，按「存档 + 场景」作键：同一个场景重进不必重生成，换场景自然换图。
 * 正式版该把它落到场景资料上（和 scene.image 同一个位置），这样全桌都能看到同一张。
 */
import { useCallback, useEffect, useState } from 'react'

import { api } from '@/api/client'

const STORE_KEY = 'kplab_backdrops'

/**
 * 底图浓淡。
 *
 * 为什么做成可调而不是定一个常数：生成出来的图质量差异极大。规整的俯视平面图压太狠就白
 * 生成了；而模型一旦跑偏出一张人物插画，不压到很淡棋子就会被脸和衣褶盖掉——实测过，
 * 那种情况下整张战术图直接失效。没有哪个常数能同时伺候这两种图，所以交给 KP 一秒切换。
 */
export type BackdropLevel = 'faint' | 'normal' | 'strong'
export const BACKDROP_LEVELS: { key: BackdropLevel; label: string }[] = [
  { key: 'faint', label: '淡' },
  { key: 'normal', label: '中' },
  { key: 'strong', label: '浓' },
]

interface Entry { url: string; level: BackdropLevel }
type Store = Record<string, Entry | string>

function readAll(): Store {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as Store
  } catch {
    return {}
  }
}

/** 旧版本只存了一个 url 字符串，读到就按「中」补齐，不让老数据把页面打挂。 */
function normalize(v: Entry | string | undefined): Entry | null {
  if (!v) return null
  if (typeof v === 'string') return { url: v, level: 'normal' }
  return { url: v.url, level: v.level || 'normal' }
}

function write(slot: string, entry: Entry | null) {
  try {
    const all = readAll()
    if (entry) all[slot] = entry
    else delete all[slot]
    localStorage.setItem(STORE_KEY, JSON.stringify(all))
  } catch { /* 存不下不影响本次显示 */ }
}

/**
 * 提示词刻意写得很硬。
 *
 * 生图模型的默认倾向是画「场景插图」——带人物、带戏剧光、带透视。那种图当氛围底很好，
 * 当战术底图是灾难：有透视就对不上方格，有人物就和棋子打架。所以正交俯视、无人物、
 * 无文字这三条必须反复强调，宁可啰嗦。
 */
function promptFor(sceneName: string, moduleTitle: string) {
  const where = sceneName || moduleTitle || '未知场所'
  return [
    `正上方垂直俯瞰的战术地图，${where}的地面平面图`,
    '正交俯视视角，镜头垂直向下，没有任何透视变形',
    '只画地面、墙体、家具与陈设的顶面',
    '画面中不要出现任何人物、生物、棋子、箭头、文字、图例、边框',
    '光线均匀平淡，不要戏剧性打光和强烈阴影',
    '整体偏暗、低饱和，便于在上面叠加方格与棋子',
  ].join('，')
}

export function useBattleBackdrop(sessionId: string | undefined, sceneKey: string, moduleTitle: string) {
  const slot = `${sessionId || '-'}:${sceneKey || '-'}`
  const [url, setUrl] = useState('')
  const [level, setLevelState] = useState<BackdropLevel>('normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const e = normalize(readAll()[slot])
    setUrl(e?.url || '')
    setLevelState(e?.level || 'normal')
    setError('')
  }, [slot])

  const generate = useCallback(async () => {
    if (!sessionId || busy) return
    setBusy(true); setError('')
    try {
      const r = await api.post<{ url: string }>(`/sessions/${sessionId}/kp/images/preview`, {
        prompt: promptFor(sceneKey, moduleTitle),
        title: `${sceneKey || moduleTitle} · 战场底图`,
      })
      if (!r?.url) throw new Error('生图接口没有返回图片')
      setUrl(r.url)
      write(slot, { url: r.url, level })
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setBusy(false)
    }
  }, [sessionId, sceneKey, moduleTitle, slot, busy, level])

  const setLevel = useCallback((next: BackdropLevel) => {
    setLevelState(next)
    if (url) write(slot, { url, level: next })
  }, [slot, url])

  const clear = useCallback(() => {
    setUrl(''); setError('')
    write(slot, null)
  }, [slot])

  return { url, level, setLevel, busy, error, generate, clear }
}
