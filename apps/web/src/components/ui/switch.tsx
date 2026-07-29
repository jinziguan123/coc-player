import type { ReactNode } from 'react'

interface Props {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  /** 无障碍标签；有可见标题时传标题文字即可。 */
  label: string
  /** 开/关各自的短文案，显示在开关右侧。省略则不显示。 */
  onText?: ReactNode
  offText?: ReactNode
}

/**
 * 开关。给「一个布尔设置」用——状态一眼可见，点击即切换。
 *
 * 此前这类设置写成了「已允许 · 点击关闭」这样的宽文字按钮：既要读完整句才知道
 * 当前是开是关，又把状态和动作挤在同一行字里。开关把两者分开——形态表示状态，
 * 点击表示动作。
 */
export function Switch({ checked, onChange, disabled, label, onText, offText }: Props) {
  const text = checked ? onText : offText
  return (
    <span className="switch-field">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`switch${checked ? ' switch--on' : ''}`}
      >
        <span className="switch-knob" aria-hidden="true" />
      </button>
      {text ? (
        <span
          className="switch-state"
          aria-live="polite"
          style={{ color: checked ? 'var(--color-text-accent)' : 'var(--color-text-secondary)' }}
        >
          {text}
        </span>
      ) : null}
    </span>
  )
}
