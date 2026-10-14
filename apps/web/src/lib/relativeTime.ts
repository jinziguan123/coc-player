/**
 * 「上次 2 小时前」这类相对时间。只回答「多久没碰这桌了」，不追求精确：
 * 一分钟内统称「刚刚」，超过一周就直接给日期——「43 天前」比「8 月 31 日」难读。
 */
export function relativeTime(iso: string | undefined | null, now: Date = new Date()): string | null {
  if (!iso) return null
  // 后端的 datetime 不带时区后缀（UTC 落库），补个 Z，否则会被当成本地时间差出几个小时
  const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  const sec = (now.getTime() - t.getTime()) / 1000
  if (Number.isNaN(sec)) return null
  if (sec < 60) return '刚刚'
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`
  if (sec < 86400 * 7) return `${Math.floor(sec / 86400)} 天前`
  return `${t.getMonth() + 1} 月 ${t.getDate()} 日`
}
