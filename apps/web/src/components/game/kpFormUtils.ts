/** 真人 KP 表单里的逗号清单：既要接 MultiSelect 的数组，也要接旧字段的字符串。 */
export function splitCsv(value?: string): string[] {
  return (value || '')
    .split(/[,，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function stripCharacterRef(ref: string): string {
  return ref.replace(/^character:/, '').replace(/^char:/, '')
}

export function stripNpcRef(ref: string): string {
  return ref.replace(/^npc:/, '')
}

/** 逃方旧字段可能是 character:<id>，MultiSelect 只认裸 id。 */
export function characterIds(value?: string): string[] {
  return splitCsv(value).map(stripCharacterRef)
}
