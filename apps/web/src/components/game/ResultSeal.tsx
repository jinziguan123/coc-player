import { m } from 'motion/react'
import { spring } from '@/lib/motion'

/**
 * 朱砂印章：只给大成功 / 大失败。
 *
 * 一局里这两种结果罕见，正因为罕见才值得「盖章」——每次普通成功都盖，印章就成了噪音。
 * 大成功是朱文印（红字红框、留白底），大失败是白文印（红底镂出字），两种一眼可辨。
 * 文字从左往右读：传统印章自右而左，但「功成大」这种读法会让玩家先愣一下。
 *
 * 落章动作：从放大、倾斜、透明处「砸」下来，带一点回弹——像真有人按了一下。
 */
export function ResultSeal({ kind, animate = true }: { kind: 'critical' | 'fumble'; animate?: boolean }) {
  const text = kind === 'critical' ? '大成功' : '大失败'
  return (
    <m.span
      className={`result-seal result-seal--${kind}`}
      role="img"
      aria-label={text}
      initial={animate ? { opacity: 0, scale: 1.9, rotate: -18 } : false}
      animate={{ opacity: 1, scale: 1, rotate: kind === 'critical' ? -7 : 6 }}
      transition={animate ? { ...spring.pop, delay: 0.95 } : { duration: 0 }}
    >
      <span className="result-seal-text">{text}</span>
    </m.span>
  )
}
