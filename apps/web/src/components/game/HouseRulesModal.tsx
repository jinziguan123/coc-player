// 本局家规（房主专用）。
//
// 层级：本局 > 模组默认 > 规则原文（RAW）。留在默认档不是「没设」，是「照规则书来」。
// 后端只落与 RAW 的差异项，所以日后 RAW 默认值调整时，没显式改过的项会跟着走。
//
// 按房主授权（后端 require_session_manager 同样把关）：家规直接改变每个人的成败概率，
// 不该让任一玩家单方面改掉。玩家仍可读（GET 不限房主）——自己在什么规则下掷骰，有权知道。
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { GiScrollUnfurled } from 'react-icons/gi'
import { api } from '@/api/client'
import { Modal } from '@/components/ui/modal'

interface RuleOptions {
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

function Row({ label, hint, children }: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <div className="min-w-0">
        <div className="text-sm">{label}</div>
        {hint && (
          <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
            {hint}
          </div>
        )}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

function Toggle({ label, checked, onChange }: {
  /** 无障碍名：光一个裸 checkbox，读屏软件念不出它管的是哪条规则 */
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      className="h-4 w-4 cursor-pointer"
    />
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
      className="input w-20 text-sm"
    />
  )
}

export function HouseRulesModal({ sessionId, canEdit, onClose }: {
  sessionId: string
  canEdit: boolean
  onClose: () => void
}) {
  const [options, setOptions] = useState<RuleOptions | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await api.get<{ options: Partial<RuleOptions>; effective: RuleOptions }>(
          `/sessions/${sessionId}/rule-options`,
        )
        if (alive) setOptions(data.effective)
      } catch (e: unknown) {
        toast.error(e instanceof Error ? e.message : '读取家规失败')
      }
    })()
    return () => { alive = false }
  }, [sessionId])

  const patch = (next: Partial<RuleOptions>) =>
    setOptions((cur) => (cur ? { ...cur, ...next } : cur))

  const save = async () => {
    if (!options) return
    setSaving(true)
    try {
      // 整份提交，后端负责白名单化、钳区间、只存与 RAW 的差异
      await api.put(`/sessions/${sessionId}/rule-options`, { rule_options: options })
      toast.success('本局家规已更新，下一次掷骰起生效')
      onClose()
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} widthClass="max-w-lg">
      <div className="p-4">
        <div className="flex items-center gap-2 mb-1" style={{ color: 'var(--color-text-accent)' }}>
          <GiScrollUnfurled />
          <span className="font-semibold">本局家规</span>
        </div>
        <div className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
          {canEdit
            ? '改动只作用于本局，随时可以调回；不改的项照规则书原文来。'
            : '由房主设定。列出本局实际生效的规则，供你了解自己在什么规则下掷骰。'}
        </div>

        {!options ? (
          <div className="text-sm py-6 text-center" style={{ color: 'var(--color-text-secondary)' }}>
            读取中…
          </div>
        ) : (
          <fieldset disabled={!canEdit} className="divide-y" style={{ borderColor: 'var(--color-border)' }}>
            <Row label="大成功阈值" hint={`骰值 ≤ 此值算大成功（规则书是 1）`}>
              <NumberBox label="大成功阈值" value={options.critical_max} min={1} max={20}
                onChange={(v) => patch({ critical_max: v })} />
            </Row>

            <Row label="大失败判定" hint={FUMBLE_LABELS[options.fumble_rule]}>
              <select
                aria-label="大失败判定"
                value={options.fumble_rule}
                onChange={(e) => patch({ fumble_rule: e.target.value })}
                className="input text-sm"
              >
                <option value="raw">照规则书</option>
                <option value="hundred_only">只认 100</option>
                <option value="ninety_six_plus">96 起</option>
              </select>
            </Row>

            <Row label="奖惩骰上限" hint="单次检定最多叠几个奖励或惩罚骰">
              <NumberBox label="奖惩骰上限" value={options.dice_pool_cap} min={0} max={3}
                onChange={(v) => patch({ dice_pool_cap: v })} />
            </Row>

            <Row
              label="幸运消费"
              hint="规则书的可选规则：检定失败后可花幸运点抵掉差值买成功。开启后失败时会问你买不买。"
            >
              <Toggle label="幸运消费" checked={options.luck_spend} onChange={(v) => patch({ luck_spend: v })} />
            </Row>

            {options.luck_spend && (
              <>
                <Row label="单次上限" hint="一次检定最多花几点，0 = 不限">
                  <NumberBox label="幸运单次上限" value={options.luck_spend_max} min={0} max={999}
                    onChange={(v) => patch({ luck_spend_max: v })} />
                </Row>
                <Row label="战斗中可用" hint="常见家规是战斗中禁用，避免拿幸运硬扛伤害判定">
                  <Toggle label="战斗中可用" checked={options.luck_spend_in_combat}
                    onChange={(v) => patch({ luck_spend_in_combat: v })} />
                </Row>
                <Row label="买来的成功不计成长" hint="照规则书：走运没教会你任何事">
                  <Toggle label="买来的成功不计成长" checked={options.luck_spend_blocks_improvement}
                    onChange={(v) => patch({ luck_spend_blocks_improvement: v })} />
                </Row>
              </>
            )}

            <Row label="重伤阈值" hint={`单击伤害 ≥ 最大 HP ÷ ${options.major_wound_divisor} 判重伤（规则书是 2，即半血）`}>
              <NumberBox label="重伤阈值" value={options.major_wound_divisor} min={1} max={10}
                onChange={(v) => patch({ major_wound_divisor: v })} />
            </Row>

            <Row label="临时疯狂口径" hint={INSANITY_LABELS[options.insanity_rule]}>
              <select
                aria-label="临时疯狂口径"
                value={options.insanity_rule}
                onChange={(e) => patch({ insanity_rule: e.target.value })}
                className="input text-sm"
              >
                <option value="fifth_of_san">按 SAN 的五分之一</option>
                <option value="flat">按固定点数</option>
              </select>
            </Row>

            {options.insanity_rule === 'flat' && (
              <Row label="固定点数" hint="规则书原文是 5">
                <NumberBox label="临时疯狂固定点数" value={options.insanity_flat_threshold} min={1} max={99}
                  onChange={(v) => patch({ insanity_flat_threshold: v })} />
              </Row>
            )}

            <Row label="技能成长" hint="关掉则模组结束时不做成长检定（短模组常这么跑）">
              <Toggle label="技能成长" checked={options.improvement} onChange={(v) => patch({ improvement: v })} />
            </Row>
          </fieldset>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="btn-secondary text-sm">
            {canEdit ? '取消' : '关闭'}
          </button>
          {canEdit && (
            <button onClick={() => void save()} disabled={saving || !options}
              className="btn-primary text-sm">
              {saving ? '保存中…' : '保存'}
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}
