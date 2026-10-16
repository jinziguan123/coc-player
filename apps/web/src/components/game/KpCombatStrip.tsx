import { useState } from 'react'
import { toast } from 'sonner'
import { Send } from 'lucide-react'
import { GiCrossedSwords, GiSkullCrossedBones } from 'react-icons/gi'
import { api } from '@/api/client'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import type { CombatLogEntry } from './combat/types'

/**
 * 真人 KP 值班台·战术模式的底条：战斗里写作台只剩这一行。
 *
 * 打起来之后 KP 的眼睛在棋盘上，要的只有三样：刚结算了什么、补一句描写、必要时收场。
 * 切场景、手书、解锁地点这类叙事动作这时用不上，摊在下面只是把战场往上挤——
 * 它们收进「更多动作」（展开回完整写作台），打完自动回来。
 */
export function KpCombatStrip({ sessionId, log, onExpand }: {
  sessionId: string
  log: CombatLogEntry[]
  onExpand: () => void
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const recent = log.slice(-3).reverse()

  const post = async (action: 'narration' | 'end_combat', payload: Record<string, unknown>, ok: string) => {
    if (busy) return false
    setBusy(true)
    try {
      await api.post(`/sessions/${sessionId}/kp/action`, { action, payload })
      toast.success(ok)
      return true
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'KP 动作执行失败')
      return false
    } finally {
      setBusy(false)
    }
  }
  const send = async () => {
    const content = text.trim()
    if (!content) return
    if (await post('narration', { content }, '描写已发布')) setText('')
  }

  return (
    <div className="kp-combat-strip">
      <div className="kp-combat-strip-log" aria-label="最近结算">
        <span className="kp-combat-strip-label"><GiCrossedSwords aria-hidden /> 最近结算</span>
        {recent.length
          ? recent.map((e) => <div key={e.id} className="kp-combat-strip-row" title={e.content}>{e.content}</div>)
          : <div className="kp-combat-strip-row kp-combat-strip-row--idle">还没有结算</div>}
      </div>
      <div className="kp-combat-strip-input">
        <input
          className="input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); void send() } }}
          placeholder="补一句战斗描写，回车发布；（圆括号）内为场外"
          aria-label="战斗描写"
        />
        <button type="button" className="btn-primary inline-flex items-center gap-1" disabled={busy || !text.trim()} onClick={() => void send()}>
          <Send size={13} /> 发布
        </button>
        <button type="button" className="btn-secondary" onClick={onExpand} title="展开完整写作台：检定、NPC 台词、手书、切场景……">
          更多动作
        </button>
        <ConfirmDialog
          title="结束战斗"
          description="敌人逃了、谈和了，或者这一架不值得再打下去？结束后回到叙事。"
          confirmLabel="结束战斗"
          onConfirm={() => void post('end_combat', {}, '战斗已结束')}
        >
          {(open) => (
            <button type="button" className="btn-secondary kp-combat-strip-end inline-flex items-center gap-1" onClick={open} disabled={busy}>
              <GiSkullCrossedBones aria-hidden /> 结束战斗
            </button>
          )}
        </ConfirmDialog>
      </div>
    </div>
  )
}
