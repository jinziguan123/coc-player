import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'

/**
 * NPC 性别的查看/编辑控件。
 *
 * 为什么这个字段值得单独占一格：解析漏填时，守秘人只能按名字猜，而外文译名
 * （加布里埃尔、艾希礼）在中文里根本看不出性别——猜错一次，那个角色整局都会被写成
 * 另一个性别（实测：马卡里奥家的姐妹被叙述成了兄弟）。
 *
 * **留空是合法状态**，不是待办事项：非人怪物、群体条目、原文确实没交代的，
 * 硬填一个反而更糟。所以查看态显示「—」而不是藏起来——没填这件事本身要让人看见。
 */
const LABELS: Record<string, string> = { male: '男', female: '女' }

/** Radix 的 Select 不接受空串作为 value，用哨兵值表示「未指定」。 */
const NONE = '__none'

export function GenderPicker({ value, edit, onChange }: {
  value?: string
  edit: boolean
  onChange: (value: string) => void
}) {
  if (!edit) return <span>{LABELS[value || ''] || '—'}</span>
  return (
    <Select value={value || NONE} onValueChange={(v) => onChange(v === NONE ? '' : v)}>
      <SelectTrigger className="w-28 min-w-0" aria-label="性别"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>未指定</SelectItem>
        <SelectItem value="male">男</SelectItem>
        <SelectItem value="female">女</SelectItem>
      </SelectContent>
    </Select>
  )
}
