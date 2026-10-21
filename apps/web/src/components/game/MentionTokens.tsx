/**
 * 提到的小签：把「@纳迪娅·哈桑」画成一枚带图标与语义色的 token，而不是一段普通文字。
 *
 * 图标与配色跟 @ 补全菜单同一套（谁/哪/查三色），于是「在菜单里选的是它」与
 * 「正文里出现的是它」在视觉上是同一个东西。
 */
import { GiCharacter, GiPositionMarker, GiRollingDices } from 'react-icons/gi'

import { mentionsIn, splitMentions } from './mentionParts'
import type { MentionCandidate, MentionKind } from './mentionSources'

const KIND_ICON = {
  character: GiCharacter,
  location: GiPositionMarker,
  skill: GiRollingDices,
} as const

export function MentionChip({ name, kind }: { name: string; kind: MentionKind }) {
  const Icon = KIND_ICON[kind]
  return (
    <span className={`mention-chip mention-chip--${kind}`}>
      <Icon size={11} aria-hidden="true" />
      @{name}
    </span>
  )
}

/** 正文渲染：可识别的提到变成小签，其余原样。 */
export function MentionText({ text, candidates }: { text: string; candidates: MentionCandidate[] }) {
  const parts = splitMentions(text, candidates)
  return (
    <>
      {parts.map((part, i) => (
        part.type === 'mention'
          ? <MentionChip key={i} name={part.name} kind={part.kind} />
          : <span key={i}>{part.text}</span>
      ))}
    </>
  )
}

/** 草稿里的「已提到」条：让选中之后当场看得见提到的到底是谁。 */
export function MentionStrip({ text, candidates }: { text: string; candidates: MentionCandidate[] }) {
  const hits = mentionsIn(text, candidates)
  if (!hits.length) return null
  return (
    <div className="mention-strip">
      <span className="mention-strip-label">已提到</span>
      {hits.map((part) => (
        part.type === 'mention'
          ? <MentionChip key={part.name} name={part.name} kind={part.kind} />
          : null
      ))}
    </div>
  )
}
