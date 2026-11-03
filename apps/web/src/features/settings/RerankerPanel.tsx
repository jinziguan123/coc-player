import { useCallback, useEffect, useState } from 'react'
import { GiCancel, GiCheckMark, GiCloudDownload, GiTrashCan } from 'react-icons/gi'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import type { components } from '@/api/generated'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Switch } from '@/components/ui/switch'
import { useUploadJobsStore } from '@/stores/uploadJobsStore'
import { ModelDownloadProgress } from './ModelDownloadProgress'
import { BADGE, formatBytes } from './modelFormat'

type View = components['schemas']['RerankerSettingsView']

const ENDPOINT = '/settings/reranker'
const POLL_MS = 1000

/**
 * 精排模型：KP 主动查阅模组原文时，把检索模型取回的候选按与问题的相关度重新排序。
 * 与检索模型分开管理（各自的开关与配置）；下载器与下载源共用。
 * 每轮自动注入的原文摘录不用它（每次要多等几秒）。
 */
export function RerankerPanel() {
  const [view, setView] = useState<View | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => localApi.get<View>(ENDPOINT).then(setView), [])

  useEffect(() => {
    refresh().catch((e: Error) => toast.error(`读取精排模型失败：${e.message}`))
  }, [refresh])

  const downloading = !!view?.models.some((m) => m.download?.state === 'downloading')
  useEffect(() => {
    if (!downloading) return
    const timer = window.setInterval(() => { refresh().catch(() => {}) }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [downloading, refresh])

  const run = async (request: Promise<View>, startsTask = false) => {
    setBusy(true)
    try {
      setView(await request)
      if (startsTask) void useUploadJobsStore.getState().expect()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!view) return null
  const installed = view.models.some((m) => m.installed)

  return (
    <div className="card">
      <h3 className="card-title">精排模型</h3>
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}>
        KP 主动查阅模组原文时，先用检索模型取回 20 个候选，再用精排模型按与问题的相关度重新排序，
        让「找到了但排在后面」的那段原文排到前面。每次查阅约多等 2～4 秒；每轮自动注入的原文摘录不受影响，规则书检索也不用它。
      </p>
      <div className="agent-params">
        <div className="agent-param">
          <div className="agent-param__text">
            <span className="text-sm font-semibold">KP 查阅模组原文时精排</span>
            <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {installed ? '开启后对 KP 的主动查阅与真人 KP 工作台的模组检索生效。' : '需先下载精排模型。'}
            </span>
          </div>
          <div className="agent-param__control">
            <Switch
              label="KP 查阅模组原文时精排"
              checked={view.enabled}
              disabled={busy || !installed}
              onChange={(next) => void run(localApi.put<View>(ENDPOINT, { enabled: next }))}
            />
          </div>
        </div>
        {view.models.map((model) => {
          const dl = model.download
          const isDownloading = dl?.state === 'downloading'
          return (
            <div key={model.id} className="agent-param">
              <div className="agent-param__text" style={{ flex: 1 }}>
                <span className="text-sm font-semibold" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                  {model.label}
                  {model.active && <span className="badge" style={BADGE}><GiCheckMark aria-hidden /> 使用中</span>}
                </span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>{model.description}</span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  下载 {formatBytes(model.size_bytes)} · {model.index_speed} · 内存{model.memory}
                </span>
                {isDownloading && dl && (
                  <ModelDownloadProgress done={dl.done_bytes} total={dl.total_bytes} label={`${model.label} 下载进度`} />
                )}
                {dl?.state === 'failed' && (
                  <span className="text-xs" style={{ color: 'var(--color-danger, #c0392b)' }}>下载失败：{dl.error}</span>
                )}
              </div>
              <div className="agent-param__control">
                {!model.installed && !isDownloading && (
                  <button
                    type="button"
                    className="btn-secondary agent-param__reset"
                    disabled={busy}
                    onClick={() => void run(localApi.post<View>(`${ENDPOINT}/models/${model.id}/download`), true)}
                    aria-label={`${dl?.state === 'failed' ? '重试下载' : '下载'} ${model.label}`}
                  >
                    <GiCloudDownload aria-hidden /> {dl?.state === 'failed' ? '重试下载' : '下载'}
                  </button>
                )}
                {isDownloading && (
                  <button
                    type="button"
                    className="btn-secondary agent-param__reset"
                    disabled={busy}
                    onClick={() => void run(localApi.post<View>(`${ENDPOINT}/models/${model.id}/cancel`))}
                  >
                    <GiCancel aria-hidden /> 取消
                  </button>
                )}
                {model.installed && (
                  <ConfirmDialog
                    title={`删除 ${model.label}`}
                    description={`删除已下载的精排模型（${formatBytes(model.size_bytes)}），精排随之关闭；以后要用需重新下载。`}
                    confirmLabel="删除"
                    onConfirm={() => run(localApi.delete<View>(`${ENDPOINT}/models/${model.id}`))}
                  >
                    {(open) => (
                      <button type="button" className="btn-secondary agent-param__reset" disabled={busy} onClick={open} aria-label={`删除 ${model.label}`}>
                        <GiTrashCan aria-hidden /> 删除
                      </button>
                    )}
                  </ConfirmDialog>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
