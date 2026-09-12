/** 自定义请求头在表单里按「每行一个：名称: 值」填写，存到后端是对象。两个方向的转换放一处。 */

/** 文本 → 对象。冒号取第一个（值里可能还有冒号，如 URL），空行与没有冒号的行跳过，名称与值去首尾空白。 */
export function parseHeaderLines(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const idx = line.search(/[:：]/)
    if (idx <= 0) continue
    const name = line.slice(0, idx).trim()
    const value = line.slice(idx + 1).trim()
    if (name && value) out[name] = value
  }
  return out
}

/** 对象 → 文本，供编辑态回填。 */
export function formatHeaderLines(headers: Record<string, string> | undefined | null): string {
  return Object.entries(headers || {})
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n')
}
