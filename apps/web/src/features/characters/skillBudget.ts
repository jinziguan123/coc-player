/**
 * 建卡时的技能点数规则（CoC 7 版）。
 *
 * 抽出来是因为这两条约束原先只活在 JSX 的 disabled 表达式里，写漏了没人发现：
 * 建卡页可以把技能加到 200%，信用评级也不花本职点——两条都是规则书写死的硬约束。
 */

/** 建卡阶段的技能值上限。克苏鲁神话不走这套（它根本不可加点）。 */
export const SKILL_MAX = 90

/**
 * 本职点剩余。信用评级按规则从本职技能点里出——它是一项本职技能，
 * 不是免费附赠的属性，白拿等于凭空多几十点。
 */
export function remainingOccPoints(
  occPoints: number,
  allocated: number,
  creditRating: number,
): number {
  return occPoints - allocated - creditRating
}

/**
 * 信用评级滑杆的实际可选上限。
 *
 * 职业规定的区间是一层，「本职点够不够付」是另一层，取更紧的。下限恒为 credit_min：
 * 那是职业的准入门槛，付不起也得付（付不起说明这个职业配这组属性本就勉强，
 * 让剩余点数显示成负数比悄悄降低门槛更诚实）。
 */
export function creditRatingCeiling(opts: {
  creditMin: number
  creditMax: number
  occPoints: number
  allocated: number
}): number {
  const affordable = opts.occPoints - opts.allocated
  return Math.max(opts.creditMin, Math.min(opts.creditMax, affordable))
}

/**
 * 一次加/减点实际能生效多少，0 表示这一下点不动。
 *
 * 加点同时受两条约束——池子里剩多少、技能值离 90 还有多远——取更紧的那条；
 * 减点只能退还自己加过的，退不到基础值以下。
 */
export function grantableDelta(opts: {
  /** 技能当前总值（基础 + 已加点） */
  current: number
  /** 已经加上去的点数（决定最多能退多少） */
  alloc: number
  /** 想要的增减量 */
  delta: number
  /** 对应点池剩余 */
  remaining: number
  cap?: number
}): number {
  const { current, alloc, delta, remaining } = opts
  const cap = opts.cap ?? SKILL_MAX
  if (delta === 0) return 0
  if (delta < 0) return alloc === 0 ? 0 : -Math.min(-delta, alloc)   // 避免返回 -0
  return Math.max(0, Math.min(delta, remaining, cap - current))
}
