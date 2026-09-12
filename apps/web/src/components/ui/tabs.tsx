import * as React from 'react'
import * as TabsPrimitive from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'

const Tabs = TabsPrimitive.Root

const TabsList = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn('flex border-b', className)}
    style={{ borderColor: 'var(--color-border)' }}
    {...props}
  />
))
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      // 字距+衬线标题字让页签更像「分区」而非普通文字；hover 先给一层轻反馈
      'flex-1 py-2 text-center transition-colors cursor-pointer border-b-2 tracking-wider',
      'text-[length:var(--text-xs)] font-[family-name:var(--font-title)]',
      'border-transparent text-[var(--color-text-secondary)]',
      'hover:text-[var(--color-text-primary)] hover:bg-[var(--surface-2)]',
      'data-[state=active]:border-[var(--color-accent)] data-[state=active]:text-[var(--color-text-accent)] data-[state=active]:font-semibold',
      'data-[state=active]:bg-[color-mix(in_srgb,var(--color-accent)_7%,transparent)]',
      className,
    )}
    {...props}
  />
))
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn('flex-1 overflow-auto p-3', className)}
    {...props}
  />
))
TabsContent.displayName = TabsPrimitive.Content.displayName

/**
 * 高度不漂移的页签内容：所有页**同时挂载**、叠在同一个网格格子里，非当前页只是不可见。
 * 容器高度因此取最高那页，切页时不会缩放；放在悬浮窗里尤其要紧——Radix 默认只渲染当前页，
 * 两页内容一长一短，切一下整个窗体就上下跳，居中重排后连点击都会落到遮罩上。
 * 用法：<TabsPanes> 包住若干 <TabsPane value=…>，替代 <TabsContent>。
 */
const TabsPanes = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('grid', className)} {...props} />
  ),
)
TabsPanes.displayName = 'TabsPanes'

const TabsPane = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    forceMount
    className={cn(
      // 同一格叠放；非当前页 visibility:hidden（也就退出了可访问性树与焦点序）
      'col-start-1 row-start-1 overflow-auto p-3',
      'data-[state=inactive]:invisible data-[state=inactive]:pointer-events-none',
      className,
    )}
    {...props}
  />
))
TabsPane.displayName = 'TabsPane'

export { Tabs, TabsList, TabsTrigger, TabsContent, TabsPanes, TabsPane }
