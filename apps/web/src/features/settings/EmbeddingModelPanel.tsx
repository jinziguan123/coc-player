import { useCallback, useEffect, useState } from 'react'
import { GiCancel, GiCheckMark, GiCloudDownload, GiCycle, GiStarMedal, GiTrashCan } from 'react-icons/gi'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import type { components } from '@/api/generated'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

type View = components['schemas']['EmbeddingSettingsView']
type Model = components['schemas']['EmbedModelView']

const ENDPOINT = '/settings/embedding'
const POLL_MS = 1000
const BADGE = { display: 'inline-flex', alignItems: 'center', gap: '0.2rem' } as const

const SOURCE_TEXT: Record<string, string> = {
  modelscope: 'ModelScope（国内推荐）',
  huggingface: 'HuggingFace（可填镜像或代理地址）',
}

function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  return `${Math.max(1, Math.round(n / 1e6))} MB`
}

function Progress({ done, total, label }: { done: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return (
    <div style={{ marginTop: '0.4rem' }}>
      <div
        className="upload-progress-track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className="upload-progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

/**
 * 检索模型：下载更强的嵌入模型并热切换。
 * 切换后后台重建全部向量索引，期间每个索引仍用建它的模型检索，完成后一次性切换。
 */
export function EmbeddingModelPanel() {
  const [view, setView] = useState<View | null>(null)
  const [busy, setBusy] = useState(false)
  const [endpoint, setEndpoint] = useState('')

  const apply = useCallback((next: View) => {
    setView(next)
    setEndpoint(next.hf_endpoint)
  }, [])

  const refresh = useCallback(
    () => localApi.get<View>(ENDPOINT).then(setView),
    [],
  )

  useEffect(() => {
    localApi.get<View>(ENDPOINT).then(apply).catch((e: Error) => toast.error(`读取检索模型失败：${e.message}`))
  }, [apply])

  const active = !!view && (view.rebuild.running || view.models.some((m) => m.download?.state === 'downloading'))
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => { refresh().catch(() => {}) }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [active, refresh])

  const run = async (request: Promise<View>) => {
    setBusy(true)
    try {
      setView(await request)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!view) return null

  const activeModel = view.models.find((m) => m.active)
  const targetModel = view.models.find((m) => m.target)
  const { rebuild } = view

  const saveSource = (source: string, hfEndpoint = endpoint) =>
    run(localApi.put<View>(`${ENDPOINT}/source`, { source, hf_endpoint: hfEndpoint }))

  const switchTo = (model: Model) =>
    run(localApi.post<View>(`${ENDPOINT}/switch`, { model_id: model.id }))

  return (
    <div className="card">
      <h3 className="card-title">检索模型</h3>
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}>
        决定规则书、模组原文与往事回忆的检索准确度。切换模型会在后台重建全部索引，期间检索照常可用
        （还没重建的索引继续用原来的模型查），全部完成后自动切换并释放旧模型的内存。
      </p>

      {(rebuild.running || rebuild.error || (view.stale > 0 && !targetModel)) && (
        <div className="agent-param" style={{ borderTop: 'none', paddingTop: 0 }}>
          <div className="agent-param__text" style={{ flex: 1 }}>
            {rebuild.running ? (
              <>
                <span className="text-sm font-semibold">
                  正在重建索引{targetModel ? `：切换到 ${targetModel.label}` : ''}
                </span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {rebuild.current || '准备中'} · {rebuild.done.toLocaleString()} / {rebuild.total.toLocaleString()} 条
                </span>
                <Progress done={rebuild.done} total={rebuild.total} label="索引重建进度" />
              </>
            ) : rebuild.error ? (
              <>
                <span className="text-sm font-semibold">索引重建失败</span>
                <span className="text-xs" style={{ color: 'var(--color-danger, #c0392b)' }}>{rebuild.error}</span>
              </>
            ) : (
              <>
                <span className="text-sm font-semibold">有 {view.stale.toLocaleString()} 条旧索引待重建</span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  它们不是用当前模型建的，重建前不参与检索。
                </span>
              </>
            )}
          </div>
          {!rebuild.running && (
            <div className="agent-param__control">
              <button
                type="button"
                className="btn-secondary agent-param__reset"
                disabled={busy}
                onClick={() => run(localApi.post<View>(`${ENDPOINT}/rebuild`))}
              >
                <GiCycle aria-hidden /> {rebuild.error ? '重试' : '重建'}
              </button>
            </div>
          )}
        </div>
      )}

      <div className="agent-params">
        {view.models.map((model) => {
          const dl = model.download
          const downloading = dl?.state === 'downloading'
          return (
            <div key={model.id} className="agent-param">
              <div className="agent-param__text" style={{ flex: 1 }}>
                <span className="text-sm font-semibold" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                  {model.label}
                  {model.recommended && <span className="badge" style={BADGE}><GiStarMedal aria-hidden /> 推荐</span>}
                  {model.active && <span className="badge" style={BADGE}><GiCheckMark aria-hidden /> 使用中</span>}
                  {model.target && <span className="badge" style={BADGE}><GiCycle aria-hidden /> 切换中</span>}
                </span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  {model.description}
                </span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  下载 {formatBytes(model.size_bytes)} · {model.dim} 维 · 建索引{model.index_speed} · 内存{model.memory}
                </span>
                {downloading && dl && (
                  <>
                    <Progress done={dl.done_bytes} total={dl.total_bytes} label={`${model.label} 下载进度`} />
                    <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                      已下载 {formatBytes(dl.done_bytes)} / {formatBytes(dl.total_bytes)}
                    </span>
                  </>
                )}
                {dl?.state === 'failed' && (
                  <span className="text-xs" style={{ color: 'var(--color-danger, #c0392b)' }}>
                    下载失败：{dl.error}
                  </span>
                )}
              </div>
              <div className="agent-param__control">
                {!model.installed && !downloading && (
                  <button
                    type="button"
                    className="btn-secondary agent-param__reset"
                    disabled={busy}
                    onClick={() => run(localApi.post<View>(`${ENDPOINT}/models/${model.id}/download`))}
                  >
                    <GiCloudDownload aria-hidden /> {dl?.state === 'failed' ? '重试下载' : '下载'}
                  </button>
                )}
                {downloading && (
                  <button
                    type="button"
                    className="btn-secondary agent-param__reset"
                    disabled={busy}
                    onClick={() => run(localApi.post<View>(`${ENDPOINT}/models/${model.id}/cancel`))}
                  >
                    <GiCancel aria-hidden /> 取消
                  </button>
                )}
                {model.target && activeModel && (
                  <button
                    type="button"
                    className="btn-secondary agent-param__reset"
                    disabled={busy}
                    onClick={() => switchTo(activeModel)}
                  >
                    <GiCancel aria-hidden /> 取消切换
                  </button>
                )}
                {model.installed && !model.active && !model.target && (
                  <>
                    <ConfirmDialog
                      title={`切换到 ${model.label}`}
                      description={`将在后台用新模型重建全部索引（建索引${model.index_speed}，规则书较多时可能需要十几分钟，期间占用较多 CPU 与约${model.memory}内存）。重建期间检索照常可用，完成后自动切换。`}
                      confirmLabel="开始切换"
                      onConfirm={() => switchTo(model)}
                    >
                      {(open) => (
                        <button type="button" className="btn-secondary agent-param__reset" disabled={busy} onClick={open}>
                          <GiCycle aria-hidden /> 切换
                        </button>
                      )}
                    </ConfirmDialog>
                    <ConfirmDialog
                      title={`删除 ${model.label}`}
                      description={`删除已下载的模型文件（${formatBytes(model.size_bytes)}），以后要用需重新下载。`}
                      confirmLabel="删除"
                      onConfirm={() => run(localApi.delete<View>(`${ENDPOINT}/models/${model.id}`))}
                    >
                      {(open) => (
                        <button
                          type="button"
                          className="btn-secondary agent-param__reset"
                          disabled={busy}
                          onClick={open}
                          aria-label={`删除 ${model.label}`}
                        >
                          <GiTrashCan aria-hidden /> 删除
                        </button>
                      )}
                    </ConfirmDialog>
                  </>
                )}
              </div>
            </div>
          )
        })}

        <div className="agent-param">
          <div className="agent-param__text">
            <span className="text-sm font-semibold">下载源</span>
            <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              国内直连 HuggingFace 常失败，默认用 ModelScope。选 HuggingFace 时可填镜像或代理地址，留空则用环境变量 HF_ENDPOINT 或官方地址。
            </span>
          </div>
          <div className="agent-param__control" style={{ flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Select value={view.source} onValueChange={(v) => saveSource(v)} disabled={busy}>
              <SelectTrigger className="w-60" aria-label="下载源">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SOURCE_TEXT).map(([key, text]) => (
                  <SelectItem key={key} value={key}>{text}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {view.source === 'huggingface' && (
              <input
                className="input"
                style={{ width: '16rem' }}
                aria-label="HuggingFace 地址"
                placeholder="https://huggingface.co"
                value={endpoint}
                disabled={busy}
                onChange={(e) => setEndpoint(e.target.value)}
                onBlur={() => { if (endpoint.trim() !== view.hf_endpoint) saveSource('huggingface', endpoint) }}
                onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
