import * as React from 'react'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { AnimatePresence, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { popoverVariants } from '@/lib/motion'
import { useControllableOpen } from '@/lib/useControllableOpen'

const TooltipProvider = TooltipPrimitive.Provider
const TooltipTrigger = TooltipPrimitive.Trigger

/** 退场动画：与 dialog.tsx 同一做法，Root 接出开合状态，Content 用 forceMount + AnimatePresence。 */
const TooltipOpenContext = React.createContext(false)

function Tooltip({ open, defaultOpen, onOpenChange, ...props }: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  const [value, setValue] = useControllableOpen({ open, defaultOpen, onOpenChange })
  return (
    <TooltipOpenContext.Provider value={value}>
      <TooltipPrimitive.Root open={value} onOpenChange={setValue} {...props} />
    </TooltipOpenContext.Provider>
  )
}

const TooltipContent = React.forwardRef<
  React.ComponentRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, children, ...props }, ref) => {
  const open = React.useContext(TooltipOpenContext)
  return (
    <AnimatePresence>
      {open && (
        <TooltipPrimitive.Portal forceMount>
          <TooltipPrimitive.Content ref={ref} forceMount asChild sideOffset={sideOffset} {...props}>
            <m.div
              className={cn('tooltip-content z-50 overflow-hidden rounded px-3 py-1.5 text-xs shadow-md', className)}
              variants={popoverVariants}
              initial="initial"
              animate="enter"
              exit="exit"
              style={{
                background: 'var(--color-text-primary)',
                color: 'var(--color-bg-primary)',
                fontFamily: 'var(--font-ui)',
                transformOrigin: 'var(--radix-tooltip-content-transform-origin)',
              }}
            >
              {children}
            </m.div>
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      )}
    </AnimatePresence>
  )
})
TooltipContent.displayName = TooltipPrimitive.Content.displayName

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
