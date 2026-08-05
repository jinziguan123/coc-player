// 文风 / 画风选择器：几档预设 + 自定义原文，模组默认值与本局设置共用同一个组件。
//
// 取值约定与后端 services/style_presets.py 一字不差：字段就是一个字符串——空串=不指定
// （模组处=不给默认，本局处=继承模组），命中预设 id 用该预设，其余一律当自定义原文。
// 所以这里不需要「是预设还是自定义」的第二个状态位；只在**渲染时**按值是否命中预设决定
// 下拉停在哪一档、要不要展开输入框。成对状态迟早会不同步，一个字段最省心。
import { useEffect, useRef, useState } from 'react'
import { GiQuillInk, GiPaintBrush } from 'react-icons/gi'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import type { StyleOption } from './useStyleOptions'

/** 下拉里两档非预设选项的哨兵值。
 *
 * Radix 的 SelectItem **不接受空字符串** value（会直接抛错），所以「跟随默认」这一档
 * 也得有个哨兵，不能直接用字段本身的空串语义；写回字段时再翻译回空串。
 * 两个哨兵都带下划线，绝不会和后端的 ASCII 预设 id 撞。
 */
const CUSTOM = '__custom__'
const INHERIT = '__inherit__'

export function StylePicker({
  kind, value, onChange, options, inheritLabel, disabled = false,
}: {
  kind: 'narrative' | 'image'
  value: string
  onChange: (next: string) => void
  options: StyleOption[]
  /** 空串那一档叫什么：模组处是「不指定」，本局处是「跟随模组」。 */
  inheritLabel: string
  disabled?: boolean
}) {
  const preset = options.find((o) => o.id === value)
  const isCustom = !!value && !preset
  // 下拉切到「自定义」时值还是空的，此时不能靠 value 判断该不该显示输入框，否则刚切过去
  // 输入框就消失了。用一个只在本次交互内有效的意图位，值一旦写进去就由 isCustom 接管。
  const [wantCustom, setWantCustom] = useState(false)
  const showCustom = isCustom || wantCustom

  // 切到「自定义」后把光标送进输入框。
  //
  // 不能只靠 textarea 的 autoFocus：Radix 关闭下拉时会**把焦点还给触发器**，而那件事发生在
  // 本次渲染之后，正好把 autoFocus 盖掉——表现成「选了自定义，敲字却什么都没进去」，
  // 用户得莫名其妙再点一下输入框。等下一帧再抢回来才稳。
  const customRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (!wantCustom) return
    const id = requestAnimationFrame(() => customRef.current?.focus())
    return () => cancelAnimationFrame(id)
  }, [wantCustom])

  const current = isCustom ? CUSTOM : (preset ? preset.id : INHERIT)
  const Icon = kind === 'narrative' ? GiQuillInk : GiPaintBrush

  return (
    <div className="space-y-2">
      <Select
        value={showCustom ? CUSTOM : current}
        disabled={disabled}
        onValueChange={(next) => {
          if (next === CUSTOM) {
            setWantCustom(true)
            return
          }
          setWantCustom(false)
          onChange(next === INHERIT ? '' : next)
        }}
      >
        <SelectTrigger>
          {/* 必须用 div 而不是 span 包：SelectTrigger 带着 `[&>span]:line-clamp-1`，
              line-clamp 会把 display 改成 -webkit-box，直接盖掉这里的 flex——
              图标与文字就竖着叠成两行了（浏览器实测）。div 不在那条选择器的射程内。 */}
          <div className="flex items-center gap-1.5 min-w-0">
            <Icon className="shrink-0 opacity-70" />
            <SelectValue placeholder={inheritLabel} />
          </div>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={INHERIT}>{inheritLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o.id} value={o.id}>
              {o.label}
              <span className="ml-2 text-xs opacity-60">{o.hint}</span>
            </SelectItem>
          ))}
          <SelectItem value={CUSTOM}>自定义…</SelectItem>
        </SelectContent>
      </Select>

      {showCustom && (
        <textarea
          ref={customRef}
          value={isCustom ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          rows={kind === 'narrative' ? 3 : 2}
          placeholder={
            kind === 'narrative'
              ? '用一段话描述你想要的行文方式，如「像武侠小说那样写，多用四字短语，动作场面要快」'
              : '英文关键词，直接拼给生图模型，如 art nouveau poster, flat colors, thick outlines'
          }
          className="w-full px-2 py-1.5 rounded text-xs resize-none"
          style={{ background: 'var(--color-input-bg)', border: '1px solid var(--color-border)' }}
        />
      )}

      {kind === 'image' && showCustom && (
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)', opacity: 0.75 }}>
          自定义画风只替换风格描述；内容红线（着装完整、非色情）由系统强制追加，改不掉。
        </p>
      )}
    </div>
  )
}
