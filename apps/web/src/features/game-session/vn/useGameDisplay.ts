import { useCallback, useEffect, useState } from 'react'
import { DISPLAY_EVENT, getGameDisplayMode, setGameDisplayMode, type GameDisplayMode } from '@/lib/theme'

/**
 * 局内显示方式（聊天流 / 对话框）。顶栏按钮与设置页改的是同一份偏好，
 * 同页内靠自定义事件即时同步，跨标签页靠 storage 事件。
 */
export function useGameDisplay(): [GameDisplayMode, (mode: GameDisplayMode) => void] {
  const [mode, setMode] = useState<GameDisplayMode>(() => getGameDisplayMode())
  useEffect(() => {
    const sync = () => setMode(getGameDisplayMode())
    window.addEventListener(DISPLAY_EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(DISPLAY_EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])
  const set = useCallback((next: GameDisplayMode) => setGameDisplayMode(next), [])
  return [mode, set]
}
