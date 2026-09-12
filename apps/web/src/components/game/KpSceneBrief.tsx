/**
 * 场景速查：本场线索。
 *
 * 曾有两块——线索 + 「玩家还不知道的地点（解锁）」。解锁那一块拆掉了：解锁地点在写作台
 * 就有入口（解锁地点动作，带场景候选列表），两处发的是同一个动作，等于两套口径。
 * 速查留「本场线索」：KP 在叙事段落里反复要看的只有这一样——这屋里还有什么没被找到。
 */
import { GiMagnifyingGlass } from 'react-icons/gi'

interface Place {
  id: string
  name: string
  current: boolean
  known?: boolean
  clues?: { id: string; name: string; status: string }[]
}

export function KpSceneBrief({ places }: { places: Place[] }) {
  const here = places.find((p) => p.current)
  const clues = here?.clues || []

  if (!clues.length) return null

  return (
    <div className="kp-brief">
      <div className="kp-brief-label">本场线索</div>
      {clues.map((c) => (
        <div key={c.id} className="kp-brief-row">
          <GiMagnifyingGlass
            size={11}
            style={{ color: c.status === 'found' ? 'var(--color-success)' : 'var(--color-text-secondary)' }}
          />
          <span className="flex-1 truncate">{c.name}</span>
          <span className="kp-brief-tag">{c.status === 'found' ? '已得' : '未得'}</span>
        </div>
      ))}
    </div>
  )
}
