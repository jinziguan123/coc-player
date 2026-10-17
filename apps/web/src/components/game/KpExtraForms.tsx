/**
 * 写作台里补齐 AI KP 能力的几张表单：结算 HP、线索记账、封路 / 建议前往、NPC 代演。
 *
 * 从 KpActionForm 分出来，是因为它们各自要一点自己的数据（HP 要拉战斗里的参战方），
 * 而 KpActionForm 只管「按动作换形态」。
 */
import { useEffect, useState } from 'react'
import { GiDramaMasks, GiHeartPlus, GiSpellBook } from 'react-icons/gi'
import { api } from '@/api/client'
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { KpInput } from './KpInput'

interface CatalogItem { id: string; name: string }
type SetField = (key: string, value: string) => void

const muted = { color: 'var(--color-text-secondary)' }

function PlainSelect({ label, value, items, onChange, className = 'min-w-40 flex-1' }: {
  label: string
  value: string
  items: CatalogItem[]
  onChange: (value: string) => void
  className?: string
}) {
  return (
    <Select value={value || undefined} onValueChange={onChange}>
      <SelectTrigger className={`${className} text-xs`} aria-label={label}>
        <SelectValue placeholder={`选择${label}`} />
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => <SelectItem key={item.id} value={item.id}>{item.name || item.id}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}

interface CombatOrder { id: string; name: string; side: string }

/**
 * 结算 HP：目标从下拉里点，不再手打名字（手打时空着会扣到主角身上、写半个名字会扣到别人身上）。
 * 战斗中额外列出敌方 / NPC 参战方——战斗外系统不追踪 NPC 的 HP。
 * 变化量收整数或骰式（1d6+1），由后端经规则内核掷。
 */
export function HpChangeForm({ sessionId, fields, characters, setField }: {
  sessionId: string
  fields: Record<string, string>
  characters: CatalogItem[]
  setField: SetField
}) {
  const [combatants, setCombatants] = useState<CombatOrder[]>([])
  useEffect(() => {
    let alive = true
    void api.get<{ active: boolean; order?: CombatOrder[] }>(`/sessions/${sessionId}/combat`)
      .then((r) => { if (alive) setCombatants(r.active ? r.order || [] : []) })
      .catch(() => { if (alive) setCombatants([]) })
    return () => { alive = false }
  }, [sessionId])
  const charIds = new Set(characters.map((c) => c.id))
  const npcs = combatants.filter((c) => !charIds.has(c.id))
  const mode = fields.hp_mode || 'damage'
  const amount = fields.amount || ''
  const update = (nextMode: string, nextAmount: string) => {
    setField('hp_mode', nextMode)
    setField('amount', nextAmount)
    const body = nextAmount.trim().replace(/^[+-]/, '')
    setField('delta', body ? `${nextMode === 'heal' ? '+' : '-'}${body}` : '')
  }
  return (
    <>
      <GiHeartPlus size={16} />
      <Select value={fields.target || undefined} onValueChange={(v) => setField('target', v)}>
        <SelectTrigger className="min-w-40 flex-1 text-xs" aria-label="结算对象">
          <SelectValue placeholder="选择结算对象" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectLabel>调查员</SelectLabel>
            {characters.map((c) => <SelectItem key={c.id} value={`character:${c.id}`}>{c.name}</SelectItem>)}
          </SelectGroup>
          {!!npcs.length && (
            <SelectGroup>
              <SelectLabel>战斗中的 NPC</SelectLabel>
              {npcs.map((c) => <SelectItem key={c.id} value={`combatant:${c.id}`}>{c.name}</SelectItem>)}
            </SelectGroup>
          )}
        </SelectContent>
      </Select>
      <Select value={mode} onValueChange={(v) => update(v, amount)}>
        <SelectTrigger className="w-auto min-w-20 text-xs" aria-label="伤害或治疗"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="damage">伤害</SelectItem>
          <SelectItem value="heal">治疗</SelectItem>
        </SelectContent>
      </Select>
      <input
        value={amount}
        onChange={(e) => update(mode, e.target.value)}
        placeholder="点数或骰式，如 3、1d6+1"
        className="input w-40 text-xs"
        aria-label="变化量"
      />
      <KpInput name="reason" placeholder="原因（可选）" fields={fields} onChange={setField} />
    </>
  )
}

/** 线索记账：KP 自己最清楚刚给出了什么——记进台账，结局判定 / 卡关检测 / 复盘都靠它。 */
export function MarkSeenForm({ fields, clues, setField }: {
  fields: Record<string, string>
  clues: CatalogItem[]
  setField: SetField
}) {
  return (
    <>
      <GiSpellBook size={16} />
      {clues.length
        ? <PlainSelect label="线索" value={fields.clue || ''} items={clues} onChange={(v) => setField('clue', v)} />
        : <span className="text-xs" style={muted}>本模组没有登记线索</span>}
      <PlainSelect
        label="给出程度"
        className="w-auto min-w-24"
        value={fields.level || 'direct'}
        items={[{ id: 'direct', name: '明确给出' }, { id: 'hint', name: '只提了一嘴' }]}
        onChange={(v) => setField('level', v)}
      />
      <KpInput name="event" placeholder="或：场景机制点原文（可选）" fields={fields} onChange={setField} />
    </>
  )
}

/** 封路 / 解封 / 建议前往：三者都只挑一个地点，外加一句缘由。 */
export function PlaceReasonForm({ fields, scenes, setField, reasonHint }: {
  fields: Record<string, string>
  scenes: CatalogItem[]
  setField: SetField
  /** 不给就不出缘由框（解封不需要说为什么） */
  reasonHint?: string
}) {
  return (
    <>
      <PlainSelect label="地点" value={fields.scene_id || ''} items={scenes} onChange={(v) => setField('scene_id', v)} />
      {reasonHint && <KpInput name="reason" placeholder={reasonHint} fields={fields} onChange={setField} />}
    </>
  )
}

/** NPC 代演：交给该 NPC 的人格代理生成一句台词并直接展示——会调用一次 AI 模型。 */
export function NpcActForm({ fields, npcs, setField }: {
  fields: Record<string, string>
  npcs: CatalogItem[]
  setField: SetField
}) {
  return (
    <>
      <GiDramaMasks size={16} />
      <PlainSelect label="NPC" value={fields.npc_id || ''} items={npcs} onChange={(v) => setField('npc_id', v)} />
      <KpInput name="trigger" placeholder="触发情境：他为什么此刻开口" fields={fields} onChange={setField} />
      <span className="w-full text-xs" style={muted}>会调用一次 AI 模型，台词直接发给全桌；想自己写请用「NPC 台词」。</span>
    </>
  )
}
