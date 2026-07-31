import { useState, type CSSProperties } from 'react'
import { Copy, Eye, EyeOff } from 'lucide-react'
import { toast } from 'sonner'

// API Key 输入框内嵌的图标小按钮（显示/隐藏、复制）
const KEY_ICON_BTN: CSSProperties = {
  background: 'transparent',
  border: 'none',
  padding: '0.25rem',
  cursor: 'pointer',
  color: 'var(--color-text-secondary)',
  display: 'flex',
  alignItems: 'center',
}

interface Props {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  hint?: string
  /** 取明文：列表接口恒掩码，显示/复制前先向后端要真实值。返回明文并由调用方回填表单。 */
  revealKey: () => Promise<string>
}

/**
 * API Key 输入框：右侧内嵌「显示/隐藏」与「复制」。
 *
 * 对话模型与生图模型两套配置都要它，因此抽出来共用——两处各写一遍的下场是
 * 掩码回填规则（含 `****` 即视为未修改）在一处被改漏。
 */
export function ApiKeyField({ value, onChange, placeholder, hint, revealKey }: Props) {
  const [shown, setShown] = useState(false)

  const toggle = async () => {
    if (!shown) {
      try {
        await revealKey()
      } catch {
        toast.error('获取 API Key 失败')
        return
      }
    }
    setShown((v) => !v)
  }

  const copy = async () => {
    try {
      const key = await revealKey()
      if (!key) {
        toast.error('该配置未填 API Key')
        return
      }
      await navigator.clipboard.writeText(key)
      toast.success('API Key 已复制到剪贴板')
    } catch {
      toast.error('复制失败')
    }
  }

  return (
    <div>
      <label className="block text-sm font-semibold mb-1" style={{ fontSize: '0.85rem' }}>
        API Key
      </label>
      <div style={{ position: 'relative' }}>
        <input
          type={shown ? 'text' : 'password'}
          className="input w-full"
          style={{ paddingRight: '4.4rem' }}
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <div
          style={{
            position: 'absolute',
            right: '0.45rem',
            top: '50%',
            transform: 'translateY(-50%)',
            display: 'flex',
            gap: '0.15rem',
          }}
        >
          <button
            type="button"
            title={shown ? '隐藏 API Key' : '显示 API Key'}
            onClick={toggle}
            style={KEY_ICON_BTN}
          >
            {shown ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
          <button type="button" title="复制 API Key" onClick={copy} style={KEY_ICON_BTN}>
            <Copy size={15} />
          </button>
        </div>
      </div>
      {hint && (
        <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
          {hint}
        </p>
      )}
    </div>
  )
}
