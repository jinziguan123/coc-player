import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import { localApi } from '../../api/client'
import { Modal } from '../../components/ui/modal'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ApiKeyField } from './ApiKeyField'
import { ImageProfilePanel } from './ImageProfilePanel'

interface AIProfile {
  id: string
  name: string
  protocol: 'openai' | 'anthropic'
  base_url: string
  model_name: string
  api_key: string
  is_active: boolean
  is_fast?: boolean
  vision?: boolean
  context_window?: number
  reasoning_effort?: string
}

interface TestResult {
  success: boolean
  message: string
  latency_ms: number
}

type FormData = {
  name: string
  protocol: 'openai' | 'anthropic'
  base_url: string
  model_name: string
  api_key: string
  vision: boolean
  context_window: number
  reasoning_effort: string
}

const EMPTY_FORM: FormData = {
  name: '',
  protocol: 'openai',
  base_url: '',
  model_name: '',
  api_key: '',
  vision: false,
  context_window: 0,
  reasoning_effort: '',
}

const PROTOCOL_INFO: Record<string, { urlPlaceholder: string; modelPlaceholder: string }> = {
  openai: { urlPlaceholder: 'https://api.deepseek.com', modelPlaceholder: 'deepseek-chat' },
  anthropic: { urlPlaceholder: 'https://api.anthropic.com', modelPlaceholder: 'claude-sonnet-4-20250514' },
}

/** 思考等级（reasoning_effort）只在 OpenAI 兼容协议下会真正下发——Anthropic 的 Provider 不接这个参数。 */
const supportsReasoning = (protocol: string) => protocol === 'openai'

export function AISettingsPanel({ onTestSuccess }: { onTestSuccess?: () => void }) {
  const [profiles, setProfiles] = useState<AIProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null) // null=列表, 'new'=新建
  const [form, setForm] = useState<FormData>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)

  const fetchProfiles = useCallback(async () => {
    try {
      setProfiles(await localApi.get<AIProfile[]>('/settings/ai/profiles'))
    } catch {
      toast.error('加载配置列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchProfiles()
  }, [fetchProfiles])

  const activeProfile = profiles.find((p) => p.is_active)

  const startCreate = () => {
    setEditingId('new')
    setForm(EMPTY_FORM)
  }

  const startEdit = (p: AIProfile) => {
    setEditingId(p.id)
    setForm({
      name: p.name,
      protocol: p.protocol,
      base_url: p.base_url,
      model_name: p.model_name,
      api_key: p.api_key,
      vision: !!p.vision,
      context_window: p.context_window || 0,
      reasoning_effort: p.reasoning_effort || '',
    })
  }

  const cancelEdit = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  /** 掩码回填：列表接口恒掩码，要明文时才向后端单独取。 */
  const revealKey = async (): Promise<string> => {
    if (editingId && editingId !== 'new' && form.api_key.includes('****')) {
      const res = await localApi.get<{ api_key: string }>(`/settings/ai/profiles/${editingId}/key`)
      setForm((f) => ({ ...f, api_key: res.api_key }))
      return res.api_key
    }
    return form.api_key
  }

  const handleDuplicate = async (id: string) => {
    try {
      await localApi.post(`/settings/ai/profiles/${id}/duplicate`)
      toast.success('已复制配置（名称加「副本」，含密钥）')
      await fetchProfiles()
    } catch {
      toast.error('复制配置失败')
    }
  }

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error('请输入配置名称')
      return
    }
    setSaving(true)
    try {
      if (editingId === 'new') {
        await localApi.post('/settings/ai/profiles', form)
        toast.success('配置已创建')
      } else {
        await localApi.put(`/settings/ai/profiles/${editingId}`, form)
        toast.success('配置已更新')
      }
      cancelEdit()
      await fetchProfiles()
    } catch (e) {
      toast.error(`保存失败: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setSaving(false)
    }
  }

  const handleActivate = async (id: string) => {
    try {
      await localApi.post(`/settings/ai/profiles/${id}/activate`)
      toast.success('已切换激活配置')
      await fetchProfiles()
    } catch {
      toast.error('激活失败')
    }
  }

  /* 标记/取消快模型（结构化副任务：裁定 planner、AI 队友、滚动摘要走它，省时提速） */
  const handleToggleFast = async (id: string) => {
    try {
      const res = await localApi.post<{ is_fast: boolean }>(`/settings/ai/profiles/${id}/set-fast`)
      toast.success(
        res.is_fast ? '已设为快模型（裁定/队友/摘要将走它）' : '已取消快模型，这些任务改用主模型',
      )
      await fetchProfiles()
    } catch {
      toast.error('设置快模型失败')
    }
  }

  const handleDelete = async (id: string, name: string) => {
    if (!window.confirm(`确定要删除配置「${name}」吗？`)) return
    try {
      await localApi.delete(`/settings/ai/profiles/${id}`)
      toast.success('配置已删除')
      if (editingId === id) cancelEdit()
      await fetchProfiles()
    } catch {
      toast.error('删除失败')
    }
  }

  const handleTest = async (id: string) => {
    setTestingId(id)
    try {
      const result = await localApi.post<TestResult>(`/settings/ai/profiles/${id}/test`)
      if (result.success) {
        toast.success(`${result.message}（${result.latency_ms}ms）`)
        onTestSuccess?.()
      } else {
        toast.error(`连接失败: ${result.message}`)
      }
    } catch (e) {
      toast.error(`测试出错: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setTestingId(null)
    }
  }

  if (loading) return <p style={{ color: 'var(--color-text-secondary)' }}>加载中...</p>

  const info = PROTOCOL_INFO[form.protocol] || PROTOCOL_INFO.openai
  // 切到 Anthropic 但思考等级还留着旧值：该值不会下发，必须说清楚，否则用户以为它还在起作用。
  const staleReasoning = !supportsReasoning(form.protocol) && !!form.reasoning_effort

  return (
    <div>
      <h2 className="page-title">AI 配置</h2>

      {/* 两类模型互不相干，各自独立成页：一次只看一套配置，不必在长页面里上下找。
          页面级页签按内容宽度左对齐——tabs.tsx 默认的 flex-1 是给窄弹窗用的，
          铺满整行会把两个标签拉成两条大色块。 */}
      <Tabs defaultValue="chat">
        <TabsList style={{ marginBottom: '1.25rem' }}>
          <TabsTrigger value="chat" className="!flex-none !text-[length:var(--text-sm)] px-5">
            对话模型
          </TabsTrigger>
          <TabsTrigger value="image" className="!flex-none !text-[length:var(--text-sm)] px-5">
            生图模型
          </TabsTrigger>
        </TabsList>

        <TabsContent value="chat" className="!p-0">
      <div
        style={{
          display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
          gap: '1rem', marginBottom: '0.85rem',
        }}
      >
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)', margin: 0 }}>
          游戏里主持人的叙述、NPC 的对话，以及骰子和规则的判定，都由这个模型生成。
        </p>
        <button
          className="btn-primary !px-3 !py-1.5 !text-[length:var(--text-xs)]"
          style={{ flexShrink: 0 }}
          onClick={startCreate}
          disabled={editingId !== null}
        >
          + 新增配置
        </button>
      </div>

      {/* 当前激活配置状态 */}
      <div
        className="card"
        style={{ marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}
      >
        <span
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: activeProfile ? 'var(--color-success)' : 'var(--color-danger)',
            flexShrink: 0,
          }}
        />
        <span style={{ fontSize: '0.875rem' }}>
          {activeProfile ? (
            <>
              当前激活：
              <strong>{activeProfile.name}</strong>
              <span className="badge" style={{ marginLeft: '0.5rem' }}>
                {activeProfile.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容'}
              </span>
              <span
                style={{
                  marginLeft: '0.5rem',
                  color: 'var(--color-text-secondary)',
                  fontSize: '0.8rem',
                }}
              >
                {activeProfile.model_name}
              </span>
            </>
          ) : (
            <span style={{ color: 'var(--color-text-secondary)' }}>
              还没有启用任何配置，请先添加一个并点「激活」
            </span>
          )}
        </span>
      </div>

      {/* 配置列表 */}
      {profiles.length > 0 && (
        <div
          style={{
            display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1rem',
          }}
        >
          {profiles.map((p) => (
            <div
              key={p.id}
              className={`card ${p.is_active ? 'active-rail' : ''}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                padding: '0.75rem 1rem',
                borderColor: p.is_active ? 'var(--color-accent)' : undefined,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem',
                  }}
                >
                  <strong style={{ fontSize: '0.9rem' }}>{p.name}</strong>
                  <span className="badge">
                    {p.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI'}
                  </span>
                  {p.is_active && <span className="chip chip--success">已激活</span>}
                  {p.is_fast && (
                    <span
                      className="chip chip--accent"
                      title="裁定 planner、AI 队友、滚动摘要等结构化副任务走此配置；KP 叙事仍走激活配置"
                    >
                      快模型
                    </span>
                  )}
                </div>
                <div
                  style={{
                    fontSize: '0.8rem',
                    color: 'var(--color-text-secondary)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {p.model_name}
                  {p.base_url && ` · ${p.base_url}`}
                </div>
              </div>

              {/* 操作区：「激活」是这里唯一有后果的主动作，保留实心按钮；
                  其余降为 chip 级次要动作；删除单独走危险色。 */}
              <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-1">
                {!p.is_active && (
                  <button
                    className="btn-primary !px-2.5 !py-1 !text-[length:var(--text-xs)]"
                    onClick={() => handleActivate(p.id)}
                    aria-label={`激活 ${p.name}`}
                  >
                    激活
                  </button>
                )}
                <button
                  className="chip hover:!border-[var(--color-accent)] hover:!text-[var(--color-text-accent)] transition-colors"
                  onClick={() => handleToggleFast(p.id)}
                  aria-label={`${p.is_fast ? '取消' : '设为'}快模型 ${p.name}`}
                  title="快模型：裁定 planner、AI 队友、滚动摘要等结构化副任务改走此配置（KP 叙事仍走激活配置）；再点一次取消"
                >
                  {p.is_fast ? '取消快模型' : '设为快模型'}
                </button>
                <button
                  className="chip hover:!border-[var(--color-accent)] hover:!text-[var(--color-text-accent)] transition-colors disabled:opacity-40"
                  onClick={() => startEdit(p)}
                  disabled={editingId !== null}
                  aria-label={`编辑 ${p.name}`}
                >
                  编辑
                </button>
                <button
                  className="chip hover:!border-[var(--color-accent)] hover:!text-[var(--color-text-accent)] transition-colors"
                  onClick={() => handleDuplicate(p.id)}
                  aria-label={`复制 ${p.name}`}
                  title="复制一份此配置（含密钥），改个模型名即可做成快模型变体"
                >
                  复制
                </button>
                <button
                  className="chip hover:!border-[var(--color-accent)] hover:!text-[var(--color-text-accent)] transition-colors disabled:opacity-40"
                  onClick={() => handleTest(p.id)}
                  disabled={testingId !== null}
                  aria-label={`测试 ${p.name}`}
                >
                  {testingId === p.id ? '测试中…' : '测试'}
                </button>
                <button
                  className="chip hover:!border-[var(--color-danger)] hover:!text-[var(--color-danger)] transition-colors"
                  onClick={() => handleDelete(p.id, p.name)}
                  aria-label={`删除 ${p.name}`}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editingId !== null && (
        <Modal onClose={cancelEdit} widthClass="max-w-xl" padded>
          <h3 className="card-title">{editingId === 'new' ? '新增配置' : '编辑配置'}</h3>
          {/* 分页签而非一条长表单：连接四件套（协议/地址/模型/密钥）本该挨在一起，
              从前被「高级配置」折叠块从中劈开，密钥反而落在最下面。 */}
          <Tabs defaultValue="conn">
            <TabsList>
              <TabsTrigger value="conn">连接</TabsTrigger>
              <TabsTrigger value="caps">能力</TabsTrigger>
            </TabsList>

            <TabsContent
              value="conn"
              className="!p-0 !pt-4"
              style={{ maxHeight: '62vh', overflowY: 'auto' }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
                <div>
                  <label
                    className="block text-sm font-semibold mb-1"
                    style={{ fontSize: '0.85rem' }}
                  >
                    配置名称
                  </label>
                  <input
                    type="text"
                    className="input w-full"
                    placeholder="例如：DeepSeek 生产环境"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </div>

                <div>
                  <label
                    className="block text-sm font-semibold mb-1"
                    style={{ fontSize: '0.85rem' }}
                  >
                    API 协议
                  </label>
                  <Select
                    value={form.protocol}
                    onValueChange={(v) =>
                      setForm({ ...form, protocol: v as 'openai' | 'anthropic' })
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="openai">OpenAI 兼容</SelectItem>
                      <SelectItem value="anthropic">Anthropic</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                    {form.protocol === 'openai'
                      ? '兼容 OpenAI API 格式的服务（DeepSeek、OpenAI、Ollama 等）'
                      : 'Anthropic Claude Messages API'}
                  </p>
                </div>

                <div>
                  <label
                    className="block text-sm font-semibold mb-1"
                    style={{ fontSize: '0.85rem' }}
                  >
                    服务地址
                  </label>
                  <input
                    type="text"
                    className="input w-full"
                    placeholder={info.urlPlaceholder}
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  />
                  <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                    留空则使用默认地址（{info.urlPlaceholder}）
                  </p>
                </div>

                <div>
                  <label
                    className="block text-sm font-semibold mb-1"
                    style={{ fontSize: '0.85rem' }}
                  >
                    模型名称
                  </label>
                  <input
                    type="text"
                    className="input w-full"
                    placeholder={info.modelPlaceholder}
                    value={form.model_name}
                    onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                  />
                </div>

                <ApiKeyField
                  value={form.api_key}
                  onChange={(v) => setForm({ ...form, api_key: v })}
                  placeholder={form.protocol === 'anthropic' ? 'sk-ant-...' : 'sk-...'}
                  hint="使用本机运行的模型（如 Ollama）时可以留空"
                  revealKey={revealKey}
                />
              </div>
            </TabsContent>

            <TabsContent
              value="caps"
              className="!p-0 !pt-4"
              style={{ maxHeight: '62vh', overflowY: 'auto' }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <div>
                  <label
                    className="flex items-center gap-2 text-sm font-semibold cursor-pointer"
                    style={{ fontSize: '0.85rem' }}
                  >
                    <input
                      type="checkbox"
                      checked={form.vision}
                      onChange={(e) => setForm({ ...form, vision: e.target.checked })}
                    />
                    支持视觉（多模态）
                  </label>
                  <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                    勾选后才能使用「上传图片生成地图」「图片剧本解析」等需要模型看图的功能。
                    请确认所选模型支持看图（如 GPT-4o、Claude、Gemini、Qwen-VL）。
                    这里说的是「看懂图片」，生成图片请到「生图模型」页设置。
                  </p>
                </div>

                <div>
                  <label
                    className="block text-sm font-semibold mb-1"
                    style={{ fontSize: '0.85rem' }}
                  >
                    上下文窗口（token）
                  </label>
                  <input
                    type="number"
                    min={0}
                    className="input w-full"
                    placeholder="留空/0：按模型名自动判断（如 deepseek≈64k、claude≈200k）"
                    value={form.context_window || ''}
                    onChange={(e) =>
                      setForm({ ...form, context_window: Number(e.target.value) || 0 })
                    }
                  />
                  <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                    用来估算游戏页显示的「上下文占用」，提示这局还能继续多久。留空会按模型名自动判断。
                  </p>
                </div>

                {/* 思考等级只在 OpenAI 兼容协议下出现——Anthropic 的 Provider 不接这个参数，
                    摆在那里只会让人填一个永远不生效的值。 */}
                {supportsReasoning(form.protocol) ? (
                  <div>
                    <label
                      className="block text-sm font-semibold mb-1"
                      style={{ fontSize: '0.85rem' }}
                    >
                      思考等级
                    </label>
                    {/* 手填而非下拉：各家取值并不统一（有的只认 low/medium/high，有的还有
                        minimal/xhigh，往后还会变），写死选项等于把能用的值挡在外面。 */}
                    <input
                      type="text"
                      className="input w-full"
                      placeholder="留空 = 用模型默认档；如需指定填 minimal、low、medium、high 等"
                      value={form.reasoning_effort}
                      onChange={(e) => setForm({ ...form, reasoning_effort: e.target.value })}
                    />
                    <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                      只对带思考能力的模型有用，等级越高想得越久、也越贵。
                      具体能填哪些值请看你所用模型的文档；普通模型请留空，否则有些服务会直接报错。
                    </p>
                    <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                      <strong style={{ color: 'var(--color-text-accent)' }}>留空不等于关闭思考</strong>
                      ——留空表示<strong>不下发这个参数</strong>，模型用它自己的默认档；
                      而不少思考型模型默认就是开的。嫌跑团一个回合等太久，
                      请显式填 <code className="coach-code">minimal</code> 或
                      <code className="coach-code">low</code>，而不是清空。
                    </p>
                  </div>
                ) : (
                  staleReasoning && (
                    <div className="notice notice--danger" role="alert">
                      <AlertTriangle size={13} style={{ flexShrink: 0 }} aria-hidden="true" />
                      <span>
                        这份配置里还留着思考等级「{form.reasoning_effort}」，但 Anthropic 的模型不支持这项设置，
                        它不会起任何作用。
                      </span>
                      <button
                        type="button"
                        className="chip"
                        onClick={() => setForm({ ...form, reasoning_effort: '' })}
                      >
                        清除
                      </button>
                    </div>
                  )
                )}
              </div>
            </TabsContent>
          </Tabs>

          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
            <button className="btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存'}
            </button>
            <button className="btn-secondary" onClick={cancelEdit}>
              取消
            </button>
          </div>
        </Modal>
      )}

        </TabsContent>

        <TabsContent value="image" className="!p-0">
          <ImageProfilePanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
