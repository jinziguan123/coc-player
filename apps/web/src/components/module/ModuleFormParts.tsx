import { useState, type ReactNode } from 'react'
import { csv, parseCsv } from '@/lib/csvList'

/** 模组编辑页的通用表单件：标签行、文本输入、逗号列表输入。 */

export function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="module-row flex gap-3 py-1 text-sm items-start">
      <span className="module-row-label flex-shrink-0 w-20 text-xs pt-1.5" style={{ color: 'var(--color-text-secondary)' }}>{label}</span>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  )
}

export function TextInput({ value, onChange, multiline, placeholder }: { value: string; onChange: (v: string) => void; multiline?: boolean; placeholder?: string }) {
  const cls = 'w-full px-2 py-1 rounded text-sm'
  const style = { background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', color: 'var(--color-text-primary)' }
  return multiline
    ? <textarea value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} rows={2} className={cls} style={style} />
    : <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={cls} style={style} />
}

/**
 * 逗号分隔的列表输入。输入框里保留用户敲的原文（草稿），只把解析结果往上报——
 * 若每次按键都「解析 → 再 join 回填」，末尾的逗号/空格会立刻被吃掉，键盘上永远敲不出第二项。
 */
export function CsvInput({ values, onChange, placeholder }: { values?: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(() => csv(values))
  // 外部改了值（撤销、换选中项等）且与草稿解析结果不一致时，以外部为准
  const external = csv(values)
  if (csv(parseCsv(draft)) !== external && draft !== external) {
    setDraft(external)
  }
  return <TextInput value={draft} placeholder={placeholder} onChange={(v) => { setDraft(v); onChange(parseCsv(v)) }} />
}
