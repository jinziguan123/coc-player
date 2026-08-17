/** 角色卡在列表/选人界面里的「一眼摘要」字段——从完整卡面里抽出来的短信息。 */

interface DigestCharacter {
  system_data?: Record<string, unknown> | null
  skills?: Record<string, number> | null
}

/** 从 system_data 里取一眼能看懂的摘要字段——角色列表要长得像角色列表，不是一排名字。 */
export function occupationOf(c: DigestCharacter): string {
  const sd = c.system_data || {}
  return String(sd.occupation || sd.profession || '').trim()
}

export function ageOf(c: DigestCharacter): string {
  const age = (c.system_data || {}).age
  return age ? `${age} 岁` : ''
}

/** 取 HP/SAN 当前值（形如 {current, max}）；缺失返回空串，不占位。 */
export function vitalOf(c: DigestCharacter, key: 'hitPoints' | 'sanity'): string {
  const v = (c.system_data || {})[key] as { current?: number; max?: number } | undefined
  if (!v || v.current == null) return ''
  return v.max != null ? `${v.current}/${v.max}` : String(v.current)
}

/** 技能里最高的几项——最能说明「这个人擅长什么」。 */
export function topSkills(c: DigestCharacter, n = 3): string[] {
  return Object.entries(c.skills || {})
    .filter(([, v]) => typeof v === 'number' && v >= 50)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([k, v]) => `${k} ${v}`)
}
