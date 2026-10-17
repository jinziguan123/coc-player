import { useEffect, useState } from 'react'

/** 只认视口宽度的布尔媒体查询；观局台用它决定是否让出三栏。 */
export function useMinWidth(px: number): boolean {
  const query = '(min-width: ' + px + 'px)'
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return matches
}
