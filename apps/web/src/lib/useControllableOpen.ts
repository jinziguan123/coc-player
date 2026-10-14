import { useCallback, useState } from 'react'

/**
 * Radix 根组件（Dialog / Select / Tooltip）的开合状态，受控 / 非受控两用。
 *
 * 为什么要自己接一层：要做退场动画，Content 得用 forceMount 交给 Motion 的
 * AnimatePresence 决定何时卸载，而那就得知道「现在开着没有」——Radix 不对外暴露
 * 这个状态。于是在包装的 Root 里把它接出来，经 context 传给同文件的 Content。
 */
export function useControllableOpen({ open, defaultOpen = false, onOpenChange }: {
  open?: boolean
  defaultOpen?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  const [inner, setInner] = useState(defaultOpen)
  const controlled = open !== undefined
  const value = controlled ? open : inner
  const setValue = useCallback((next: boolean) => {
    if (!controlled) setInner(next)
    onOpenChange?.(next)
  }, [controlled, onOpenChange])
  return [value, setValue] as const
}
