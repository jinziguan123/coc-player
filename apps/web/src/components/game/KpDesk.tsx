/**
 * KP 待办台：写作台下面那一块「我还欠着什么、我刚做了什么」。
 *
 * - 待投检定：玩家迟迟不投（掉线、走神）时 KP 可以代投或撤销；群检里撤掉最后一个没投的，
 *   整批收束，结果照常交回 KP。
 * - 玩家的检定申请：从前只有一条 toast，一晃就没了；现在留在这里，一键填进检定表单。
 * - 私有台账：每次动作的结果（含暗投点数）都留底，下一次动作不再把上一次的结果冲掉；
 *   自己发过的旁白 / NPC 台词可以改写或撤回。
 *
 * 数据只经 KP 鉴权的 /kp/desk 读出，不走房间广播——暗投结果在这里，不能进玩家可见路径。
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { ChevronDown, ChevronRight, Pencil, Undo2, X } from 'lucide-react'
import { GiCancel, GiRollingDices } from 'react-icons/gi'
import { api } from '@/api/client'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useKpPrefill } from '@/stores/kpPrefill'
import { ACTION_LABELS, type KpAction } from './kpActions'
import { useKpDesk, type KpDeskPendingCheck } from './useKpDesk'

interface LedgerEvent {
  id: string
  type: string
  content: string
  withdrawn?: boolean
}

interface LedgerEntry {
  id: string
  at: string
  action: string
  result: string
  events: LedgerEvent[]
}

const DIFFICULTY: Record<string, string> = { normal: '普通', hard: '困难', extreme: '极难' }
const EXTRA_LABELS: Record<string, string> = {
  cancel_check: '撤销检定', dismiss_request: '忽略申请', edit_event: '改写', withdraw_event: '撤回',
  combat_control: '敌方操控', combat_npc_act: '敌方行动', set_battle_map: '战场底图', set_token: '战棋',
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action as KpAction] || EXTRA_LABELS[action] || action
}

function clock(at: string): string {
  const d = new Date(at)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function KpDesk({ sessionId, tick, lastResult, onClearResult, onAdopt }: {
  sessionId: string
  /** 面板侧的刷新信号（KP 每发一个动作 +1） */
  tick: number
  lastResult: string
  onClearResult: () => void
  /** 把参数填进写作台（KP 过目后再发） */
  onAdopt: (action: KpAction, fields: Record<string, string | undefined>) => void
}) {
  const roomTick = useKpPrefill((s) => s.deskTick)
  const [ledgerOpen, setLedgerOpen] = useState(false)
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null)
  const [busy, setBusy] = useState('')
  const [reload, setReload] = useState(0)
  // 与观局台账页共用一个取数口；tick 由面板动作、房间事件和本地重试共同推进。
  const desk = useKpDesk(sessionId, tick + roomTick + reload)


  const act = async (key: string, action: string, payload: Record<string, unknown>, ok: string) => {
    if (busy) return false
    setBusy(key)
    try {
      await api.post(`/sessions/${sessionId}/kp/action`, { action, payload })
      toast.success(ok)
      setReload((x) => x + 1)
      return true
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'KP 动作执行失败')
      return false
    } finally {
      setBusy('')
    }
  }

  const proxyRoll = async (check: KpDeskPendingCheck) => {
    if (busy) return
    setBusy(`roll:${check.id}`)
    try {
      await api.post(`/sessions/${sessionId}/kp/checks/${check.id}/roll`)
      toast.success(`已替 ${check.actor_name} 投骰`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '代投失败')
    } finally {
      setBusy('')
      setReload((x) => x + 1)
    }
  }

  const pending = desk?.pending_checks || []
  const requests = desk?.check_requests || []
  const ledger = (desk?.ledger || []) as LedgerEntry[]
  if (!lastResult && !pending.length && !requests.length && !ledger.length) return null

  return (
    <div className="kp-desk mt-2 space-y-2 border-t pt-2 text-xs" style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}>
      {lastResult && (
        <div className="flex items-start gap-2" aria-live="polite">
          <strong className="shrink-0" style={{ color: 'var(--color-text-primary)' }}>最近结果</strong>
          <span className="min-w-0 flex-1 whitespace-pre-wrap">{lastResult}</span>
          <button type="button" onClick={onClearResult} className="btn-secondary !p-1" title="清除最近结果（台账里仍留底）"><X size={12} /></button>
        </div>
      )}

      {(pending.length > 0 || requests.length > 0) && (
        <div className="space-y-1">
          <strong style={{ color: 'var(--color-text-primary)' }}>待办</strong>
          {pending.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-2">
              <GiRollingDices size={13} aria-hidden />
              <span className="min-w-0 flex-1">
                等 {c.actor_name} 投{c.kind === 'san_check' ? '理智' : c.skill}
                {c.kind !== 'san_check' && `（${DIFFICULTY[c.difficulty] || c.difficulty}）`}
                {c.group && ' · 群检'}
              </span>
              <button type="button" className="btn-secondary !px-2 !py-0.5" disabled={!!busy} onClick={() => void proxyRoll(c)} title="玩家不在时替他投；结果照常公开，不弹幸运消费">
                代投
              </button>
              <button type="button" className="btn-secondary inline-flex items-center gap-1 !px-2 !py-0.5" disabled={!!busy}
                onClick={() => void act(`cancel:${c.id}`, 'cancel_check', { check_id: c.id }, '已撤销这一掷')}>
                <GiCancel size={12} aria-hidden /> 撤销
              </button>
            </div>
          ))}
          {requests.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1">{r.actor_name} 申请「{r.skill}」{r.intent && `：${r.intent}`}</span>
              <button type="button" className="btn-secondary !px-2 !py-0.5" title="填进检定表单，可改难度后再发"
                onClick={() => onAdopt('dice_check', {
                  skill: r.skill, char: r.actor_id ? `character:${r.actor_id}` : undefined,
                  reason: r.intent, request_id: r.id,
                })}>
                填入检定
              </button>
              <button type="button" className="btn-secondary !px-2 !py-0.5" disabled={!!busy}
                onClick={() => void act(`dismiss:${r.id}`, 'dismiss_request', { request_id: r.id }, '已忽略')}>
                忽略
              </button>
            </div>
          ))}
        </div>
      )}

      {ledger.length > 0 && (
        <div>
          <button type="button" onClick={() => setLedgerOpen((v) => !v)} className="inline-flex items-center gap-1" aria-expanded={ledgerOpen}>
            {ledgerOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <strong style={{ color: 'var(--color-text-primary)' }}>私有台账</strong>
            <span>· {ledger.length} 条，仅你可见（含暗投结果）</span>
          </button>
          {ledgerOpen && (
            <ol className="mt-1 max-h-60 space-y-1.5 overflow-y-auto pr-1">
              {ledger.map((entry) => (
                <li key={entry.id} className="rounded px-1.5 py-1" style={{ background: 'var(--color-bg-tertiary)' }}>
                  <div className="flex gap-2">
                    <span className="shrink-0 tabular-nums">{clock(entry.at)}</span>
                    <span className="shrink-0" style={{ color: 'var(--color-text-primary)' }}>{actionLabel(entry.action)}</span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap">{entry.result}</span>
                  </div>
                  {entry.events.map((ev) => ev.withdrawn ? (
                    <div key={ev.id} className="mt-1 italic">（已撤回）</div>
                  ) : editing?.id === ev.id ? (
                    <div key={ev.id} className="mt-1 flex flex-col gap-1">
                      <textarea className="input min-h-16 text-xs" value={editing.text} onChange={(e) => setEditing({ id: ev.id, text: e.target.value })} aria-label="改写内容" />
                      <div className="flex gap-1">
                        <button type="button" className="btn-primary !px-2 !py-0.5" disabled={!!busy || !editing.text.trim()}
                          onClick={() => void act(`edit:${ev.id}`, 'edit_event', { event_id: ev.id, content: editing.text }, '已改写')
                            .then((ok) => { if (ok) setEditing(null) })}>
                          保存
                        </button>
                        <button type="button" className="btn-secondary !px-2 !py-0.5" onClick={() => setEditing(null)}>取消</button>
                      </div>
                    </div>
                  ) : (
                    <div key={ev.id} className="mt-1 flex items-start gap-1">
                      <span className="min-w-0 flex-1 truncate" title={ev.content}>{ev.content}</span>
                      <button type="button" className="btn-secondary !p-1" title="改写（全桌看到的这条会跟着变）" onClick={() => setEditing({ id: ev.id, text: ev.content })}>
                        <Pencil size={11} />
                      </button>
                      <ConfirmDialog
                        title="撤回这条消息"
                        description="全桌的消息流里会删掉它。已经据此行动的玩家不会被回滚——必要时补一句旁白交代。"
                        confirmLabel="撤回"
                        onConfirm={() => void act(`withdraw:${ev.id}`, 'withdraw_event', { event_id: ev.id }, '已撤回')}
                      >
                        {(open) => (
                          <button type="button" className="btn-secondary !p-1" title="撤回" onClick={open} disabled={!!busy}>
                            <Undo2 size={11} />
                          </button>
                        )}
                      </ConfirmDialog>
                    </div>
                  ))}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  )
}
