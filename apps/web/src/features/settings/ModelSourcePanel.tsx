import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import type { components } from '@/api/generated'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

type View = components['schemas']['ModelSourceView']

const ENDPOINT = '/settings/model-source'

const SOURCE_TEXT: Record<string, string> = {
  modelscope: 'ModelScope（国内推荐）',
  huggingface: 'HuggingFace（可填镜像或代理地址）',
}

/** 模型下载源：检索模型与精排模型共用。 */
export function ModelSourcePanel() {
  const [view, setView] = useState<View | null>(null)
  const [endpoint, setEndpoint] = useState('')
  const [busy, setBusy] = useState(false)

  const apply = (next: View) => {
    setView(next)
    setEndpoint(next.hf_endpoint)
  }

  useEffect(() => {
    localApi.get<View>(ENDPOINT).then(apply).catch((e: Error) => toast.error(`读取下载源失败：${e.message}`))
  }, [])

  const save = async (source: string, hfEndpoint: string) => {
    setBusy(true)
    try {
      apply(await localApi.put<View>(ENDPOINT, { source, hf_endpoint: hfEndpoint }))
    } catch (e) {
      toast.error((e as Error).message)
      if (view) apply(view)
    } finally {
      setBusy(false)
    }
  }

  if (!view) return null

  return (
    <div className="card">
      <h3 className="card-title">模型下载源</h3>
      <div className="agent-param" style={{ borderTop: 'none', paddingTop: 0 }}>
        <div className="agent-param__text">
          <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            检索模型与精排模型都从这里下载。国内直连 HuggingFace 常失败，默认用 ModelScope；
            选 HuggingFace 时可填镜像或代理地址，留空则用环境变量 HF_ENDPOINT 或官方地址。
          </span>
        </div>
        <div className="agent-param__control" style={{ flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Select value={view.source} onValueChange={(v) => void save(v, endpoint)} disabled={busy}>
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
              onBlur={() => { if (endpoint.trim() !== view.hf_endpoint) void save('huggingface', endpoint) }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
            />
          )}
        </div>
      </div>
    </div>
  )
}
