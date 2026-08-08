import { useEffect, useRef, useState } from 'react'

/**
 * 数字输入框：**输入过程中绝不改写用户打的字**，规整只发生在失焦时。
 *
 * 这是在修一类反复出现的可用性 bug：把校验写进 onChange 后，用户根本打不出目标值——
 * 年龄框限定 15..90，想输 30 时第一个字符 `3` 就被判定小于 15 而钳成 15；
 * 写成 `Number(e.target.value) || 0` 的框同理，把 50 改成 80 得先删空，一删就变 0，
 * 再输入就成了 080。中间状态本来就是非法的，拿终值的规则去卡它必然卡错。
 *
 * 所以显示值来自内部草稿而非外部 value——外部 state 变成什么都不影响用户正在打的字。
 * 调用方照常在 onChange 里拿到实时数值（清空时是 undefined），做联动计算不受影响。
 *
 * 也刻意不下发 HTML 的 min/max：那会让浏览器弹自己的校验气泡，
 * 在这种「中间态必然越界」的输入里只会一直冒红。
 */
export function NumberField({
  value,
  onChange,
  min,
  max,
  /** 失焦时若为空，回落到这个值；不给则允许留空（onChange 收到 undefined）。 */
  fallback,
  className,
  style,
  ...rest
}: {
  value: number | undefined
  onChange: (value: number | undefined) => void
  min?: number
  max?: number
  fallback?: number
  className?: string
  style?: React.CSSProperties
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'min' | 'max'>) {
  const [draft, setDraft] = useState(value == null ? '' : String(value))
  const editing = useRef(false)

  // 外部值变了要能反映出来（如「重掷属性」批量改写）；但用户正在输入时绝不覆盖。
  useEffect(() => {
    if (!editing.current) setDraft(value == null ? '' : String(value))
  }, [value])

  return (
    <input
      {...rest}
      type="number"
      value={draft}
      className={className}
      style={style}
      onFocus={(e) => {
        editing.current = true
        rest.onFocus?.(e)
      }}
      onChange={(e) => {
        const raw = e.target.value
        setDraft(raw)
        if (raw === '') return onChange(undefined)
        const n = Number(raw)
        // NaN 只可能来自「-」「.」这类中间态：保留在框里，先不上报
        if (!Number.isNaN(n)) onChange(n)
      }}
      onBlur={(e) => {
        editing.current = false
        const raw = e.target.value
        let next: number | undefined
        if (raw === '') {
          next = fallback
        } else {
          const n = Number(raw)
          next = Number.isFinite(n) ? n : fallback
          if (next != null) {
            if (min != null) next = Math.max(min, next)
            if (max != null) next = Math.min(max, next)
          }
        }
        setDraft(next == null ? '' : String(next))
        onChange(next)
        rest.onBlur?.(e)
      }}
    />
  )
}
