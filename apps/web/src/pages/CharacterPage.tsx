import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { GiCharacter, GiReturnArrow, GiScrollUnfurled, GiUpCard } from 'react-icons/gi'
import { RefreshCw } from 'lucide-react'
import { syncCharactersBackFromHost } from '@/features/characters/syncBack'
import { getServerUrl } from '../api/client'
import { CharacterPanel } from '../components/character/CharacterPanel'
import { CharacterEditModal } from '../components/character/CharacterEditModal'
import { CharacterList } from '@/features/characters/CharacterList'
import { ArchiveHead } from '@/components/layout/ArchiveHead'
import { listCharacters, removeCharacter, type Character } from '@/features/characters/api'
import { CharacterSheet } from '@/features/characters/sheet/CharacterSheet'
import { DRAFT_KEY, readDraft, type SheetState } from '@/features/characters/sheet/useSheet'

/** 旧版六步向导的草稿：字段结构与一页式人物卡不同，恢复不了，见到就清掉。 */
const LEGACY_DRAFT_KEY = 'trpg_character_draft'

function formatTime(iso: string): string {
  const dt = new Date(iso)
  return `${dt.getMonth() + 1}/${dt.getDate()} ${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`
}

export function CharacterPage() {
  const [characters, setCharacters] = useState<Character[]>([])
  const [selectedChar, setSelectedChar] = useState<Character | null>(null)
  const [editingChar, setEditingChar] = useState<Character | null>(null)
  /** 建卡中：值是人物卡的初始状态（恢复草稿时带上），undefined = 新建一张空白卡 */
  const [sheet, setSheet] = useState<{ initial?: SheetState } | null>(null)
  const [draft, setDraft] = useState(() => readDraft())

  /** 把角色的部分字段合并进列表与选中项（不影响弹窗开关）。 */
  const syncChar = useCallback((updated: Partial<Character> & { id: string }) => {
    setCharacters((prev) => prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)))
    setSelectedChar((prev) => (prev && prev.id === updated.id ? { ...prev, ...updated } : prev))
  }, [])

  const loadCharacters = useCallback(async () => {
    setCharacters(await listCharacters())
  }, [])

  useEffect(() => {
    void loadCharacters()
    try { localStorage.removeItem(LEGACY_DRAFT_KEY) } catch { /* 隐私模式 */ }
  }, [loadCharacters])

  // 手动把参战结果取回本机（兜底；正常进出房间会自动同步，见 useSyncBack）。
  const connectedToHost = !!getServerUrl()
  const [pulling, setPulling] = useState(false)
  const pullBackFromHost = async () => {
    setPulling(true)
    try {
      const { synced, failed } = await syncCharactersBackFromHost()
      if (synced.length) {
        toast.success(`已取回本局结果：${synced.join('、')}`)
        await loadCharacters()
      } else if (failed) {
        toast.error('取回失败，请确认与房主的连接是否正常')
      } else {
        toast.success('没有需要同步的内容')
      }
    } finally {
      setPulling(false)
    }
  }

  const deleteCharacter = async (id: string) => {
    try {
      await removeCharacter(id)
      if (selectedChar?.id === id) setSelectedChar(null)
      await loadCharacters()
      toast.success('角色已删除')
    } catch (e) {
      toast.error(`删除失败：${e instanceof Error ? e.message : '未知原因'}`)
    }
  }

  const closeSheet = () => { setSheet(null); setDraft(readDraft()) }

  /** 手机上名录在上、角色卡在下：选中后把角色卡滚进视野，省得用户自己找。 */
  const selectCharacter = (character: Character) => {
    const deselect = selectedChar?.id === character.id
    setSelectedChar(deselect ? null : character)
    if (deselect) return
    // 手机（<=767）与平板兜底单列（<=1023）都需要把角色卡滚进视野
    const narrow = typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(max-width: 1023px)').matches
    if (!narrow) return
    window.setTimeout(() => {
      document.getElementById('character-sheet-panel')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    }, 60)
  }

  if (sheet) {
    return (
      <div className="character-sheet-page flex h-full min-h-0 flex-col">
        <ArchiveHead
          title="新建调查员"
          actions={(
            <button onClick={closeSheet} className="btn-secondary btn-sm flex items-center gap-1">
              <GiReturnArrow /> 返回列表
            </button>
          )}
        />
        <CharacterSheet initial={sheet.initial} onCancel={closeSheet}
          onCreated={() => { setSheet(null); setDraft(null); void loadCharacters() }} />
      </div>
    )
  }

  return (
    <div className="characters-page flex h-full min-h-0 flex-col pb-4">
      <ArchiveHead
        title="角色"
        stats={[{ label: '调查员', value: characters.length }]}
        actions={(
          <>
            {/* 兜底：正常情况下进出房间会自动同步，这里给一个手动入口，
                应对异常退出（关窗口/掉线/房主先退）后想立刻把结果取回来。 */}
            {connectedToHost && (
              <button onClick={() => void pullBackFromHost()} disabled={pulling} className="btn-secondary btn-sm flex items-center gap-1"
                title="把本局的 HP、理智、成长与物品变化写回你自己的角色卡">
                <RefreshCw size={13} /> {pulling ? '同步中…' : '从房主处取回本局结果'}
              </button>
            )}
            <button onClick={() => setSheet({})} className="btn-primary btn-sm flex items-center gap-1">
              <GiUpCard /> 创建角色
            </button>
          </>
        )}
      />

      {/* 未完成草稿：非模态横幅，可恢复或丢弃 */}
      {draft && (
        <div className="card mb-6 flex flex-wrap items-center gap-3" style={{ borderColor: 'var(--color-accent)', background: 'var(--color-bg-card)' }}>
          <GiScrollUnfurled className="text-lg flex-shrink-0" style={{ color: 'var(--color-text-accent)' }} />
          <span className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            有一张没建完的人物卡{draft.state.name ? `「${draft.state.name}」` : ''}（{formatTime(draft.savedAt)} 自动保存），是否继续？
          </span>
          <div className="flex items-center gap-2 ml-auto">
            <button onClick={() => setSheet({ initial: draft.state })} className="btn-primary !px-3 !py-1 text-sm">继续</button>
            <button onClick={() => { localStorage.removeItem(DRAFT_KEY); setDraft(null) }} className="btn-secondary !px-3 !py-1 text-sm">丢弃</button>
          </div>
        </div>
      )}

      {/* 默认视图：一本摊开的书——左页名录、右页详情，中间一道书脊 */}
      <div className="tome character-tome min-h-0 flex-1">
        <div className="tome-page tome-page--left">
          <CharacterList
            characters={characters}
            selectedId={selectedChar?.id ?? null}
            onSelect={selectCharacter}
            onEdit={setEditingChar}
            onDelete={deleteCharacter}
          />
        </div>
        <div className="tome-page tome-page--right" id="character-sheet-panel">
          {selectedChar ? (
            <CharacterPanel key={selectedChar.id} character={selectedChar} />
          ) : (
            /* 书摊开着，只是还没翻到某个人 */
            <div className="tome-empty">
              <GiCharacter size={30} style={{ opacity: 0.5 }} />
              <p className="text-xs">从左页名录里挑一个人</p>
            </div>
          )}
        </div>
      </div>

      {editingChar && (
        <CharacterEditModal
          character={editingChar}
          open={!!editingChar}
          onOpenChange={(v) => { if (!v) setEditingChar(null) }}
          onSaved={(updated) => { syncChar(updated); setEditingChar(null) }}
          onPatched={syncChar}
        />
      )}
    </div>
  )
}
