/**
 * 悬浮窗的拖动与缩放（指针事件版）。
 *
 * 从 HumanKpPanel 抽出来：那六十行与 KP 的业务毫无关系，纯粹是「一个能拖能拉的窗」，
 * 留在那个文件里只是占地方。抽出来之后别处要开悬浮窗也能直接用。
 *
 * 用 setPointerCapture 而不是在 window 上挂 move/up：捕获之后指针出了窗体、甚至出了
 * 浏览器可视区，事件仍然回到这个元素——否则拖快一点就「脱手」，窗停在半路。
 *
 * 缩放从右下角拉，但**左上角要跟着动**：窗体的定位原点在右侧（见调用方的 right 定位），
 * 不补这一下，拉宽时窗会朝左边长出去，看起来像在往反方向跑。
 */
import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

interface Size { width: number; height: number }

export function useDragResize(initial: Size, min: Size = { width: 360, height: 320 }) {
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const [size, setSize] = useState(initial)
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null)
  const resizeRef = useRef<
    { startX: number; startY: number; width: number; height: number; originX: number } | null
  >(null)

  const drag = {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
      // 标题栏上的按钮与输入框照常工作，别让拖拽把点击吃掉
      if ((event.target as HTMLElement).closest('button, input')) return
      event.currentTarget.setPointerCapture(event.pointerId)
      dragRef.current = {
        startX: event.clientX, startY: event.clientY,
        originX: position.x, originY: position.y,
      }
    },
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
      const d = dragRef.current
      if (!d) return
      setPosition({
        x: d.originX + event.clientX - d.startX,
        y: d.originY + event.clientY - d.startY,
      })
    },
    onPointerUp: () => { dragRef.current = null },
  }

  const resize = {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
      event.stopPropagation()          // 否则同时触发标题栏的拖动
      event.currentTarget.setPointerCapture(event.pointerId)
      resizeRef.current = {
        startX: event.clientX, startY: event.clientY,
        width: size.width, height: size.height, originX: position.x,
      }
    },
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
      const r = resizeRef.current
      if (!r) return
      const maxWidth = typeof window === 'undefined' ? 960 : Math.max(min.width, window.innerWidth - 32)
      const maxHeight = typeof window === 'undefined' ? 900 : Math.max(min.height, window.innerHeight - 96)
      const width = Math.min(maxWidth, Math.max(min.width, r.width + event.clientX - r.startX))
      const height = Math.min(maxHeight, Math.max(min.height, r.height + event.clientY - r.startY))
      setSize({ width, height })
      setPosition((current) => ({ ...current, x: r.originX + width - r.width }))
    },
    onPointerUp: () => { resizeRef.current = null },
  }

  /**
   * 复位到初始位置与尺寸。换存档、重开窗时用——窗停在上一局拖到的角落里很莫名。
   *
   * 初始值存 ref 再配 useCallback：调用方几乎都是内联传 `{ width: …, height: … }`，
   * 每次渲染都是新对象；直接依赖它的话 reset 永远不稳定，放进 effect 依赖数组就成了
   * 每帧复位一次。
   */
  const initialRef = useRef(initial)
  const reset = useCallback(() => {
    setPosition({ x: 0, y: 0 })
    setSize(initialRef.current)
  }, [])

  return { position, size, drag, resize, reset }
}
