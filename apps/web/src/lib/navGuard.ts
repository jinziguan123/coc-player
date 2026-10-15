// 离开页面前的拦截：有未保存改动的页面登记一个守卫，侧边栏跳转前先问它。
//
// 本应用用的是 <BrowserRouter>（非数据路由），react-router 的 useBlocker 用不了；
// 而离开一级页面的入口几乎只有侧边栏，于是在那里问一句就够了。
// 刷新/关窗由页面自己挂 beforeunload（见 useLeaveGuard）。
import { useEffect, useRef } from 'react'

/** 返回 true 放行；返回 false 表示守卫自己接手（如弹出「还没保存」对话框）。 */
type Guard = (to: string) => boolean

let current: Guard | null = null

export function canLeave(to: string): boolean {
  return current ? current(to) : true
}

/** 页面在 dirty 时登记守卫；onBlocked 拿到目标地址，由页面决定弹框、保存或放弃后再跳。 */
export function useLeaveGuard(dirty: boolean, onBlocked: (to: string) => void): void {
  const cb = useRef(onBlocked)
  useEffect(() => { cb.current = onBlocked })

  useEffect(() => {
    if (!dirty) return
    const guard: Guard = (to) => { cb.current(to); return false }
    current = guard
    const beforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', beforeUnload)
    return () => {
      if (current === guard) current = null
      window.removeEventListener('beforeunload', beforeUnload)
    }
  }, [dirty])
}
