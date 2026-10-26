import { useCallback, useEffect, useState } from 'react'
import { GiBackwardTime } from 'react-icons/gi'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import type { components } from '@/api/generated'
import { Switch } from '@/components/ui/switch'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

type View = components['schemas']['AgentSettingsView']
type Param = components['schemas']['AgentParam']

const ENDPOINT = '/settings/agent'

/** 档位的文案（数值由后端给出，保证与实际生效值一致）。 */
const PRESET_TEXT: Record<string, { label: string; desc: string }> = {
  economy: { label: '省钱', desc: '大窗口模型也只用较小的上下文，token 花费更低；KP 记得的逐字原文更少，更依赖摘要。' },
  balanced: { label: '均衡', desc: '默认。预算随模型窗口放大并封顶，兼顾记忆厚度与花费。' },
  full: { label: '完整', desc: '保留更多逐字原文与去过的场景，记忆更厚、花费更高；小窗口模型更容易顶到窗口上限。' },
}

const CHOICE_TEXT: Record<string, string> = { release: '照常放出', withhold: '扣下不展示' }

function formatNumber(value: unknown): string {
  return typeof value === 'number' ? String(value) : ''
}

/**
 * 高级设置：本机生效的 agent 运行预算与上下文档位。
 * 只存改过的项；每项可单独恢复默认，下一次生成起生效。
 */
export function AgentAdvancedPanel() {
  const [view, setView] = useState<View | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const apply = useCallback((next: View) => {
    setView(next)
    setDrafts(Object.fromEntries(
      next.params.filter((p) => p.kind === 'int' || p.kind === 'float')
        .map((p) => [p.key, formatNumber(p.value)]),
    ))
  }, [])

  useEffect(() => {
    localApi.get<View>(ENDPOINT).then(apply).catch((e: Error) => toast.error(`读取高级设置失败：${e.message}`))
  }, [apply])

  const run = async (request: Promise<View>) => {
    setSaving(true)
    try {
      apply(await request)
    } catch (e) {
      toast.error((e as Error).message)
      if (view) apply(view)   // 还原输入框里未保存成功的草稿
    } finally {
      setSaving(false)
    }
  }

  const save = (values: Record<string, unknown>, contextPreset?: string) =>
    run(localApi.put<View>(ENDPOINT, { values, context_preset: contextPreset ?? null }))

  const reset = (key?: string) =>
    run(localApi.delete<View>(key ? `${ENDPOINT}?key=${encodeURIComponent(key)}` : ENDPOINT))

  const commitNumber = (param: Param) => {
    const raw = (drafts[param.key] ?? '').trim()
    if (raw === formatNumber(param.value)) return
    const value = Number(raw)
    if (raw === '' || Number.isNaN(value)) {
      toast.error(`${param.label}必须是数字`)
      setDrafts((d) => ({ ...d, [param.key]: formatNumber(param.value) }))
      return
    }
    save({ [param.key]: value })
  }

  if (!view) return null

  return (
    <div>
      <h2 className="page-title">高级</h2>
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '1rem' }}>
        只影响这台电脑上的 AI 运行方式，下一次生成起生效。没把握时保持默认即可。
      </p>

      <div className="card">
        <h3 className="card-title">上下文档位</h3>
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}>
          决定 KP 每回合能读到多少过往剧情：事件预算上限、最近原文保底、兜底摘要、保留的去过场景数。
        </p>
        <div className="display-modes" role="radiogroup" aria-label="上下文档位">
          {view.context_presets.map((preset) => {
            const text = PRESET_TEXT[preset.key] ?? { label: preset.key, desc: '' }
            const on = view.context_preset === preset.key
            return (
              <button
                key={preset.key}
                type="button"
                role="radio"
                aria-checked={on}
                disabled={saving}
                className={`display-mode ${on ? 'is-on' : ''}`}
                onClick={() => !on && save({}, preset.key)}
              >
                <b>{text.label}</b>
                <span className="agent-preset-figures">
                  预算上限 {preset.budget_ceiling.toLocaleString()} · 保底 {preset.min_recent_events} 条 ·
                  摘要 {preset.max_summary_tokens.toLocaleString()} · 场景 {preset.visited_scene_window} 个
                </span>
                <span>{text.desc}</span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">运行预算</h3>
        <div className="agent-params">
          {view.params.map((param) => (
            <div key={param.key} className="agent-param">
              <div className="agent-param__text">
                <span className="text-sm font-semibold">
                  {param.label}
                  {param.overridden && <span className="badge" style={{ marginLeft: '0.5rem' }}>已修改</span>}
                </span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {param.description}
                  {(param.kind === 'int' || param.kind === 'float') && param.minimum != null && param.maximum != null && (
                    <>（{param.minimum.toLocaleString()}–{param.maximum.toLocaleString()} {param.unit}，默认 {typeof param.default === 'number' ? param.default.toLocaleString() : String(param.default)} {param.unit}）</>
                  )}
                </span>
              </div>
              <div className="agent-param__control">
                {param.kind === 'bool' && (
                  <Switch
                    label={param.label}
                    checked={!!param.value}
                    disabled={saving}
                    onChange={(next) => save({ [param.key]: next })}
                  />
                )}
                {param.kind === 'choice' && (
                  <Select value={String(param.value)} onValueChange={(v) => save({ [param.key]: v })} disabled={saving}>
                    <SelectTrigger className="w-40" aria-label={param.label}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(param.choices ?? []).map((choice) => (
                        <SelectItem key={choice} value={choice}>{CHOICE_TEXT[choice] ?? choice}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
                {(param.kind === 'int' || param.kind === 'float') && (
                  <span className="agent-param__number">
                    <input
                      className="input"
                      inputMode="decimal"
                      aria-label={param.label}
                      value={drafts[param.key] ?? ''}
                      disabled={saving}
                      onChange={(e) => setDrafts((d) => ({ ...d, [param.key]: e.target.value }))}
                      onBlur={() => commitNumber(param)}
                      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                    />
                    {param.unit && <span className="text-xs">{param.unit}</span>}
                  </span>
                )}
                {param.overridden && (
                  <button
                    type="button"
                    className="btn-secondary agent-param__reset"
                    disabled={saving}
                    onClick={() => reset(param.key)}
                    aria-label={`恢复「${param.label}」的默认值`}
                  >
                    <GiBackwardTime aria-hidden /> 恢复默认
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: '1rem', display: 'flex', justifyContent: 'flex-end' }}>
          <ConfirmDialog
            title="全部恢复默认"
            description="清除所有改过的运行预算，并把上下文档位设回「均衡」。"
            confirmLabel="恢复默认"
            onConfirm={() => reset()}
          >
            {(open) => (
              <button type="button" className="btn-secondary" disabled={saving} onClick={open}>
                全部恢复默认
              </button>
            )}
          </ConfirmDialog>
        </div>
      </div>
    </div>
  )
}
