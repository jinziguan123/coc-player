/**
 * 背景音乐的纯逻辑：此刻该放什么氛围、这个氛围有哪些曲子可放。
 *
 * 氛围优先级（越靠前越硬）：
 *   1. 战斗进行中 → combat；追逐进行中 → chase。前端本来就握着这两份状态，
 *      直接在这里判，不必让后端每条开战/开追路径都记得去改配乐；
 *   2. 我自己所在场景的 mood（后端已把「场景默认 + KP 临时覆盖」合成好，见 /locations）。
 *      取「我自己」的场景而不是会话的 current_scene_id：分头行动时各听各的；
 *   3. 都没有 → explore。
 */

export const DEFAULT_MOOD = 'explore'

/** 氛围标签 → 中文名（与后端 services/bgm.py 的 MOODS 同一份枚举） */
export const MOOD_LABELS: Record<string, string> = {
  calm: '安宁', explore: '调查', tense: '不安', horror: '恐怖',
  mythos: '神话', sorrow: '悲伤', combat: '战斗', chase: '追逐',
}

/** 真人 KP 可手动指定的氛围（战斗/追逐由系统自动切）；auto = 回到场景默认 */
export const KP_MOOD_OPTIONS: Array<{ value: string; hint: string }> = [
  ...['calm', 'explore', 'tense', 'horror', 'mythos', 'sorrow'].map((m) => ({ value: m, hint: MOOD_LABELS[m] })),
  { value: 'auto', hint: '回到场景默认' },
]

export function deriveBgmMood(input: {
  inCombat: boolean
  inChase: boolean
  locations: { current: boolean; mood?: string }[]
}): string {
  if (input.inCombat) return 'combat'
  if (input.inChase) return 'chase'
  return input.locations.find((l) => l.current)?.mood || DEFAULT_MOOD
}

/**
 * 某氛围可播的曲目。该氛围没有曲子就回落到 explore（宁可放一首中性的，也别让
 * 新加的氛围在曲库没补齐前把整局变成静音）；explore 也没有就返回空 = 静音。
 */
export function tracksForMood(tracks: Record<string, string[]>, mood: string): string[] {
  const own = tracks[mood]
  if (own && own.length) return own
  return tracks[DEFAULT_MOOD] || []
}

/** 从曲目列表里挑下一首：多于一首时避开刚放过的那首，免得同一首连放两遍。 */
export function pickTrack(list: string[], previous: string | null, rand: () => number = Math.random): string | null {
  if (!list.length) return null
  const pool = list.length > 1 ? list.filter((t) => t !== previous) : list
  return pool[Math.floor(rand() * pool.length)] ?? pool[0]
}
