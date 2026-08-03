// 模组配图槽位：看图 + 重新生成 + 手动上传，编辑态与查看态通用。
//
// 此前配图只有一条隐形路径：`{!edit && item.image && <ModuleImage/>}`——
//   · 编辑模组时整个不渲染，房主在改场景的时候根本看不到这个场景长什么样；
//   · 没有图的条目连占位都没有，也就没有任何入口去生成第一张；
//   · 只有 <img onError> 触发的自愈重生成，没有面向人的按钮，出图不满意只能干瞪眼；
//   · 完全没有上传通道，没配文生图模型的人一张图都拿不到。
// 这个组件把这四件事补齐，三处配图（场景 / NPC / 线索）共用。
import { useRef, useState } from 'react'
import { ImageOff, LoaderCircle, RefreshCw, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { api, getServerUrl, uploadFile } from '@/api/client'
import type { ModuleImageKind } from './ModuleImage'
import { useRepairableImage } from './ModuleImage'

type ImageField = 'image' | 'image_variant' | 'portrait' | 'encounter_image'

interface ImageSlotProps {
  src?: string
  /** 新建但尚未保存的模组没有 id —— 此时换图只会 404，按钮置灰 */
  moduleId?: string
  kind: ModuleImageKind
  itemId: string
  field: ImageField
  alt: string
  aspectRatio?: string
  className?: string
  /** 新图落库后回调，调用方据此更新本地模组草稿 */
  onChange: (url: string) => void
  visualStateKey?: string
}

export function ImageSlot({
  src, moduleId, kind, itemId, field, alt,
  aspectRatio = '16 / 9', className = '', onChange, visualStateKey,
}: ImageSlotProps) {
  const image = useRepairableImage({ src, moduleId, kind, itemId, field, onRegenerated: onChange, visualStateKey })
  const [busy, setBusy] = useState<'' | 'gen' | 'upload'>('')
  const fileRef = useRef<HTMLInputElement>(null)

  // 模组或条目还没落库（后端查不到），换图必然 404 —— 与其让用户点了才吃报错，
  // 不如先把按钮禁掉并说清楚原因。
  const unsaved = !moduleId || !itemId

  const regenerate = async () => {
    if (busy) return
    setBusy('gen')
    try {
      // force：不加的话后端是自愈语义——图还在就把原图返回来，点了跟没点一样
      const r = await api.post<{ url: string }>(`/modules/${moduleId}/images/regenerate`, {
        kind, item_id: itemId, field, visual_state_key: visualStateKey, force: true,
      })
      onChange(r.url)
      toast.success('已重新生成配图')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '重新生成失败')
    } finally {
      setBusy('')
    }
  }

  const upload = async (file: File) => {
    if (busy) return
    setBusy('upload')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('kind', kind)
      form.append('item_id', itemId)
      form.append('field', field)
      if (visualStateKey) form.append('visual_state_key', visualStateKey)
      const r = await uploadFile<{ url: string }>(`/modules/${moduleId}/images/upload`, form)
      onChange(r.url)
      toast.success('已更新配图')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '上传失败')
    } finally {
      setBusy('')
      if (fileRef.current) fileRef.current.value = ''   // 允许连选同一个文件重传
    }
  }

  const working = !!busy || image.status === 'regenerating'
  const absolute = src && !/^https?:\/\//i.test(src) ? `${getServerUrl()}${src}` : src

  return (
    <div className={className}>
      <div
        className="relative overflow-hidden rounded-md"
        style={{ aspectRatio, border: '1px solid var(--color-border)', background: 'var(--color-bg-tertiary)' }}
      >
        {src && image.status !== 'failed' && (
          <img
            src={image.imageUrl || absolute}
            alt={alt}
            className="block h-full w-full"
            style={{ objectFit: 'cover', opacity: image.status === 'ready' ? 1 : 0.35 }}
            onLoad={image.onLoad}
            onError={image.onError}
          />
        )}
        {!src && !working && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-xs"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            <ImageOff size={20} />
            <span>暂无配图</span>
          </div>
        )}
        {src && image.status === 'failed' && (
          <div
            className="absolute inset-0 flex items-center justify-center gap-2 text-xs"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            <ImageOff size={18} /> 图片暂不可用
          </div>
        )}
        {working && (
          <div
            className="absolute inset-0 flex items-center justify-center gap-2 text-xs"
            style={{ background: 'rgba(0,0,0,0.45)', color: 'var(--color-text-primary)' }}
            aria-label={busy === 'upload' ? '图片上传中' : '配图生成中'}
          >
            <LoaderCircle className="animate-spin" size={18} />
            {busy === 'upload' ? '上传中…' : '生成中…'}
          </div>
        )}
      </div>

      <div className="mt-1.5 flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => void regenerate()}
          disabled={working || unsaved}
          title={unsaved ? '这个条目还没保存，先保存模组再换图' : 'AI 按该条目的文字描述重新出一张'}
          className="btn-secondary !px-2 !py-0.5 text-xs inline-flex items-center gap-1"
          style={{ opacity: working || unsaved ? 0.5 : 1 }}
        >
          <RefreshCw size={11} /> {src ? '重新生成' : 'AI 生成'}
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={working || unsaved}
          title={unsaved ? '这个条目还没保存，先保存模组再换图' : '上传本地图片替换（JPG / PNG / WebP）'}
          className="btn-secondary !px-2 !py-0.5 text-xs inline-flex items-center gap-1"
          style={{ opacity: working || unsaved ? 0.5 : 1 }}
        >
          <Upload size={11} /> 上传
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f) }}
        />
      </div>
    </div>
  )
}
