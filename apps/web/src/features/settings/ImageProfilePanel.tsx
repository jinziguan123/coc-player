import { useCallback, useEffect, useState } from 'react'
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
import { ApiKeyField } from './ApiKeyField'

export interface ImageProfile {
  id: string
  name: string
  backend: 'openai' | 'comfyui'
  is_active: boolean
  model: string
  base_url: string
  api_key: string
  comfyui_base_url: string
  comfyui_workflow: string
}

type FormData = Omit<ImageProfile, 'id' | 'is_active'>

const EMPTY_FORM: FormData = {
  name: '',
  backend: 'openai',
  model: '',
  base_url: '',
  api_key: '',
  comfyui_base_url: '',
  comfyui_workflow: '',
}

interface TestResult {
  success: boolean
  message: string
  latency_ms: number
}

/**
 * 生图模型配置：与对话模型完全独立的一套增删改与激活。
 *
 * 从前生图设置寄生在每个对话配置里，后果是同一台 ComfyUI 要在每个配置里重抄一遍，
 * 而且 `image_model` 只在 OpenAI 协议下才会真正生效——用 Anthropic 跑团时填了也白填。
 * 拆开之后：用什么模型跑团，与用什么后端出图，互不相干。
 */
export function ImageProfilePanel() {
  const [profiles, setProfiles] = useState<ImageProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null) // null=列表, 'new'=新建
  const [form, setForm] = useState<FormData>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)

  const fetchProfiles = useCallback(async () => {
    try {
      setProfiles(await localApi.get<ImageProfile[]>('/settings/ai/image-profiles'))
    } catch {
      toast.error('加载生图配置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchProfiles()
  }, [fetchProfiles])

  const active = profiles.find((p) => p.is_active)

  const startCreate = () => {
    setEditingId('new')
    setForm(EMPTY_FORM)
  }

  const startEdit = (p: ImageProfile) => {
    setEditingId(p.id)
    setForm({
      name: p.name,
      backend: p.backend === 'comfyui' ? 'comfyui' : 'openai',
      model: p.model || '',
      base_url: p.base_url || '',
      api_key: p.api_key || '',
      comfyui_base_url: p.comfyui_base_url || '',
      comfyui_workflow: p.comfyui_workflow || '',
    })
  }

  const cancelEdit = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
  }

  /** 掩码回填：列表接口恒掩码，要明文时才向后端单独取。 */
  const revealKey = async (): Promise<string> => {
    if (editingId && editingId !== 'new' && form.api_key.includes('****')) {
      const res = await localApi.get<{ api_key: string }>(
        `/settings/ai/image-profiles/${editingId}/key`,
      )
      setForm((f) => ({ ...f, api_key: res.api_key }))
      return res.api_key
    }
    return form.api_key
  }

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error('请输入配置名称')
      return
    }
    setSaving(true)
    try {
      if (editingId === 'new') {
        await localApi.post('/settings/ai/image-profiles', form)
        toast.success('生图配置已创建')
      } else {
        await localApi.put(`/settings/ai/image-profiles/${editingId}`, form)
        toast.success('生图配置已更新')
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
      await localApi.post(`/settings/ai/image-profiles/${id}/activate`)
      toast.success('已切换生图配置')
      await fetchProfiles()
    } catch {
      toast.error('激活失败')
    }
  }

  const handleDelete = async (id: string, name: string) => {
    if (!window.confirm(`确定要删除生图配置「${name}」吗？`)) return
    try {
      await localApi.delete(`/settings/ai/image-profiles/${id}`)
      toast.success('生图配置已删除')
      if (editingId === id) cancelEdit()
      await fetchProfiles()
    } catch {
      toast.error('删除失败')
    }
  }

  const handleTest = async (id: string) => {
    setTestingId(id)
    try {
      const result = await localApi.post<TestResult>(`/settings/ai/image-profiles/${id}/test`)
      if (result.success) toast.success(`${result.message}（${result.latency_ms}ms）`)
      else toast.error(`生图测试失败: ${result.message}`)
    } catch (e) {
      toast.error(`测试出错: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setTestingId(null)
    }
  }

  if (loading) return <p style={{ color: 'var(--color-text-secondary)' }}>加载中...</p>

  return (
    <div style={{ marginTop: '2rem' }}>
      <h3
        className="card-title"
        style={{ fontSize: '1rem', marginBottom: '0.35rem' }}
      >
        生图模型
      </h3>
      <p
        className="text-xs"
        style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}
      >
        手书配图、场景插画与沙盘底图用它出图，与上面的对话模型各走各的——
        用 Anthropic 跑团也能配 OpenAI 或本地 ComfyUI 出图。不配则不出图，游戏照常进行。
      </p>

      {profiles.length === 0 ? (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)', margin: 0 }}>
            尚未配置生图模型——手书与场景配图会静默跳过，不影响跑团。
          </p>
        </div>
      ) : (
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
                    {p.backend === 'comfyui' ? 'ComfyUI' : 'OpenAI'}
                  </span>
                  {p.is_active && <span className="chip chip--success">使用中</span>}
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
                  {p.backend === 'comfyui'
                    ? p.comfyui_base_url || '（未填 ComfyUI 地址）'
                    : `${p.model || '（未填模型名）'}${p.base_url ? ` · ${p.base_url}` : ''}`}
                </div>
              </div>

              <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-1">
                {!p.is_active && (
                  <button
                    className="btn-primary !px-2.5 !py-1 !text-[length:var(--text-xs)]"
                    onClick={() => handleActivate(p.id)}
                    aria-label={`使用 ${p.name} 出图`}
                  >
                    使用
                  </button>
                )}
                <button
                  className="chip hover:!border-[var(--color-accent)] hover:!text-[var(--color-text-accent)] transition-colors disabled:opacity-40"
                  onClick={() => startEdit(p)}
                  disabled={editingId !== null}
                  aria-label={`编辑 ${p.name}`}
                >
                  编辑
                </button>
                <button
                  className="chip hover:!border-[var(--color-accent)] hover:!text-[var(--color-text-accent)] transition-colors disabled:opacity-40"
                  onClick={() => handleTest(p.id)}
                  disabled={testingId !== null}
                  aria-label={`测试生图 ${p.name}`}
                  title="真出一张图，验证这份配置能否用于配图"
                >
                  {testingId === p.id ? '测试中…' : '测试生图'}
                </button>
                <button
                  className="chip chip--danger hover:!bg-[var(--color-danger-deep)] hover:!text-[var(--color-on-danger)] transition-colors"
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

      <button className="btn-secondary" onClick={startCreate} disabled={editingId !== null}>
        + 新增生图配置
      </button>

      {editingId !== null && (
        <Modal onClose={cancelEdit} widthClass="max-w-xl" padded>
          <div style={{ maxHeight: '78vh', overflowY: 'auto', paddingRight: '0.25rem' }}>
            <h3 className="card-title">
              {editingId === 'new' ? '新增生图配置' : '编辑生图配置'}
            </h3>
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
                  placeholder="例如：本地 ComfyUI"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>

              <div>
                <label
                  className="block text-sm font-semibold mb-1"
                  style={{ fontSize: '0.85rem' }}
                >
                  出图后端
                </label>
                <Select
                  value={form.backend}
                  onValueChange={(v) =>
                    setForm({ ...form, backend: v as 'openai' | 'comfyui' })
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="openai">OpenAI 接口</SelectItem>
                    <SelectItem value="comfyui">ComfyUI</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {form.backend === 'comfyui' ? (
                <>
                  <div>
                    <label
                      className="block text-sm font-semibold mb-1"
                      style={{ fontSize: '0.85rem' }}
                    >
                      ComfyUI 地址
                    </label>
                    <input
                      className="input w-full"
                      placeholder="如 http://127.0.0.1:8188"
                      value={form.comfyui_base_url}
                      onChange={(e) => setForm({ ...form, comfyui_base_url: e.target.value })}
                    />
                  </div>
                  <div>
                    <label
                      className="block text-sm font-semibold mb-1"
                      style={{ fontSize: '0.85rem' }}
                    >
                      工作流 JSON（可选）
                    </label>
                    <textarea
                      className="input w-full"
                      rows={6}
                      style={{
                        fontFamily: 'var(--font-mono, monospace)',
                        fontSize: '0.78rem',
                        resize: 'vertical',
                      }}
                      placeholder={'从 ComfyUI 菜单导出 (API) 格式粘贴到这里；\n正/负提示词处分别写 PLACEHOLDER_POSITIVE / PLACEHOLDER_NEGATIVE 占位；\n留空则使用内置默认工作流。'}
                      value={form.comfyui_workflow}
                      onChange={(e) => setForm({ ...form, comfyui_workflow: e.target.value })}
                    />
                    <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                      后端会把正/负提示词占位符替换成画面描述后提交生图。
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label
                      className="block text-sm font-semibold mb-1"
                      style={{ fontSize: '0.85rem' }}
                    >
                      生图模型
                    </label>
                    <input
                      className="input w-full"
                      placeholder="如 dall-e-3、gpt-image-1"
                      value={form.model}
                      onChange={(e) => setForm({ ...form, model: e.target.value })}
                    />
                  </div>
                  <div>
                    <label
                      className="block text-sm font-semibold mb-1"
                      style={{ fontSize: '0.85rem' }}
                    >
                      Base URL
                    </label>
                    <input
                      className="input w-full"
                      placeholder="留空则用 https://api.openai.com/v1"
                      value={form.base_url}
                      onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    />
                  </div>
                  <ApiKeyField
                    value={form.api_key}
                    onChange={(v) => setForm({ ...form, api_key: v })}
                    placeholder="sk-..."
                    hint="生图与对话常不在同一分组/供应商，这里填生图端点自己的密钥。"
                    revealKey={revealKey}
                  />
                </>
              )}

              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
                <button className="btn-primary" onClick={handleSave} disabled={saving}>
                  {saving ? '保存中...' : '保存'}
                </button>
                <button className="btn-secondary" onClick={cancelEdit}>
                  取消
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {active && (
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginTop: '0.6rem' }}>
          当前用「{active.name}」出图。保存后点该卡片的「测试生图」可确认是否真的能出图。
        </p>
      )}
    </div>
  )
}
