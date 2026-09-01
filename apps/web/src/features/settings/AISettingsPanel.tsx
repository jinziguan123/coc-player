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
import { ModelNameField } from './ModelNameField'
import { ModelRoles } from './ModelRoles'
import { ProfileRow } from './ProfileRow'
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
  is_vision?: boolean
  context_window?: number
  thinking_disabled?: boolean
  reasoning_effort?: string
}

/** 岗位卡只要「是谁、跑的哪个模型」这两样。 */
function roleHolder(p?: AIProfile) {
  return p ? { id: p.id, name: p.name, model_name: p.model_name } : undefined
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
  context_window: number
  thinking_disabled: boolean
  reasoning_effort: string
}

const EMPTY_FORM: FormData = {
  name: '',
  protocol: 'openai',
  base_url: '',
  model_name: '',
  api_key: '',
  context_window: 0,
  thinking_disabled: false,
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
      context_window: p.context_window || 0,
      thinking_disabled: !!p.thinking_disabled,
      reasoning_effort: p.reasoning_effort || '',
    })
  }

  const cancelEdit = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  /** 取真实密钥。列表接口恒掩码，明文要向后端单独要。**不动表单**——
   *  「获取可用模型」也要用它，那可不该顺手把密钥显示出来。 */
  const secretKey = async (): Promise<string> => {
    if (editingId && editingId !== 'new' && form.api_key.includes('****')) {
      const res = await localApi.get<{ api_key: string }>(`/settings/ai/profiles/${editingId}/key`)
      return res.api_key
    }
    return form.api_key
  }

  /** 掩码回填：给「显示密钥」用，取到后填回表单。 */
  const revealKey = async (): Promise<string> => {
    const key = await secretKey()
    setForm((f) => ({ ...f, api_key: key }))
    return key
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

  /* 标记/取消快模型（结构化副任务：裁定 planner、滚动摘要、生图提示词走它，省时提速；
     AI 队友与 KP 叙事一样直接摆在玩家面前，走主模型） */
  const handleToggleFast = async (id: string) => {
    try {
      const res = await localApi.post<{ is_fast: boolean }>(`/settings/ai/profiles/${id}/set-fast`)
      toast.success(
        res.is_fast ? '已设为快模型（裁定/摘要等副任务将走它）' : '已取消快模型，这些任务改用主模型',
      )
      await fetchProfiles()
    } catch {
      toast.error('设置快模型失败')
    }
  }

  /* 标记/取消视觉模型（解析扫描件与图文模组时走它）。单开一档是因为带团模型多为纯文本，
     从前想解析图文模组就得连带团模型一起换——标一个视觉配置即可，主模型不动。 */
  const handleToggleVision = async (id: string) => {
    try {
      const res = await localApi.post<{ is_vision: boolean }>(`/settings/ai/profiles/${id}/set-vision`)
      toast.success(
        res.is_vision
          ? '已设为视觉模型（解析扫描件/图文模组将走它）'
          : '已取消视觉模型，看图改用主模型',
      )
      await fetchProfiles()
    } catch {
      toast.error('设置视觉模型失败')
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
        {/* 页签钉在顶上。往下翻配置时它会跟着滚出视野，人就不知道自己在哪一页了，
            要切还得先滚回去。「新增配置」并到同一行——它是这页唯一的新建入口，
            跟页签一样属于「不随内容走」的那层。 */}
        <div className="settings-tabs flex items-center justify-between gap-4">
          <TabsList>
            <TabsTrigger value="chat" className="!flex-none !text-[length:var(--text-sm)] px-5">
              对话模型
            </TabsTrigger>
            <TabsTrigger value="image" className="!flex-none !text-[length:var(--text-sm)] px-5">
              生图模型
            </TabsTrigger>
          </TabsList>
          <button
            className="btn-primary !px-3 !py-1.5 !text-[length:var(--text-xs)]"
            style={{ flexShrink: 0 }}
            onClick={startCreate}
            disabled={editingId !== null}
          >
            新增配置
          </button>
        </div>

        <TabsContent value="chat" className="!p-0">

      {/* 三个岗位摆在最前：这一页的结构本就是「三个岗位、一批候选」。
          此前它藏在每行三个指派按钮里，八条配置摊出二十四个按钮，而岗位统共只有三个。 */}
      <ModelRoles
        holders={{
          narrator: roleHolder(profiles.find((p) => p.is_active)),
          aide: roleHolder(profiles.find((p) => p.is_fast)),
          reader: roleHolder(profiles.find((p) => p.is_vision)),
        }}
      />

      {profiles.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '1rem' }}>
          {profiles.map((p) => (
            <ProfileRow
              key={p.id}
              profile={p}
              busy={editingId !== null}
              testing={testingId === p.id}
              onAssignNarrator={() => void handleActivate(p.id)}
              onToggleAide={() => void handleToggleFast(p.id)}
              onToggleReader={() => void handleToggleVision(p.id)}
              onEdit={() => startEdit(p)}
              onDuplicate={() => void handleDuplicate(p.id)}
              onTest={() => void handleTest(p.id)}
              onDelete={() => void handleDelete(p.id, p.name)}
            />
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

                <ModelNameField
                  protocol={form.protocol}
                  baseUrl={form.base_url}
                  value={form.model_name}
                  placeholder={info.modelPlaceholder}
                  onChange={(v) => setForm({ ...form, model_name: v })}
                  resolveKey={secretKey}
                />

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
                </div>

                {/* 思考开关与等级只在 OpenAI 兼容协议下出现——Anthropic 的 Provider 不接这两个
                    参数，摆在那里只会让人设一个永远不生效的值。 */}
                {supportsReasoning(form.protocol) && (
                  <div>
                    <label
                      style={{
                        display: 'flex', alignItems: 'flex-start', gap: '0.6rem',
                        cursor: 'pointer', fontSize: '0.85rem',
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={form.thinking_disabled}
                        onChange={(e) =>
                          setForm({
                            ...form,
                            thinking_disabled: e.target.checked,
                            reasoning_effort: e.target.checked ? '' : form.reasoning_effort,
                          })
                        }
                        style={{ marginTop: '0.2rem', flexShrink: 0 }}
                      />
                      <strong>关闭模型思考</strong>
                    </label>
                  </div>
                )}

                {supportsReasoning(form.protocol) ? (
                  !form.thinking_disabled && (
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
                        placeholder="留空 = 用模型默认档（DeepSeek 默认 high）；可填 low / high / max"
                        value={form.reasoning_effort}
                        onChange={(e) => setForm({ ...form, reasoning_effort: e.target.value })}
                      />
                    </div>
                  )
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
