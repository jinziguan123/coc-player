/**
 * @ 补全的候选从哪来。
 *
 * 只有「有稳定身份、且玩家本来就看得见」的东西才进这张表：
 *   - 队友 / 自己带的调查员：参会席位里就有名字；
 *   - 已知地点：走 useKnownLocations，**可见性过滤在后端**（已访问 ∪ 当前 ∪ 玩家可见叙事里
 *     提及过的）——绝不能改用 KP 工作台那份 catalogs.scenes，那是模组全量、不筛可见性，
 *     拿来喂玩家的补全等于把没探索过的地名抖出来；
 *   - 技能：角色卡上已有的技能名。
 *
 * 刻意不收的两类：线索（台账 id 是临场生成的 UUID，名字也不稳，补一个「起不出来的名字」
 * 比不补还糟）、模组 NPC（名字由 KP 临场定，本人未必在场）。
 */

export type MentionKind = 'character' | 'location' | 'skill'

/** 一级入口（分组）的名字。用「已知道的地点」而不是「场景」：场景是 KP 的词，
 *  玩家嘴里的说法是「去过哪儿」。 */
export const KIND_LABELS: Record<MentionKind, string> = {
  character: '队友',
  location: '已知地点',
  skill: '技能',
}

/** 一级入口只列有内容的组：点进去是空的，比没有这个入口更糟。 */
export function countByKind(candidates: MentionCandidate[]): Array<{ kind: MentionKind; count: number }> {
  const order: MentionKind[] = ['character', 'location', 'skill']
  return order
    .map((kind) => ({ kind, count: candidates.reduce((n, c) => n + (c.kind === kind ? 1 : 0), 0) }))
    .filter((g) => g.count > 0)
}

export function candidatesOfKind(candidates: MentionCandidate[], kind: MentionKind): MentionCandidate[] {
  return candidates.filter((c) => c.kind === kind)
}

export interface MentionCandidate {
  kind: MentionKind
  /** 插入的正文。名字里带 @ 会把下一次触发判定搅乱，这里过滤掉。 */
  label: string
  /** 行尾的浅色说明：地点是「当前/已到过」，技能是值。 */
  hint?: string
  /** 过滤时一并参与匹配但不出现在行尾的词（职业、别名）。 */
  extra?: string
}

/** 席位形状只取需要的字段，不把整个 Participant 类型拽进来。 */
export interface PartySeatLike {
  character_id?: string | null
  character_name?: string | null
  role?: string | null
}

export interface LocationLike {
  id: string
  name: string
  current?: boolean
  visited?: boolean
}

/** 分组顺序：先用队友，再用地点，最后技能——按「一句话里最常提到谁」排。 */
const KIND_ORDER: Record<MentionKind, number> = { character: 0, location: 1, skill: 2 }

const MAX_PER_KIND = 40

export function buildCandidates(input: {
  seats?: PartySeatLike[] | null
  locations?: LocationLike[] | null
  /** 角色卡上的技能：名 → 值。 */
  skills?: Record<string, number> | null
}): MentionCandidate[] {
  const out: MentionCandidate[] = []
  const seen = new Set<string>()

  const push = (c: MentionCandidate) => {
    if (!c.label || c.label.includes('@')) return
    const key = `${c.kind}:${c.label}`
    if (seen.has(key)) return
    seen.add(key)
    out.push(c)
  }

  for (const seat of input.seats || []) {
    if (!seat?.character_id || seat.role === 'kp') continue
    push({ kind: 'character', label: String(seat.character_name || '').trim() })
  }

  const locations = [...(input.locations || [])]
  // 当前所在排最前：玩家最常提的就是「这儿」。
  locations.sort((a, b) => Number(!!b.current) - Number(!!a.current))
  for (const loc of locations.slice(0, MAX_PER_KIND)) {
    push({
      kind: 'location',
      label: String(loc.name || '').trim(),
      hint: loc.current ? '当前' : loc.visited ? '已到过' : undefined,
    })
  }

  for (const [name, value] of Object.entries(input.skills || {})) {
    push({ kind: 'skill', label: name, hint: typeof value === 'number' ? `${value}` : undefined })
  }

  return out.sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind])
}

/**
 * 按查询词过滤并排序。打分只分四档，够用了：
 *   0 完全相同 · 1 前缀 · 2 名字里包含 · 3 只在别名/职业里包含
 * 同档内按分组顺序、再按名字排——**不用「最近用过」那套权重**：一局里的输入没有那么多，
 * 而「上次点过谁就永远排前面」会让候选顺序变得不可预测。
 */
export function filterCandidates(
  candidates: MentionCandidate[],
  query: string,
): MentionCandidate[] {
  const q = query.trim().toLowerCase()
  if (!q) return candidates
  const scored: Array<{ c: MentionCandidate; score: number; exact: boolean }> = []
  for (const c of candidates) {
    const label = c.label.toLowerCase()
    const extra = (c.extra || '').toLowerCase()
    let score: number | null = null
    if (label === q) score = 0
    else if (label.startsWith(q)) score = 1
    else if (label.includes(q)) score = 2
    else if (extra.includes(q)) score = 3
    if (score == null) continue
    scored.push({ c, score, exact: label === q })
  }
  return scored
    // 打全了名字的那一条永远最前：玩家已经把「侦查」写完了，就不该被「侦查所」顶掉。
    .sort((a, b) => Number(b.exact) - Number(a.exact)
      || a.score - b.score
      || KIND_ORDER[a.c.kind] - KIND_ORDER[b.c.kind]
      // 同档里名字短的靠前：「侦」想找的多半是「侦查」而不是「侦查所」。
      || a.c.label.length - b.c.label.length
      || a.c.label.localeCompare(b.c.label, 'zh'))
    .map((s) => s.c)
}
