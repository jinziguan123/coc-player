import { m } from 'motion/react'
import { spring } from '@/lib/motion'

/**
 * 黄铜表盘：把一次 d100 检定画成一只仪表。
 *
 * 弧从左（1）到右（100），按技能值切出 CoC 的成功档：极难（≤1/5）、困难（≤1/2）、
 * 常规（≤技能值）、失败，以及最右端的大失败区。指针从最左端甩到掷出值，带金属件的
 * 过冲回摆（spring.needle）——一眼看到「落在哪一档」，比读两个数字再心算快。
 *
 * 只有**新出现**的检定才甩针；回看历史时直接停在终点（animate=false），
 * 不然往上翻聊天记录会看到一屏指针在乱晃。
 */
const W = 132
const H = 76
const CX = W / 2
const CY = 68
const R = 56

/** 骰值 → 角度（度）：1 在正左（180°），100 在正右（0°） */
const angleOf = (v: number) => 180 - ((Math.min(100, Math.max(1, v)) - 1) / 99) * 180

function pointAt(deg: number, r: number): [number, number] {
  const rad = (deg * Math.PI) / 180
  return [CX + r * Math.cos(rad), CY - r * Math.sin(rad)]
}

function arc(from: number, to: number, r: number): string {
  const [x1, y1] = pointAt(angleOf(from), r)
  const [x2, y2] = pointAt(angleOf(to), r)
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
}

/** CoC 7e：技能 <50 时 96–100 为大失败，否则只有 100 */
function fumbleFrom(skill: number): number {
  return skill < 50 ? 96 : 100
}

export function BrassDial({
  roll, skill, target, animate = true,
}: {
  roll: number
  /** 切档用的技能值；缺省时用目标值代替 */
  skill?: number
  /** 本次要求的门槛（困难检定时是半值），在弧上标一道刻线 */
  target: number
  animate?: boolean
}) {
  const base = skill && skill > 0 ? skill : target
  const extreme = Math.max(1, Math.floor(base / 5))
  const hard = Math.max(extreme, Math.floor(base / 2))
  const regular = Math.min(99, Math.max(hard, base))
  const fumble = fumbleFrom(base)

  const bands = [
    { from: 1, to: extreme, cls: 'dial-band--extreme' },
    { from: extreme, to: hard, cls: 'dial-band--hard' },
    { from: hard, to: regular, cls: 'dial-band--regular' },
    { from: regular, to: fumble, cls: 'dial-band--fail' },
    { from: fumble, to: 100, cls: 'dial-band--fumble' },
  ].filter((b) => b.to > b.from || (b.from === b.to && b.from === 100))

  const needle = angleOf(roll)
  // 指针画成朝正上方（90°），旋转 90 - 角度 即指向目标；起点是最左端（-90°）
  const finalRotate = 90 - needle
  const [tx1, ty1] = pointAt(angleOf(target), R - 9)
  const [tx2, ty2] = pointAt(angleOf(target), R + 7)

  return (
    <span className="brass-dial" style={{ width: W, height: H }} aria-hidden>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}>
        {/* 外圈双线 */}
        <path d={arc(1, 100, R + 9)} className="dial-rim" />
        <path d={arc(1, 100, R + 6)} className="dial-rim dial-rim--thin" />
        {/* 成功档色带：错峰亮起 */}
        {bands.map((b, i) => (
          <m.path
            key={b.cls}
            d={arc(b.from, Math.max(b.to, b.from + 0.6), R)}
            className={`dial-band ${b.cls}`}
            initial={animate ? { opacity: 0 } : false}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.25, delay: animate ? 0.05 + i * 0.06 : 0 }}
          />
        ))}
        {/* 本次门槛刻线 */}
        <line x1={tx1} y1={ty1} x2={tx2} y2={ty2} className="dial-target" />
      </svg>
      {/* 指针用 HTML 元素而不是 SVG <g>：Motion 给 SVG 元素算旋转中心时按包围盒取，
          指针一头长一头短，中心落不到表心上；HTML 元素的 transform-origin 是确定的。 */}
      <m.span
        className="dial-needle"
        style={{ left: CX - 1, top: CY - (R - 4), height: R - 4, originX: 0.5, originY: 1 }}
        initial={animate ? { rotate: -90 } : false}
        animate={{ rotate: finalRotate }}
        transition={animate ? { ...spring.needle, delay: 0.25 } : { duration: 0 }}
      />
      <span className="dial-hub" style={{ left: CX - 4.5, top: CY - 4.5 }} />
    </span>
  )
}
