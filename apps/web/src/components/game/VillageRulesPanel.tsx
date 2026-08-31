// 村规：这一桌长期沿用的规则改动，按规则系统配置（规则书页面）。
//
// 层级：模组默认 > 村规 > 本局覆盖 > 规则原文。留在默认档不是「没设」，是「照规则书来」。
// 后端只落与原文的差异项，所以日后规则默认值调整时，没显式改过的项会跟着走。
//
// 它不在房间里设——村规是桌上的规矩，不该每开一局重填一遍。改动对所有用这套规则的
// 房间即时生效（含进行中的局）。
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

export interface VillageRules {
  critical_max: number
  fumble_rule: string
  dice_pool_cap: number
  luck_spend: boolean
  luck_spend_max: number
  luck_spend_in_combat: boolean
  luck_spend_blocks_improvement: boolean
  major_wound_divisor: number
  insanity_rule: string
  insanity_flat_threshold: number
  improvement: boolean
}

const FUMBLE_LABELS: Record<string, string> = {
  raw: '照规则书：100 必大失败，技能 < 50 时 96 起即大失败',
  hundred_only: '只有掷出 100 才算大失败',
  ninety_six_plus: '96 起一律大失败，不看技能高低',
}

const INSANITY_LABELS: Record<string, string> = {
  fifth_of_san: '单次损失 ≥ 当前 SAN 的五分之一',
  flat: '单次损失达到固定点数',
}

/** 一条规则：标题与控件同行（控件一律靠右，右边缘天然对齐），说明另起一行占满宽度。
 *
 *  说明不能跟标题挤在左半边——那样它只剩一半宽度，一句话要折三行，整列看着像被压扁的表格。
 *  ``indent`` 用于从属项（幸运消费展开的那几条），靠缩进表明它们依附于上一条。 */
function Row({ label, hint, indent, children }: {
  label: string
  hint?: string
  indent?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`py-2 ${indent ? 'pl-3' : ''}`} style={
      indent ? { borderLeft: '2px solid var(--color-border)' } : undefined
    }>
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm leading-tight">{label}</span>
        <span className="flex-shrink-0">{children}</span>
      </div>
      {hint && (
        <p className="mt-1 leading-snug" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)',
        }}>
          {hint}
        </p>
      )}
    </div>
  )
}

function NumberBox({ label, value, onChange, min, max }: {
  label: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
}) {
  return (
    <input
      type="number"
      aria-label={label}
      value={value}
      min={min}
      max={max}
      onChange={(e) => onChange(Number(e.target.value))}
      className="input w-16 text-sm text-right"
    />
  )
}

const NOTES_MAX = 800

export function VillageRulesPanel({ ruleSystem, twoColumn }: {
  ruleSystem: string
  /** 宽容器里把参数排成两列：单列十一行要滚半屏，两列一屏就看完了，
   *  而且判定类与伤害/状态类天然分开，比一长串更有条理。 */
  twoColumn?: boolean
}) {
  const [rules, setRules] = useState<VillageRules | null>(null)
  const [notes, setNotes] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await api.get<{
          options: Partial<VillageRules>
          effective: VillageRules
          table_notes: string
          enabled: boolean
        }>(`/rulebooks/village-rules/${ruleSystem}`)
        if (alive) {
          setRules(data.effective)
          setNotes(data.table_notes || '')
          setEnabled(data.enabled !== false)
        }
      } catch (e: unknown) {
        toast.error(e instanceof Error ? e.message : '读取村规失败')
      }
    })()
    return () => { alive = false }
  }, [ruleSystem])

  const patch = (next: Partial<VillageRules>) =>
    setRules((cur) => (cur ? { ...cur, ...next } : cur))

  const save = async () => {
    if (!rules) return
    setSaving(true)
    try {
      // 整份提交，后端负责白名单化、钳区间、只存与规则原文的差异
      await api.put(`/rulebooks/village-rules/${ruleSystem}`, {
        options: rules, table_notes: notes, enabled,
      })
      toast.success(enabled ? '村规已更新，下一次掷骰起生效' : '村规已停用，这一桌回到规则原文')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (!rules) {
    return (
      <div className="text-sm py-6 text-center" style={{ color: 'var(--color-text-secondary)' }}>
        读取中…
      </div>
    )
  }

  return (
    <div>
      <div className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
        这一桌长期沿用的规则改动，对所有用这套规则的房间生效（含进行中的局）；不改的项照规则书原文来。
      </div>

      {/* 总开关。关掉时下面配好的东西一条都不生效，但也**不会被清掉**——
          想先照规则原文跑一局试试，回头一开就全回来了。 */}
      <div className="flex items-center justify-between gap-3 rounded-md px-3 py-2 mb-3"
        style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}>
        <div className="min-w-0">
          <div className="text-sm" style={{ color: 'var(--color-text-primary)' }}>启用村规</div>
          <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
            {enabled
              ? '下面配好的改动正在生效。'
              : '已停用：这一桌完全照规则书原文跑，下面的配置留着不动，随时能开回来。'}
          </div>
        </div>
        <Switch label="启用村规" checked={enabled} onChange={setEnabled} />
      </div>

      {/* 停用时把配置区压暗：仍然可读可改（改完开回来就生效），但一眼看得出它此刻不算数 */}
      <div style={enabled ? undefined : { opacity: 0.55 }}>

      <div className={twoColumn ? 'grid gap-x-6 md:grid-cols-2' : ''}>
        {/* 左组：一次检定怎么判 */}
        <div className="divide-y self-start" style={{ borderColor: 'var(--color-border)' }}>
          <Row label="大成功阈值" hint="骰值 ≤ 此值算大成功（规则书是 1）">
            <NumberBox label="大成功阈值" value={rules.critical_max} min={1} max={20}
              onChange={(v) => patch({ critical_max: v })} />
          </Row>

          <Row label="大失败阈值" hint={FUMBLE_LABELS[rules.fumble_rule]}>
            <Select value={rules.fumble_rule} onValueChange={(v) => patch({ fumble_rule: v })}>
              <SelectTrigger className="w-[8.5rem] text-sm" aria-label="大失败阈值">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="raw">照规则书</SelectItem>
                <SelectItem value="hundred_only">只认 100</SelectItem>
                <SelectItem value="ninety_six_plus">96 起</SelectItem>
              </SelectContent>
            </Select>
          </Row>

          <Row label="奖惩骰上限" hint="单次检定最多叠几个奖励或惩罚骰">
            <NumberBox label="奖惩骰上限" value={rules.dice_pool_cap} min={0} max={3}
              onChange={(v) => patch({ dice_pool_cap: v })} />
          </Row>

          <Row
            label="幸运消费"
            hint="规则书的可选规则：检定失败后可花幸运点抵掉差值买成功。开启后失败时会问玩家买不买。"
          >
            <Switch label="幸运消费" checked={rules.luck_spend}
              onChange={(v) => patch({ luck_spend: v })} />
          </Row>

          {rules.luck_spend && (
            <>
              <Row indent label="单次上限" hint="一次检定最多花几点，0 = 不限">
                <NumberBox label="幸运单次上限" value={rules.luck_spend_max} min={0} max={999}
                  onChange={(v) => patch({ luck_spend_max: v })} />
              </Row>
              <Row indent label="战斗中可用" hint="常见做法是战斗中禁用，避免拿幸运硬扛伤害判定">
                <Switch label="战斗中可用" checked={rules.luck_spend_in_combat}
                  onChange={(v) => patch({ luck_spend_in_combat: v })} />
              </Row>
              <Row indent label="买来的成功不计成长" hint="照规则书：走运没教会你任何事">
                <Switch label="买来的成功不计成长" checked={rules.luck_spend_blocks_improvement}
                  onChange={(v) => patch({ luck_spend_blocks_improvement: v })} />
              </Row>
            </>
          )}
        </div>

        {/* 右组：挨打与掉 SAN 之后怎么算 */}
        <div className="divide-y self-start" style={{ borderColor: 'var(--color-border)' }}>
          <Row label="重伤阈值" hint={`单击伤害 ≥ 最大 HP ÷ ${rules.major_wound_divisor} 判重伤（规则书是 2，即半血）`}>
            <NumberBox label="重伤阈值" value={rules.major_wound_divisor} min={1} max={10}
              onChange={(v) => patch({ major_wound_divisor: v })} />
          </Row>

          <Row label="临时疯狂口径" hint={INSANITY_LABELS[rules.insanity_rule]}>
            <Select value={rules.insanity_rule} onValueChange={(v) => patch({ insanity_rule: v })}>
              <SelectTrigger className="w-[8.5rem] text-sm" aria-label="临时疯狂口径">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fifth_of_san">SAN 的 1/5</SelectItem>
                <SelectItem value="flat">固定点数</SelectItem>
              </SelectContent>
            </Select>
          </Row>

          {rules.insanity_rule === 'flat' && (
            <Row indent label="固定点数" hint="规则书原文是 5">
              <NumberBox label="临时疯狂固定点数" value={rules.insanity_flat_threshold} min={1} max={99}
                onChange={(v) => patch({ insanity_flat_threshold: v })} />
            </Row>
          )}

          <Row label="技能成长" hint="关掉则模组结束时不做成长检定（短模组常这么跑）">
            <Switch label="技能成长" checked={rules.improvement}
              onChange={(v) => patch({ improvement: v })} />
          </Row>
        </div>
      </div>

      {/* 桌面约定：参数表达不了的规矩走自由文本。**必须把界限写在界面上**——它进的是
          KP 的提示词，只改叙述与裁定倾向，不改任何一次骰子结算。不说清楚，玩家会在这里
          写「大失败只认 100」然后以为生效了。 */}
      <div className="mt-4 pt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
        <label htmlFor="table-notes" className="text-sm">桌面约定</label>
        <p className="mt-1 mb-1.5 leading-snug" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)',
        }}>
          参数管不到的规矩，写给守秘人看（「重调查轻战斗」「NPC 死亡不可逆」「别用现代词汇」）。
          <b>只影响怎么演，不改骰子结算</b>——要改判定请用上面的参数。
        </p>
        <textarea
          id="table-notes"
          value={notes}
          maxLength={NOTES_MAX}
          rows={4}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="例：本局重调查轻战斗，能谈就别打；NPC 死了就是死了，不要复活。"
          className="input w-full text-sm resize-y"
        />
        <div className="text-right" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)',
        }}>
          {notes.length}/{NOTES_MAX}
        </div>
      </div>
      </div>

      <div className="flex justify-end mt-3">
        <button onClick={() => void save()} disabled={saving} className="btn-primary btn-sm">
          {saving ? '保存中…' : '保存村规'}
        </button>
      </div>
    </div>
  )
}
