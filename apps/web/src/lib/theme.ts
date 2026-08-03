/**
 * 主题切换（纯前端，裸 localStorage，无状态库）
 * - 'gothic'    ：克苏鲁哥特暗色（默认，:root 基线）
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
  gothic: '#0c0e13',
  parchment: '#f0e6d3',
}

export const THEMES: { value: Theme; label: string; swatch: string[] }[] = [
  { value: 'gothic', label: '暗夜哥特', swatch: ['#0c0e13', '#14171f', '#d4a24e'] },
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
