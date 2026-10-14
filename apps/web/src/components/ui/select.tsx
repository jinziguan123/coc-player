import * as React from 'react'
import * as SelectPrimitive from '@radix-ui/react-select'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { popoverVariants } from '@/lib/motion'
import { useControllableOpen } from '@/lib/useControllableOpen'

/**
 * 退场动画：与 dialog.tsx 同一做法——Root 把开合状态接出来，SelectContent 据此用
 * forceMount + AnimatePresence 自己决定何时卸载。
 *
 * Select 比 Dialog 多一个坑：关着的时候 Radix 并不是什么都不渲染，而是把选项渲染进一个
 * 离屏 DocumentFragment，触发器上显示的「当前值文字」就是从那里的选中项传送过来的。
 * 所以 forceMount 的那份退场播完之后，必须换回不带 forceMount 的普通 Content，
 * 否则触发器会掉回占位文字。
 */
const SelectOpenContext = React.createContext(false)

function Select({ open, defaultOpen, onOpenChange, ...props }: React.ComponentProps<typeof SelectPrimitive.Root>) {
  const [value, setValue] = useControllableOpen({ open, defaultOpen, onOpenChange })
  return (
    <SelectOpenContext.Provider value={value}>
      <SelectPrimitive.Root open={value} onOpenChange={setValue} {...props} />
    </SelectOpenContext.Provider>
  )
}
const SelectGroup = SelectPrimitive.Group
const SelectValue = SelectPrimitive.Value

const SelectTrigger = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Trigger
    ref={ref}
    className={cn(
      'flex h-9 w-full items-center justify-between rounded-[3px] border px-3 py-2 text-sm',
      'focus:outline-none disabled:cursor-not-allowed disabled:opacity-50',
      '[&>span]:line-clamp-1',
      className,
    )}
    style={{
      borderColor: 'var(--color-border-strong)',
      background: 'var(--color-input-bg)',
      color: 'var(--color-text-primary)',
      fontFamily: 'var(--font-ui)',
    }}
    {...props}
  >
    {children}
    <SelectPrimitive.Icon asChild>
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="ml-2 opacity-60">
        <path d="M3 5L6 8L9 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
))
SelectTrigger.displayName = SelectPrimitive.Trigger.displayName

const SelectContent = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = 'popper', ...props }, ref) => {
  const open = React.useContext(SelectOpenContext)
  // 退场播完前一直由 forceMount 那份顶着（它同样会把选中项文字送回触发器）
  const [exiting, setExiting] = React.useState(false)
  if (open && !exiting) setExiting(true)

  const viewport = (
    <SelectPrimitive.Viewport
      className={cn(
        'p-1',
        position === 'popper' && 'h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)]',
      )}
    >
      {children}
    </SelectPrimitive.Viewport>
  )

  return (
    <>
      <AnimatePresence onExitComplete={() => setExiting(false)}>
        {open && (
          <SelectPrimitive.Portal forceMount key="select">
            <SelectPrimitive.Content ref={ref} forceMount asChild position={position} {...props}>
              <m.div
                className={cn(
                  'relative z-[110] max-h-72 min-w-[8rem] overflow-hidden rounded-md border shadow-lg',  // 高于 Modal 的 z-[100]，否则弹窗内的下拉被遮罩压住点不到
                  position === 'popper' && 'data-[side=bottom]:translate-y-1 data-[side=top]:-translate-y-1',
                  className,
                )}
                variants={popoverVariants}
                initial="initial"
                animate="enter"
                exit="exit"
                style={{
                  background: 'var(--color-bg-secondary)',
                  borderColor: 'var(--color-border-strong)',
                  fontFamily: 'var(--font-ui)',
                  transformOrigin: 'var(--radix-select-content-transform-origin)',
                }}
              >
                {viewport}
              </m.div>
            </SelectPrimitive.Content>
          </SelectPrimitive.Portal>
        )}
      </AnimatePresence>
      {/* 关着时的离屏那份：只为让触发器拿得到当前值文字 */}
      {!open && !exiting && (
        <SelectPrimitive.Content position={position} {...props}>{viewport}</SelectPrimitive.Content>
      )}
    </>
  )
})
SelectContent.displayName = SelectPrimitive.Content.displayName

const SelectItem = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Item
    ref={ref}
    className={cn(
      'relative flex w-full cursor-pointer select-none items-center rounded-sm py-1.5 px-2 text-sm outline-none',
      'data-[highlighted]:bg-[rgba(212,162,78,0.12)] data-[highlighted]:text-[var(--color-text-accent)]',
      'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
      className,
    )}
    style={{ color: 'var(--color-text-primary)' }}
    {...props}
  >
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
  </SelectPrimitive.Item>
))
SelectItem.displayName = SelectPrimitive.Item.displayName

const SelectLabel = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Label>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Label>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.Label
    ref={ref}
    className={cn('px-2 py-1.5 text-xs font-semibold', className)}
    style={{ color: 'var(--color-text-secondary)' }}
    {...props}
  />
))
SelectLabel.displayName = SelectPrimitive.Label.displayName

const SelectSeparator = React.forwardRef<
  React.ComponentRef<typeof SelectPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.Separator
    ref={ref}
    className={cn('-mx-1 my-1 h-px', className)}
    style={{ background: 'var(--color-border)' }}
    {...props}
  />
))
SelectSeparator.displayName = SelectPrimitive.Separator.displayName

export {
  Select, SelectGroup, SelectValue,
  SelectTrigger, SelectContent, SelectItem,
  SelectLabel, SelectSeparator,
}
