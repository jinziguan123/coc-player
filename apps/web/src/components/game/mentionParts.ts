/**
 * 把一段文本里的 @提到 切出来：纯函数，给消息气泡与草稿条共用。
 *
 * 只有**能对上一个已知候选**的 @名字 才算提到：认不出来的 @xxx 原样留在文本里。
 * 否则「钱要花在@什么地方」这种句子会被切出一个假 token——玩家的正文不该被我们的
 * 渲染规则改写。
 *
 * 名字按长度从长到短试：「艾琳」与「艾琳·卡特」同时在册时，先试长的才不会被短名抢走前缀。
 */
import { isBoundaryChar } from './mentionTrigger'
import type { MentionCandidate, MentionKind } from './mentionSources'

export type MentionPart =
  | { type: 'text'; text: string }
  | { type: 'mention'; name: string; kind: MentionKind }

export function splitMentions(text: string, candidates: MentionCandidate[]): MentionPart[] {
  if (!text) return []
  if (!text.includes('@') || candidates.length === 0) return [{ type: 'text', text }]

  const byLength = [...candidates].sort((a, b) => b.label.length - a.label.length)
  const parts: MentionPart[] = []
  let buf = ''
  let i = 0
  while (i < text.length) {
    if (text[i] === '@' && isBoundaryChar(text.slice(0, i).slice(-1))) {
      const hit = byLength.find((c) => text.startsWith(c.label, i + 1))
      if (hit) {
        if (buf) { parts.push({ type: 'text', text: buf }); buf = '' }
        parts.push({ type: 'mention', name: hit.label, kind: hit.kind })
        i += 1 + hit.label.length
        continue
      }
    }
    buf += text[i]
    i += 1
  }
  if (buf) parts.push({ type: 'text', text: buf })
  return parts
}

/** 文本里出现过哪些提到（去重、保持首次出现顺序）——草稿条用。 */
export function mentionsIn(text: string, candidates: MentionCandidate[]): MentionPart[] {
  const seen = new Set<string>()
  const out: MentionPart[] = []
  for (const part of splitMentions(text, candidates)) {
    if (part.type !== 'mention' || seen.has(part.name)) continue
    seen.add(part.name)
    out.push(part)
  }
  return out
}
