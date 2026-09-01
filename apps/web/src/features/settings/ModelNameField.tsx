/**
 * 模型名称字段 ＋「获取可用模型」。
 *
 * 模型名此前只能手打，而差一个横杠就是 404——报错还要等到真开团、KP 该说话的时候才
 * 冒出来。两种协议都有标准的 `GET …/models`，地址和密钥都已经填在旁边了，问一下就是。
 *
 * 仍然是个能打字的输入框而不是纯下拉：中转站不实现清单接口是常态，模型也可能刚上线还
 * 没进清单，手填这条路一步都不能堵。
 *
 * 下拉是自己做的，不用 `<datalist>`：那玩意儿要点右侧的小箭头、或者先敲几个字才展开，
 * 可发现性等于没有（实测清空输入框也不弹），而桌面版跑在 WKWebView 里支持还更不可靠。
 * 自己做还能顺带把「按已输入的内容过滤」做了——中转站动辄报上几百个模型。
 */
import { useEffect, useId, useRef, useState } from 'react'
import { ChevronDown, RefreshCw } from 'lucide-react'
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
  const [open, setOpen] = useState(false)
  // 只有用户在框里**动过手**才按内容过滤。不看框里有没有值：刚拉完清单时它八成已经
  // 填着当前配置的模型名，拿它去筛，一屏候选会只剩它自己——等于白拉。
  const [filtering, setFiltering] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  const keyword = value.trim().toLowerCase()
  const shown = filtering && keyword
    ? models.filter((m) => m.toLowerCase().includes(keyword))
    : models

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

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
        setOpen(false)
        return
      }
      setModels(res.models)
      // 拉完就摊开、且先不过滤。否则界面上只多了一行「找到 N 个」，人还得自己去猜从
      // 哪儿挑。
      setFiltering(false)
      setOpen(true)
      toast.success(res.message)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '获取失败')
    } finally {
      setLoading(false)
    }
  }

  const pick = (name: string) => {
    onChange(name)
    setFiltering(false)     // 选完再点开，仍该看到全部候选
    setOpen(false)
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <div className="flex items-center justify-between mb-1">
        <label
          className="block text-sm font-semibold"
          style={{ fontSize: '0.85rem' }}
          htmlFor={listId}
        >
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

      <div style={{ position: 'relative' }}>
        <input
          id={listId}
          type="text"
          className="input w-full"
          style={models.length > 0 ? { paddingRight: '1.9rem' } : undefined}
          placeholder={placeholder}
          value={value}
          role={models.length > 0 ? 'combobox' : undefined}
          aria-expanded={models.length > 0 ? open : undefined}
          aria-controls={models.length > 0 && open ? `${listId}-list` : undefined}
          aria-autocomplete={models.length > 0 ? 'list' : undefined}
          onChange={(e) => {
            onChange(e.target.value)
            setFiltering(true)
            if (models.length > 0) setOpen(true)
          }}
          onFocus={() => { if (models.length > 0) setOpen(true) }}
        />
        {models.length > 0 && (
          <button
            type="button"
            className="combo-toggle"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? '收起模型列表' : '展开模型列表'}
            tabIndex={-1}
          >
            <ChevronDown size={14} aria-hidden="true" />
          </button>
        )}

        {open && shown.length > 0 && (
          <div id={`${listId}-list`} role="listbox" className="combo-list">
            {shown.map((name) => (
              <button
                key={name}
                type="button"
                role="option"
                aria-selected={name === value}
                data-active={name === value}
                className="combo-option"
                // onMouseDown 而不是 onClick：input 失焦会先触发外部点击关闭，
                // 等到 click 时这个按钮已经不在了。
                onMouseDown={(e) => { e.preventDefault(); pick(name) }}
              >
                {name}
              </button>
            ))}
          </div>
        )}
      </div>

      {models.length > 0 && (
        <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
          {shown.length === models.length
            ? `上游报了 ${models.length} 个模型，点输入框可以挑；照旧手填也行。`
            : `${models.length} 个里有 ${shown.length} 个含「${value.trim()}」。`}
        </p>
      )}
    </div>
  )
}
