/**
 * 对话框模式（VN 式）的纯逻辑：哪些消息是一「站」、长文怎么分页、谁在说话。
 *
 * 与组件分开，是为了能单测——分页切在哪、什么算卡片，都是读起来顺不顺的关键。
 */
import type { ChatMessage } from '@/stores/sessionStore'
import { stripCommandTags } from '@/features/game-session/derive'

/** 一站的形态：line=对话框里读的一段；card=浮在对话框上方的卡片（检定、掷骰、手书、去向建议、配图） */
export type StopKind = 'line' | 'card'

export interface Stop {
  msg: ChatMessage
  kind: StopKind
  /** 对话框里显示的文字（card 也给一句摘要，免得对话框空着） */
  text: string
}

/** 一站的稳定键：落库消息用 id，个别没 id 的按序号 */
export function stopKey(msg: ChatMessage): string {
  return msg.id || `s${msg.sequence_num ?? ''}`
}

/** 对话框只放纯文字：去掉叙事里的 Markdown 记号（粗体、标题、引用、行内代码） */
export function plainText(md: string): string {
  return md.replace(/\*\*|__|`/g, '').replace(/^\s*(#{1,6}|>)\s*/gm, '').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1$2')
}

/** 场景配图：不是一站，而是把背景换掉 */
export function sceneImageOf(msg: ChatMessage): string {
  const md = msg.metadata || {}
  return msg.type === 'system' && md.kind === 'illustration' && md.icat === 'scene' && typeof md.image === 'string'
    ? md.image
    : ''
}

function isCardMessage(msg: ChatMessage): boolean {
  const md = msg.metadata || {}
  if (msg.type === 'dice') return true
  if (msg.type !== 'system') return false
  return !!md.check_request || md.kind === 'handout' || md.kind === 'travel_suggest'
    || (md.kind === 'illustration' && md.icat !== 'scene')
}

/** 卡片在对话框里的那句摘要 */
function cardText(msg: ChatMessage): string {
  const md = msg.metadata || {}
  if (md.kind === 'handout') return `收到一份手书：${String(md.title || '').trim() || '（未命名）'}`
  if (md.kind === 'illustration') return String(md.title || msg.content || '').trim()
  return msg.content
}

/** 把消息流整理成一站一站。场景配图只换背景、不占一站；战斗机械结算行由调用方先滤掉。 */
export function buildStops(messages: ChatMessage[]): Stop[] {
  const out: Stop[] = []
  for (const msg of messages) {
    if (sceneImageOf(msg)) continue
    const md = msg.metadata || {}
    if (isCardMessage(msg)) {
      out.push({ msg, kind: 'card', text: cardText(msg) })
      continue
    }
    if (msg.type === 'system' && md.kind === 'module_intro') {
      const title = String(md.title || '').trim()
      out.push({ msg, kind: 'line', text: [title, msg.content].filter(Boolean).join('\n') })
      continue
    }
    const text = (msg.type === 'narration' ? plainText(stripCommandTags(msg.content)) : msg.content || '').trim()
    if (!text && !msg.id?.startsWith('stream-')) continue
    out.push({ msg, kind: 'line', text })
  }
  return out
}

/**
 * 长文分页：对话框一屏放得下的长度（缺省约 120 字）。尽量切在句末（。！？…换行），
 * 一句本身超长才硬切。流式中的叙事每次重算，新长出来的部分自然落进后面的页。
 */
export function paginate(text: string, max = 120): string[] {
  const clean = text.replace(/\n{3,}/g, '\n\n').trim()
  if (!clean) return ['']
  // 按句切：保留句末标点与紧随的引号
  const sentences = clean.match(/[^。！？!?…\n]+[。！？!?…]*[」』”"）)]*\n*|[\n]+/g) || [clean]
  const pages: string[] = []
  let cur = ''
  for (const s of sentences) {
    if ((cur + s).trim().length <= max) {
      cur += s
      continue
    }
    if (cur.trim()) pages.push(cur.trim())
    cur = ''
    let rest = s
    while (rest.trim().length > max) {
      pages.push(rest.slice(0, max).trim())
      rest = rest.slice(max)
    }
    cur = rest
  }
  if (cur.trim()) pages.push(cur.trim())
  return pages.length ? pages : ['']
}

export type SpeakerKind = 'kp' | 'npc' | 'player' | 'system'

export interface Speaker {
  kind: SpeakerKind
  /** 名牌上的字；KP 旁白与系统消息不挂名牌 */
  name: string
  /** NPC 立绘（相对路径，调用方补服务器前缀） */
  portrait: string
}

export function speakerOf(msg: ChatMessage): Speaker {
  const md = msg.metadata || {}
  const portrait = typeof md.portrait === 'string' ? md.portrait : ''
  if (msg.type === 'narration') return { kind: 'kp', name: '', portrait: '' }
  if (msg.type === 'system' || msg.type === 'dice') return { kind: 'system', name: '', portrait: '' }
  if (md.is_player) return { kind: 'player', name: msg.actor_name || '', portrait: '' }
  return { kind: msg.type === 'ooc' ? 'player' : 'npc', name: msg.actor_name || '', portrait }
}

/** 自动播放时这一页停多久：读完它的时间，至少 1.4 秒、至多 9 秒 */
export function dwellMs(text: string): number {
  return Math.min(9000, Math.max(1400, 900 + text.length * 55))
}
