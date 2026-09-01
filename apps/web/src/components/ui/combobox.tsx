/**
 * 可输入的下拉：填得了自由值，也挑得了候选。
 *
 * 用来顶掉 `<datalist>`。那玩意儿的弹层由系统绘制，在这套羊皮纸配色里会突兀地弹出一片
 * 深色系统菜单，跟站内其它下拉长得完全不是一回事（同样的理由见 RoomLobbyPage 里改掉
 * 原生 `<select>` 的那处注释）。它的交互也不好：要点右侧那个小箭头、或者先敲几个字才
 * 展开，可发现性约等于没有；桌面版跑在 WKWebView 里，支持还更不可靠。
 *
 * 与 `Select` 的分工：那个是「只能从给定项里选」，这个是「有候选，但也允许自己写」。
 * KP 填场景 ID、模型名这类都属于后者——清单未必全，手填这条路不能堵。
 *
 * 浮层走 Radix Popover 的 Portal：绝对定位的浮层会被祖先的 overflow 裁掉，而这些输入
 * 框常常就待在可滚动的面板里。
 */
import { useId, useRef, useState } from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { ChevronDown } from 'lucide-react'

export interface ComboboxOption {
  /** 填进输入框的值 */
  value: string
  /** 旁注：给一眼认不出 value 的情况用（场景 ID → 场景名） */
  hint?: string
}

export function Combobox({
  value, onChange, options, placeholder, className = '', ariaLabel, inputId,
}: {
  value: string
  onChange: (next: string) => void
  options: ComboboxOption[]
  placeholder?: string
  className?: string
  ariaLabel?: string
  /** 外部 label 要 htmlFor 时给 */
  inputId?: string
}) {
  const autoId = useId()
  const id = inputId ?? autoId
  const [open, setOpen] = useState(false)
  // 只有用户在框里**动过手**才按内容过滤。不看框里有没有值：编辑既有内容时它本来就
  // 填着一个候选，拿它去筛，一屏候选只会剩它自己。
  const [filtering, setFiltering] = useState(false)
  const anchorRef = useRef<HTMLDivElement>(null)

  const keyword = value.trim().toLowerCase()
  const shown = filtering && keyword
    ? options.filter((o) =>
        o.value.toLowerCase().includes(keyword) || (o.hint || '').toLowerCase().includes(keyword))
    : options

  const pick = (next: string) => {
    onChange(next)
    setFiltering(false)
    setOpen(false)
  }

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen} modal={false}>
      <PopoverPrimitive.Anchor asChild>
        <div ref={anchorRef} className={`relative min-w-0 flex-1 ${className}`}>
          <input
            id={id}
            type="text"
            className="input w-full text-xs"
            style={options.length > 0 ? { paddingRight: '1.9rem' } : undefined}
            placeholder={placeholder}
            value={value}
            aria-label={ariaLabel}
            role={options.length > 0 ? 'combobox' : undefined}
            aria-expanded={options.length > 0 ? open : undefined}
            aria-autocomplete={options.length > 0 ? 'list' : undefined}
            onChange={(e) => {
              onChange(e.target.value)
              setFiltering(true)
              if (options.length > 0) setOpen(true)
            }}
            onFocus={() => { if (options.length > 0) setOpen(true) }}
          />
          {options.length > 0 && (
            <button
              type="button"
              className="combo-toggle"
              onClick={() => setOpen((v) => !v)}
              aria-label={open ? '收起候选' : '展开候选'}
              tabIndex={-1}
            >
              <ChevronDown size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </PopoverPrimitive.Anchor>

      {shown.length > 0 && (
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content
            align="start"
            side="bottom"
            sideOffset={4}
            collisionPadding={12}
            className="combo-list z-[110] w-[var(--radix-popover-trigger-width)]"
            // 别把焦点从输入框抢走——它是个能继续打字的 combobox，不是纯菜单
            onOpenAutoFocus={(e) => e.preventDefault()}
            onCloseAutoFocus={(e) => e.preventDefault()}
            // 点回输入框或展开钮不算「点到别处」，否则会先关再开、闪一下
            onInteractOutside={(e) => {
              if (anchorRef.current?.contains(e.target as Node)) e.preventDefault()
            }}
          >
            <div role="listbox" aria-label={ariaLabel || '候选'}>
              {shown.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="option"
                  aria-selected={o.value === value}
                  data-active={o.value === value}
                  className="combo-option"
                  onClick={() => pick(o.value)}
                >
                  {o.value}
                  {o.hint && <span className="combo-option__hint">{o.hint}</span>}
                </button>
              ))}
            </div>
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      )}
    </PopoverPrimitive.Root>
  )
}
