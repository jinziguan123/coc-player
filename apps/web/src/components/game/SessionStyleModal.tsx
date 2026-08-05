// 本局的文风 / 画风（房主专用）。
//
// 层级：本局 > 模组默认 > 系统默认。所以两项都可以留在「跟随模组」——那不是「没设」，
// 是「按模组作者推荐的来」。取值约定见 components/style/StylePicker 与后端 style_presets。
//
// 按房主授权（后端同样按 require_session_manager 把关）：风格是整桌共享的观感，
// 不该让任一玩家单方面改掉别人的体验。
import { useState } from 'react'
import { toast } from 'sonner'
import { GiQuillInk } from 'react-icons/gi'
import { api } from '@/api/client'
import { Modal } from '@/components/ui/modal'
import { StylePicker } from '@/components/style/StylePicker'
import { useStyleOptions } from '@/components/style/useStyleOptions'

export function SessionStyleModal({
  sessionId, narrativeStyle, imageStyle, onSaved, onClose,
}: {
  sessionId: string
  narrativeStyle: string
  imageStyle: string
  onSaved: (next: { narrative_style: string; image_style: string }) => void
  onClose: () => void
}) {
  const options = useStyleOptions()
  const [narrative, setNarrative] = useState(narrativeStyle)
  const [image, setImage] = useState(imageStyle)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      const saved = await api.put<{ narrative_style: string; image_style: string }>(
        `/sessions/${sessionId}/style`,
        { narrative_style: narrative, image_style: image },
      )
      onSaved({ narrative_style: saved.narrative_style, image_style: saved.image_style })
      toast.success('本局风格已更新，下一段叙事起生效')
      onClose()
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} widthClass="max-w-md" padded>
      <div className="flex items-start gap-3">
        <span className="char-sigil !h-10 !w-10" aria-hidden="true"><GiQuillInk /></span>
        <div className="min-w-0 flex-1">
          <h2
            className="font-semibold"
            style={{
              fontFamily: 'var(--font-title)',
              fontSize: 'var(--text-lg)',
              color: 'var(--color-text-accent)',
            }}
          >
            本局的文风与画风
          </h2>
          <p className="mt-0.5 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            只影响这一局。留在「跟随模组」就按模组作者推荐的来。
          </p>
        </div>
      </div>

      <div className="mt-4 space-y-4">
        <div>
          <label className="block mb-1.5 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            叙事文风 · KP 怎么写
          </label>
          <StylePicker
            kind="narrative" inheritLabel="跟随模组" disabled={saving}
            options={options?.narrative || []}
            value={narrative} onChange={setNarrative}
          />
        </div>
        <div>
          <label className="block mb-1.5 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            配图画风 · 局内插图长什么样
          </label>
          <StylePicker
            kind="image" inheritLabel="跟随模组" disabled={saving}
            options={options?.image || []}
            value={image} onChange={setImage}
          />
          <p className="mt-1.5 text-xs" style={{ color: 'var(--color-text-secondary)', opacity: 0.75 }}>
            只对<strong>之后新生成</strong>的图片生效——已经出过的场景图、立绘不会重画，
            所以中途换画风会让新旧图片风格不一致。
          </p>
        </div>
      </div>

      <div className="mt-5 flex items-center justify-end gap-2">
        <button onClick={onClose} disabled={saving} className="btn-secondary !px-3 !py-1 text-sm">
          取消
        </button>
        <button onClick={() => void save()} disabled={saving} className="btn-primary !px-3 !py-1 text-sm">
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </Modal>
  )
}
