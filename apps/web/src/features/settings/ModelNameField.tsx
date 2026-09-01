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
 *
 * 浮层走 Radix Popover 的 Portal，不用 position:absolute。编辑配置那个表单是
 * `maxHeight + overflowY` 的滚动容器，绝对定位的浮层会被祖先的 overflow 裁掉——
 * 表现出来是下拉「被弹窗挡住」，而且只露得出字母序靠前的那几个（一堆 deepseek 排在
 * qwen 前面，于是看着像上游只给了 deepseek，一输入 qwen 又冒出来了）。
 */
import { useId, useRef, useState } from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
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
  const anchorRef = useRef<HTMLDivElement>(null)

  const keyword = value.trim().toLowerCase()
  const shown = filtering && keyword
    ? models.filter((m) => m.toLowerCase().includes(keyword))
    : models

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
    <div>
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

      <PopoverPrimitive.Root open={open} onOpenChange={setOpen} modal={false}>
        <PopoverPrimitive.Anchor asChild>
          <div ref={anchorRef} style={{ position: 'relative' }}>
            <input
              id={listId}
              type="text"
              className="input w-full"
              style={models.length > 0 ? { paddingRight: '1.9rem' } : undefined}
              placeholder={placeholder}
              value={value}
              role={models.length > 0 ? 'combobox' : undefined}
              aria-expanded={models.length > 0 ? open : undefined}
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
          </div>
        </PopoverPrimitive.Anchor>

        {shown.length > 0 && (
          <PopoverPrimitive.Portal>
            <PopoverPrimitive.Content
              align="start"
              side="bottom"
              sideOffset={4}
              collisionPadding={12}
              className="combo-list z-[110] w-[var(--radix-popover-trigger-width)]"
              // 别把焦点从输入框抢走——它是个能继续打字的 combobox，不是纯菜单
              onOpenAutoFocus={(e) => e.preventDefault()}
              onCloseAutoFocus={(e) => e.preventDefault()}
              // 点回输入框或那个展开钮不算「点到别处」，否则会先关再开、闪一下
              onInteractOutside={(e) => {
                if (anchorRef.current?.contains(e.target as Node)) e.preventDefault()
              }}
            >
              <div role="listbox" aria-label="可用模型">
                {shown.map((name) => (
                  <button
                    key={name}
                    type="button"
                    role="option"
                    aria-selected={name === value}
                    data-active={name === value}
                    className="combo-option"
                    onClick={() => pick(name)}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </PopoverPrimitive.Content>
          </PopoverPrimitive.Portal>
        )}
      </PopoverPrimitive.Root>

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
