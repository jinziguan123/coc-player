import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { GiPerspectiveDiceSixFacesRandom, GiTrashCan } from 'react-icons/gi'
import { Upload } from 'lucide-react'
import { CharacterPortrait } from './CharacterPortrait'
import {
  clearCharacterAvatar,
  generateCharacterAvatar,
  uploadCharacterAvatar,
} from '@/features/characters/api'

/**
 * 角色头像编辑：上传 / AI 生成 / 摘掉。
 *
 * 生成是**手动**的：建卡途中角色描述往往还没定，那时出的图既费钱又不像；等这张卡成型了
 * 玩家自己点，命中率高得多。没配生图模型的人走上传那条路，一样能有头像。
 *
 * 三个动作都直接落库并把最新角色回传给父组件——头像不进「保存」那一批（它是独立资源，
 * 上传完就该看见，不该因为用户最后点了取消而丢失）。
 */
export function AvatarEditor({
  characterId,
  name,
  avatarUrl,
  onChange,
}: {
  characterId: string
  name: string
  avatarUrl?: string | null
  onChange: (avatarUrl: string | null) => void
}) {
  const [busy, setBusy] = useState<'' | 'upload' | 'generate' | 'clear'>('')
  const fileRef = useRef<HTMLInputElement>(null)

  async function run(kind: 'upload' | 'generate' | 'clear', fn: () => Promise<{ avatar_url?: string | null }>) {
    setBusy(kind)
    try {
      const updated = await fn()
      onChange(updated.avatar_url ?? null)
      toast.success({ upload: '头像已更新', generate: '头像已生成', clear: '已摘掉头像' }[kind])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="flex items-center gap-3">
      <CharacterPortrait name={name} avatarUrl={avatarUrl} size="lg" />
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            className="chip chip--accent inline-flex items-center gap-1"
            disabled={!!busy}
            onClick={() => fileRef.current?.click()}
          >
            <Upload className="h-3 w-3" />
            {busy === 'upload' ? '上传中…' : '上传'}
          </button>
          <button
            type="button"
            className="chip chip--accent inline-flex items-center gap-1"
            disabled={!!busy}
            onClick={() => run('generate', () => generateCharacterAvatar(characterId))}
          >
            <GiPerspectiveDiceSixFacesRandom size={12} />
            {busy === 'generate' ? '生成中…' : 'AI 生成'}
          </button>
          {avatarUrl && (
            <button
              type="button"
              className="chip inline-flex items-center gap-1"
              disabled={!!busy}
              onClick={() => run('clear', () => clearCharacterAvatar(characterId))}
            >
              <GiTrashCan size={12} />
              摘掉
            </button>
          )}
        </div>
        <p style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}>
          不设头像也没关系，卡上会用姓名首字的纹章。
        </p>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''            // 同一张图连选两次也要能触发
          if (file) void run('upload', () => uploadCharacterAvatar(characterId, file))
        }}
      />
    </div>
  )
}
