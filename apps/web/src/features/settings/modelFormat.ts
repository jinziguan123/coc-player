/** 检索模型 / 精排模型设置卡共用的小工具。 */

export const BADGE = { display: 'inline-flex', alignItems: 'center', gap: '0.2rem' } as const

export function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  return `${Math.max(1, Math.round(n / 1e6))} MB`
}
