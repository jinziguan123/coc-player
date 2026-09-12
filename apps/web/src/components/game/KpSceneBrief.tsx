/**
 * 场景速查：本场线索 + 玩家还不知道的地点。
 *
 * 放这两样而不是别的，是因为它们是 KP 在叙事段落里唯一会反复回头看的东西：
 * 「这屋里还有什么没被找到」和「下一步能把哪儿放出去」。此前前者要开大地图弹窗的调查板、
 * 后者根本没有入口——只能绕着写一句旁白去碰关键词匹配（见后端 unlock_place 的说明）。
 *
 * 解锁直接发出而不是填进表单：它只有一个参数，而参数就是你刚点的那一行，没有可斟酌的余地。
 * 卡上的检定/伤害那些不一样——那些要填技能、填数值，所以走预填。
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { GiMagnifyingGlass, GiPadlock } from 'react-icons/gi'

import { api } from '@/api/client'

interface Place {
  id: string
  name: string
  current: boolean
  known?: boolean
  clues?: { id: string; name: string; status: string }[]
}

export function KpSceneBrief({ sessionId, places, onDone }: {
  sessionId: string
  places: Place[]
  /** 解锁成功后通知外层刷新地点表（否则那一行要等下一次整轮刷新才消失） */
  onDone: () => void
}) {
  const [busy, setBusy] = useState('')
  const here = places.find((p) => p.current)
  // known === false 才是「KP 看得见、玩家还看不见」。玩家侧拿到的 known 恒为 true，
  // 所以这一段在玩家那边天然为空——这个组件本就只在 KP 席渲染。
  const locked = places.filter((p) => p.known === false && !p.current)
  const clues = here?.clues || []

  const unlock = async (p: Place) => {
    if (busy) return
    setBusy(p.id)
    try {
      await api.post(`/sessions/${sessionId}/kp/action`, {
        action: 'unlock_place', payload: { scene_id: p.id },
      })
      toast.success(`${p.name} 已对玩家公开`)
      onDone()
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '解锁失败')
    } finally {
      setBusy('')
    }
  }

  if (!clues.length && !locked.length) return null

  return (
    <div className="kp-brief">
      {clues.length > 0 && (
        <>
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
        </>
      )}
      {locked.length > 0 && (
        <>
          <div className="kp-brief-label mt-2">玩家还不知道的地点</div>
          {locked.map((p) => (
            <button
              key={p.id}
              type="button"
              className="kp-brief-row kp-brief-row--act"
              disabled={!!busy}
              onClick={() => void unlock(p)}
              title="把这个地点对玩家公开（立即发出）"
            >
              <GiPadlock size={11} />
              <span className="flex-1 truncate text-left">{p.name}</span>
              <span className="kp-brief-tag kp-brief-tag--act">
                {busy === p.id ? '…' : '解锁'}
              </span>
            </button>
          ))}
        </>
      )}
    </div>
  )
}
