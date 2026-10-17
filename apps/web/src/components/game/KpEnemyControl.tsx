/**
 * 真人 KP 的「我来操控敌人」：打开后，敌方回合像真人回合一样停下，等 KP 下令。
 *
 * 默认关着——敌人照旧由启发式自动出手（最低 HP 的调查员挨打、快死了的谨慎者会逃）。
 * 想让某个敌人做出剧情需要的选择（盯着某人打、掉头就跑、按兵不动）时打开它；
 * 不想逐个下令的回合点「交给自动」即可。只接管敌方：AI 队友是调查员，照旧自己行动。
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { GiCrossedSwords, GiRun, GiShield, GiSandsOfTime, GiAutoRepair } from 'react-icons/gi'
import { api } from '@/api/client'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import type { CombatState } from './combat/types'

const DOWN = new Set(['dead', 'dying', 'unconscious', 'fled'])

export function KpEnemyControl({ sessionId, combat }: { sessionId: string; combat: CombatState }) {
  const [busy, setBusy] = useState(false)
  const [target, setTarget] = useState('')
  const [weapon, setWeapon] = useState('')
  const actor = combat.awaiting_kp ? combat.order.find((p) => p.id === combat.awaiting_kp) : undefined
  const foes = combat.order.filter((p) => p.side !== 'enemy' && !DOWN.has(p.status))
  const chosen = foes.some((p) => p.id === target) ? target : foes[0]?.id || ''

  const post = async (action: string, payload: Record<string, unknown>, ok: string) => {
    if (busy) return
    setBusy(true)
    try {
      await api.post(`/sessions/${sessionId}/kp/action`, { action, payload })
      toast.success(ok)
      setWeapon('')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'KP 动作执行失败')
    } finally {
      setBusy(false)
    }
  }
  const order = (type: string, extra: Record<string, unknown> = {}) =>
    void post('combat_npc_act', { type, ...extra }, `${actor?.name || '敌人'} 的回合已结算`)

  return (
    <div className="kp-enemy-control flex flex-shrink-0 flex-wrap items-center gap-2 border-t px-3 py-1.5 text-xs"
      style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}>
      <Switch
        checked={!!combat.kp_controls_enemies}
        onChange={(on) => void post('combat_control', { on }, on ? '敌方回合改由你下令' : '敌方回合交回自动')}
        disabled={busy}
        label="我来操控敌人"
        onText="我来操控敌人"
        offText="敌人自动行动"
      />
      {actor && <>
        <strong style={{ color: 'var(--color-text-primary)' }}>轮到 {actor.name}：</strong>
        <Select value={chosen || undefined} onValueChange={setTarget}>
          <SelectTrigger className="w-auto min-w-28 text-xs" aria-label="攻击目标"><SelectValue placeholder="攻击目标" /></SelectTrigger>
          <SelectContent>
            {foes.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <input className="input w-28 text-xs" value={weapon} onChange={(e) => setWeapon(e.target.value)}
          placeholder={actor.weapon || '武器（默认）'} aria-label="武器" />
        <button type="button" className="btn-primary inline-flex items-center gap-1 !px-2 !py-0.5" disabled={busy || !chosen}
          onClick={() => order('attack', { target_id: chosen, weapon })}>
          <GiCrossedSwords size={12} aria-hidden /> 攻击
        </button>
        <button type="button" className="btn-secondary inline-flex items-center gap-1 !px-2 !py-0.5" disabled={busy} onClick={() => order('dodge')}>
          <GiShield size={12} aria-hidden /> 防御
        </button>
        <button type="button" className="btn-secondary inline-flex items-center gap-1 !px-2 !py-0.5" disabled={busy} onClick={() => order('flee')}>
          <GiRun size={12} aria-hidden /> 逃跑
        </button>
        <button type="button" className="btn-secondary inline-flex items-center gap-1 !px-2 !py-0.5" disabled={busy} onClick={() => order('wait')}>
          <GiSandsOfTime size={12} aria-hidden /> 按兵不动
        </button>
        <button type="button" className="btn-secondary inline-flex items-center gap-1 !px-2 !py-0.5" disabled={busy} onClick={() => order('auto')}
          title="这一回合照启发式自动行动">
          <GiAutoRepair size={12} aria-hidden /> 交给自动
        </button>
      </>}
    </div>
  )
}
