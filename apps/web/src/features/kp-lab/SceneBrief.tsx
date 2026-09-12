/**
 * 场景速查：当前场景的线索、以及还没对玩家公开的地点。
 *
 * 它**只在叙事模式出现**，战术模式下这块位置让给棋盘——这正是「主台按模式重组」的意思。
 * 不做成常驻第三栏是刻意的：常驻就意味着打起来的时候它还在那儿占着棋盘的地方。
 *
 * 放这两样而不是别的，是因为它们是 KP 在叙事段落里唯一会反复回头看的东西：
 * 「这屋里还有什么没被找到」和「下一步能把哪儿放出去」。
 */
import { GiPadlock, GiMagnifyingGlass } from 'react-icons/gi'

import type { LabPlace } from './useLabData'

const CLUE_TONE: Record<string, string> = {
  found: 'var(--color-success)',
  revealed: 'var(--color-success)',
}

export function SceneBrief({ places, onUnlock, onReveal }: {
  places: LabPlace[]
  onUnlock: (p: LabPlace) => void
  onReveal: (clue: string) => void
}) {
  const here = places.find((p) => p.current)
  // 「玩家还不知道」的地点：KP 视角拿得到全图，known=false 的就是还没放出去的。
  const locked = places.filter((p) => p.known === false && !p.current)
  const clues = here?.clues || []

  return (
    <aside className="kl-brief">
      <div className="kl-section-label mb-1.5">本场线索</div>
      {clues.length === 0 && (
        <p className="kl-brief-empty">这个场景没有登记在册的线索。</p>
      )}
      <div className="flex flex-col gap-1">
        {clues.map((c) => (
          <button key={c.id} className="kl-brief-row" onClick={() => onReveal(c.name)}>
            <GiMagnifyingGlass size={12} aria-hidden="true" style={{ color: CLUE_TONE[c.status] || 'var(--color-text-secondary)' }} />
            <span className="flex-1 truncate text-left">{c.name}</span>
            <span className="kl-brief-tag">{c.status === 'found' ? '已得' : '未得'}</span>
          </button>
        ))}
      </div>

      <div className="kl-section-label mb-1.5 mt-4">玩家还不知道的地点</div>
      {locked.length === 0 && (
        <p className="kl-brief-empty">该放的都放出去了。</p>
      )}
      <div className="flex flex-col gap-1">
        {locked.map((p) => (
          <button key={p.id} className="kl-brief-row" onClick={() => onUnlock(p)}>
            <GiPadlock size={12} aria-hidden="true" style={{ color: 'var(--color-text-secondary)' }} />
            <span className="flex-1 truncate text-left">{p.name}</span>
            <span className="kl-brief-tag kl-brief-tag--act">解锁</span>
          </button>
        ))}
      </div>
    </aside>
  )
}
