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
import { ProfileRow } from './ProfileRow'
import { Image as ImageIcon, SquarePen, Trash2 } from 'lucide-react'

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
export function ImageProfilePanel({ openCreateAt = 0 }: {
  /** 页签行那个「新增配置」按钮的计数。一变就打开新建表单——按钮在父组件那儿，
   *  但表单在这儿。 */
  openCreateAt?: number
}) {
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

  useEffect(() => {
    if (openCreateAt > 0) startCreate()
    // 只认计数变化；startCreate 每次渲染都是新函数，进依赖会变成一渲染就弹表单
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openCreateAt])

  /** 岗位卡要知道现在谁在出图。 */
  const active = profiles.find((p) => p.is_active)

  if (loading) return <p style={{ color: 'var(--color-text-secondary)' }}>加载中...</p>

  return (
    <div>
      {/* 与对话页同一套语言：岗位摆在最前，说清它管什么、现在是谁。
          生图只有一个岗位，卡也就一张——单张不撑满整行，见 .role-grid--single。 */}
      <div className="role-grid role-grid--single">
        <div className={`role-card${active ? '' : ' role-card--vacant'}`}>
          <div className="role-card__head">
            <span className="role-card__label">出图</span>
            {active
              ? <span className="role-card__who">{active.name}</span>
              : <span className="role-card__who role-card__who--vacant">未指定</span>}
          </div>
          <p className="role-card__duty">场景插画、信件与道具图片</p>
          <p className="role-card__foot">
            {active
              ? (active.backend === 'comfyui'
                  ? active.comfyui_base_url || 'ComfyUI'
                  : active.model || '（未填模型名）')
              : '不指定就没有配图，不影响正常游戏'}
          </p>
        </div>
      </div>

      {profiles.length === 0 ? (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)', margin: 0 }}>
            还没有添加生图模型，游戏中不会生成图片。这不影响正常游戏。
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '1rem' }}>
          {profiles.map((p) => (
            <ProfileRow
              key={p.id}
              name={p.name}
              highlighted={p.is_active}
              menuLabel={`${p.name} 的更多操作`}
              badges={
                <>
                  {p.is_active && <span className="role-badge">出图</span>}
                  <span className="badge">{p.backend === 'comfyui' ? 'ComfyUI' : 'OpenAI'}</span>
                </>
              }
              meta={p.backend === 'comfyui'
                ? (p.comfyui_base_url || '（未填 ComfyUI 地址）')
                : <>{p.model || '（未填模型名）'}{p.base_url && <span className="profile-row__url"> · {p.base_url}</span>}</>}
              primary={p.is_active ? undefined : {
                label: '设为出图',
                onClick: () => void handleActivate(p.id),
                disabled: editingId !== null,
                title: editingId !== null ? '先保存或取消正在编辑的配置' : undefined,
                ariaLabel: `让 ${p.name} 来出图`,
              }}
              menuItems={[
                ...(editingId !== null ? [] : [{
                  label: '编辑', icon: <SquarePen size={12} />, onClick: () => startEdit(p),
                }]),
                {
                  label: testingId === p.id ? '测试中…' : '测试生图',
                  icon: <ImageIcon size={12} />,
                  onClick: () => void handleTest(p.id),
                  title: '真出一张图，确认这份配置能正常生成图片',
                },
                {
                  label: '删除', icon: <Trash2 size={12} />,
                  onClick: () => void handleDelete(p.id, p.name), separated: true,
                },
              ]}
            />
          ))}
        </div>
      )}

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
                  生图方式
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
                    <SelectItem value="openai">在线服务（OpenAI 等）</SelectItem>
                    <SelectItem value="comfyui">本机 ComfyUI</SelectItem>
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
                      placeholder={'留空即可，会用内置的默认流程。\n想用自己的流程：在 ComfyUI 里「导出 (API)」，把内容粘贴到这里，\n并把正、反向提示词分别改成 PLACEHOLDER_POSITIVE 和 PLACEHOLDER_NEGATIVE。'}
                      value={form.comfyui_workflow}
                      onChange={(e) => setForm({ ...form, comfyui_workflow: e.target.value })}
                    />
                    <p className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                      生成时会把这两个占位词替换成实际的画面描述。
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
                      服务地址
                    </label>
                    <input
                      className="input w-full"
                      placeholder="留空则使用 OpenAI 官方地址"
                      value={form.base_url}
                      onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    />
                  </div>
                  <ApiKeyField
                    value={form.api_key}
                    onChange={(v) => setForm({ ...form, api_key: v })}
                    placeholder="sk-..."
                    hint="填这个生图服务自己的密钥；它与对话模型的密钥互不相干。"
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
    </div>
  )
}
