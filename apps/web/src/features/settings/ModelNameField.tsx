/**
 * 模型名称字段 ＋「获取可用模型」。
 *
 * 模型名此前只能手打，而差一个横杠就是 404——报错还要等到真开团、KP 该说话的时候才
 * 冒出来。两种协议都有标准的 `GET …/models`，地址和密钥都已经填在旁边了，问一下就是。
 *
 * 仍然是个 input 而不是下拉：中转站不实现清单接口是常态，模型也可能刚上线还没进清单。
 * 拉到的东西挂在 `<datalist>` 上当建议，手填这条路一步都没堵。
 */
import { useId, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { localApi } from '@/api/client'

interface ModelsResult {
  success: boolean
  models: string[]
  message: string
}

export function ModelNameField({
  protocol, baseUrl, value, placeholder, onChange, resolveKey,
}: {
  protocol: string
  baseUrl: string
  value: string
  placeholder: string
  onChange: (next: string) => void
  /** 取真实密钥：编辑态下表单里是掩码，要向后端单独要。 */
  resolveKey: () => Promise<string>
}) {
  const listId = useId()
  const [models, setModels] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  const fetchModels = async () => {
    setLoading(true)
    try {
      const res = await localApi.post<ModelsResult>('/settings/ai/models', {
        protocol,
        base_url: baseUrl.trim(),
        api_key: await resolveKey(),
      })
      if (!res.success) {
        // 后端已经把「这个服务没有清单接口」之类的情况说成人话了，照转即可
        toast.error(res.message)
        setModels([])
        return
      }
      setModels(res.models)
      toast.success(res.message)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '获取失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="block text-sm font-semibold" style={{ fontSize: '0.85rem' }}>
          模型名称
        </label>
        <button
          type="button"
          className="btn-secondary btn-xs flex items-center gap-1"
          onClick={() => void fetchModels()}
          disabled={loading}
          title="按上面填的地址和密钥，问上游有哪些模型可用"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          {loading ? '获取中…' : '获取可用模型'}
        </button>
      </div>
      <input
        type="text"
        className="input w-full"
        placeholder={placeholder}
        value={value}
        list={models.length > 0 ? listId : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {models.length > 0 && (
        <>
          <datalist id={listId}>
            {models.map((name) => <option key={name} value={name} />)}
          </datalist>
          <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
            上游报了 {models.length} 个模型，清空输入框可以挑；照旧手填也行。
          </p>
        </>
      )}
    </div>
  )
}
