/**
 * KP 工作台的表单输入：有候选就是可输入的下拉，没有就是普通输入框。
 *
 * 原先靠 `<datalist>`——弹层由系统绘制，羊皮纸主题里会突兀地弹出一片深色系统菜单，
 * 跟站内其它下拉完全不是一路（同样的理由见 RoomLobbyPage 里换掉原生 `<select>` 的
 * 那处注释）。换成项目自己的 Combobox。
 *
 * 单独成文件是给 HumanKpPanel 腾地方——它顶着行数红线，按约定新增内容进新文件。
 */
import { Combobox, type ComboboxOption } from '@/components/ui/combobox'

interface KpInputProps {
  name: string
  placeholder: string
  /** 有候选就走 Combobox；没有就是个普通输入框。 */
  options?: ComboboxOption[]
  fields: Record<string, string>
  onChange: (name: string, value: string) => void
}

export function KpInput({ name, placeholder, options, fields, onChange }: KpInputProps) {
  // 原先靠 <datalist>，弹层由系统绘制——羊皮纸主题里会弹出一片深色系统菜单，
  // 跟站内其它下拉完全不是一路。见 components/ui/combobox.tsx。
  if (options) {
    return (
      <Combobox
        value={fields[name] || ''}
        onChange={(v) => onChange(name, v)}
        options={options}
        placeholder={placeholder}
        ariaLabel={placeholder}
      />
    )
  }
  return (
    <input
      value={fields[name] || ''}
      onChange={(event) => onChange(name, event.target.value)}
      placeholder={placeholder}
      className="input min-w-0 flex-1 text-xs"
    />
  )
}
