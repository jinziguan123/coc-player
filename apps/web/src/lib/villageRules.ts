// 本桌规矩的短标签：把村规的**差异项**翻成玩家一眼能读的几个词。
//
// 放在 lib 而不是组件文件里：它是纯函数、也是被单测钉住的那部分，
// 和组件混在一个文件里还会让 Fast Refresh 失效。

/** 差异项 → 给玩家看的短标签。相关项合并成一条，省得「幸运消费」「单次上限」各占一格。 */
export function ruleDiffLabels(options: Record<string, unknown>): string[] {
  const o = options || {}
  const has = (k: string) => o[k] !== undefined
  const out: string[] = []

  if (has('critical_max')) out.push(`大成功 ≤ ${o.critical_max}`)
  if (o.fumble_rule === 'hundred_only') out.push('大失败只认 100')
  if (o.fumble_rule === 'ninety_six_plus') out.push('大失败 96 起（不看技能）')
  if (has('dice_pool_cap')) {
    out.push(o.dice_pool_cap === 0 ? '不使用奖惩骰' : `奖惩骰最多 ${o.dice_pool_cap} 个`)
  }

  if (o.luck_spend) {
    const extra: string[] = []
    if (o.luck_spend_max) extra.push(`单次 ≤ ${o.luck_spend_max} 点`)
    if (o.luck_spend_in_combat === false) extra.push('战斗中不可用')
    if (o.luck_spend_blocks_improvement === false) extra.push('仍计成长')
    out.push(`幸运消费${extra.length ? `（${extra.join('、')}）` : ''}`)
  }
  // 非官方规则，标出来——玩家有权知道哪条不是规则书里的
  if (o.luck_reroll) out.push(`燃运重骰 ${o.luck_reroll_cost ?? 10} 点·本桌自定`)

  if (has('major_wound_divisor')) out.push(`重伤线 1/${o.major_wound_divisor}`)
  if (o.insanity_rule === 'flat') {
    out.push(`临时疯狂 ${o.insanity_flat_threshold ?? 5} 点`)
  }
  if (o.improvement === false) out.push('不做成长检定')
  return out
}
