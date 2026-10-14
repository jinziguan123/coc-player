import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { modalBackdropVariants, modalPanelVariants } from '@/lib/motion'
import { useControllableOpen } from '@/lib/useControllableOpen'

/**
 * 退场动画：Radix 关掉就卸载 Content，来不及播退场。这里在 Root 上把开合状态接出来，
 * DialogContent 据此用 forceMount + AnimatePresence 自己决定何时卸载（与 Modal 同一套
 * modalBackdropVariants / modalPanelVariants）。Esc、点遮罩、焦点归还仍由 Radix 负责。
 */
const DialogOpenContext = React.createContext(false)

function Dialog({ open, defaultOpen, onOpenChange, ...props }: React.ComponentProps<typeof DialogPrimitive.Root>) {
  const [value, setValue] = useControllableOpen({ open, defaultOpen, onOpenChange })
  return (
    <DialogOpenContext.Provider value={value}>
      <DialogPrimitive.Root open={value} onOpenChange={setValue} {...props} />
    </DialogOpenContext.Provider>
  )
}
const DialogTrigger = DialogPrimitive.Trigger
const DialogClose = DialogPrimitive.Close
const DialogPortal = DialogPrimitive.Portal

const DialogOverlay = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      'fixed inset-0 z-50 bg-black/60',
      className,
    )}
    {...props}
  />
))
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName

const DialogContent = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, style, ...props }, ref) => {
  const open = React.useContext(DialogOpenContext)
  return (
    <AnimatePresence>
      {open && (
        <DialogPortal forceMount>
          <DialogPrimitive.Overlay forceMount asChild onClick={(e) => e.stopPropagation()}>
            <m.div
              className="fixed inset-0 z-50 bg-black/60"
              variants={modalBackdropVariants}
              initial="initial"
              animate="enter"
              exit="exit"
            />
          </DialogPrimitive.Overlay>
          <DialogPrimitive.Content ref={ref} forceMount asChild onClick={(e) => e.stopPropagation()} {...props}>
            <m.div
              // 居中用 Tailwind 的 translate 属性，Motion 动的是 transform，两者叠加互不覆盖
              className={cn(
                'fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
                // dialog-panel：与 .modal-panel 同一窗体质感（琥珀顶缘高光 + 深层投影）
                'rounded-md border p-6 dialog-panel',
                className,
              )}
              variants={modalPanelVariants}
              initial="initial"
              animate="enter"
              exit="exit"
              // 默认在前、调用方 style 在后合并——避免调用方传 style（如 maxHeight）把背景色覆盖没了
              style={{
                background: 'var(--color-bg-secondary)',
                borderColor: 'var(--color-border-strong)',
                color: 'var(--color-text-primary)',
                fontFamily: 'var(--font-ui)',
                ...style,
              }}
            >
              {children}
            </m.div>
          </DialogPrimitive.Content>
        </DialogPortal>
      )}
    </AnimatePresence>
  )
})
DialogContent.displayName = DialogPrimitive.Content.displayName

function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-1.5 mb-4', className)} {...props} />
}

function DialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex justify-end gap-2 mt-4', className)} {...props} />
}

const DialogTitle = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-lg font-semibold leading-none tracking-tight', className)}
    style={{ fontFamily: 'var(--font-title)', color: 'var(--color-text-accent)' }}
    {...props}
  />
))
DialogTitle.displayName = DialogPrimitive.Title.displayName

const DialogDescription = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-sm', className)}
    style={{ color: 'var(--color-text-secondary)' }}
    {...props}
  />
))
DialogDescription.displayName = DialogPrimitive.Description.displayName

export {
  Dialog, DialogTrigger, DialogClose, DialogPortal,
  DialogOverlay, DialogContent, DialogHeader, DialogFooter,
  DialogTitle, DialogDescription,
}
