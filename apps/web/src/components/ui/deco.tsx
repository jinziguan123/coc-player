/**
 * 装饰艺术（Art Deco）纹饰原语。
 *
 * 这些是**结构纹饰**而非图标：扇形日芒、四角回纹、三线分隔。全部用 currentColor 描边，
 * 颜色跟随所在容器（默认黄铜），羊皮纸主题下自然变成深红墨线。
 *
 * 克制使用：日芒只在品牌位与「当前这一局」的焦点处出现；四角回纹只给检定卡与档案这类
 * 「一份正式文件」用。每张卡都镶边，纹饰就成了噪音。
 */
import type { SVGProps } from 'react'

/** 扇形日芒：品牌标与空状态的母题 */
export function DecoSunburst(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 64 34" fill="none" aria-hidden {...props}>
      <path d="M2 33 A30 30 0 0 1 62 33" stroke="currentColor" strokeWidth="1.2" />
      <path d="M10 33 A22 22 0 0 1 54 33" stroke="currentColor" strokeWidth="1" />
      <path d="M32 33 L32 3 M32 33 L13 10 M32 33 L51 10 M32 33 L5 22 M32 33 L59 22" stroke="currentColor" strokeWidth="1" />
      <path d="M26 33 A6 6 0 0 1 38 33 Z" fill="currentColor" />
    </svg>
  )
}

/** 四角回纹：放在 position:relative 的容器里，四只角各一个 */
export function DecoCorners({ className = '' }: { className?: string }) {
  return (
    <span className={`deco-corners ${className}`} aria-hidden>
      {(['tl', 'tr', 'bl', 'br'] as const).map((pos) => (
        <svg key={pos} viewBox="0 0 26 26" className={`deco-corner deco-corner--${pos}`}>
          <path d="M1 25 V1 H25" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M6 25 V6 H25" fill="none" stroke="currentColor" strokeWidth="1" />
          <rect x="10" y="10" width="5" height="5" fill="currentColor" />
        </svg>
      ))}
    </span>
  )
}

/** 三线分隔：区块之间的「封口」，比单根细线更有分量 */
export function DecoRule({ className = '' }: { className?: string }) {
  return <div className={`deco-rule ${className}`} aria-hidden />
}
