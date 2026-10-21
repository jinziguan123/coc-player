/**
 * 主题切换（纯前端，裸 localStorage，无状态库）
 * - 'gothic'    ：密斯卡托尼克 · 装饰艺术暗色（默认，:root 基线；id 沿用旧名以兼容已存的 localStorage）
 * - 'parchment' ：羊皮纸暖褐（:root[data-theme="parchment"] 覆盖）
 *
 * 首帧防白闪由 index.html 内联脚本负责（在 #root 渲染前设好 data-theme）。
 * 本模块承载运行时读/写/应用，供 Settings 等页面调用。
 */

export type Theme = 'gothic' | 'parchment'

const STORAGE_KEY = 'trpg_theme'
const DEFAULT_THEME: Theme = 'gothic'

/** 各主题的 <meta name="theme-color"> 值（与 body 顶部底色一致，移动端地址栏配色） */
const META_THEME_COLOR: Record<Theme, string> = {
  gothic: '#0a110f',
  parchment: '#f0e6d3',
}

export const THEMES: { value: Theme; label: string; swatch: string[] }[] = [
  { value: 'gothic', label: '密大装饰', swatch: ['#0a110f', '#142420', '#c8a25a'] },
  { value: 'parchment', label: '羊皮纸', swatch: ['#f0e6d3', '#e8dcc8', '#8b2500'] },
]

export function getTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'parchment' || stored === 'gothic' ? stored : DEFAULT_THEME
}

/** 把主题应用到 DOM（写 data-theme + 同步 meta theme-color），不落库 */
export function applyTheme(theme: Theme): void {
  if (theme === DEFAULT_THEME) {
    delete document.documentElement.dataset.theme
  } else {
    document.documentElement.dataset.theme = theme
  }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', META_THEME_COLOR[theme])
}

/** 持久化 + 立即应用 */
export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme)
  applyTheme(theme)
}

/* ── 场景氛围底 ──────────────────────────────────────────────────────────────
 * 当前场景的配图作为对局界面的底色来源：重度模糊 + 压暗 + 降饱和后铺满，看不出是
 * 一张照片，只留下这个地方的色调（雾港的冷灰、地窖的暗褐）。
 *
 * 为什么不直接铺原图：AI 生成的图亮度分布不可控（实测港务所那张中央就是一大片过曝
 * 的白窗），正文压上去直接不可读；图又只有 1024²，铺满宽屏会糊。糊掉高频细节之后，
 * 过曝区变成一块柔和浅灰，压不垮文字，氛围却留住了。滤镜参数按主题各给一套
 * （见 index.css 的 --scene-backdrop-*），浅色的羊皮纸主题要提亮而不是压暗。
 */

const BACKDROP_KEY = 'trpg_scene_backdrop'

export function getSceneBackdropEnabled(): boolean {
  try {
    return localStorage.getItem(BACKDROP_KEY) !== '0'   // 缺省开
  } catch {
    return true    // 隐私模式等读不到 localStorage：按默认走，别把功能整个关掉
  }
}

export function setSceneBackdropEnabled(on: boolean): void {
  try {
    localStorage.setItem(BACKDROP_KEY, on ? '1' : '0')
  } catch { /* 存不下就只在本次会话生效 */ }
}

/**
 * 局内的显示方式：
 * - 'chat'：聊天流（缺省）。场景图在流里清晰出现一次，此后糊成氛围底；
 * - 'dialogue'：对话框（视觉小说式）。场景图清晰铺满，文字一段一段在底部对话框里读，
 *   往回看走「历史」。只对玩家视角生效，KP 工作台一律是聊天流。
 */
export type GameDisplayMode = 'chat' | 'dialogue'
const DISPLAY_KEY = 'trpg_game_display'
/** 同一页里切换（顶栏按钮 ↔ 设置页）时广播，免得各处各读各的旧值 */
export const DISPLAY_EVENT = 'trpg-game-display'

export function getGameDisplayMode(): GameDisplayMode {
  try {
    return localStorage.getItem(DISPLAY_KEY) === 'dialogue' ? 'dialogue' : 'chat'
  } catch {
    return 'chat'
  }
}

export function setGameDisplayMode(mode: GameDisplayMode): void {
  try {
    localStorage.setItem(DISPLAY_KEY, mode)
  } catch { /* 存不下就只在本次会话生效 */ }
  window.dispatchEvent(new Event(DISPLAY_EVENT))
}
