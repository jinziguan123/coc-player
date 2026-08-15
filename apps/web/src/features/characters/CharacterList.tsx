import { useState } from 'react'
import { GiScrollUnfurled } from 'react-icons/gi'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { CharacterPortrait } from '@/components/character/CharacterPortrait'
import type { Character } from './api'

const PAGE_SIZE = 8

/** 迷你要害条：窄页里只留两根细线，够看出「快没了」即可。低于三成转血红。 */
function MiniVital({ current, max, tone }: { current: number; max: number; tone: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (current / max) * 100)) : 0
  return (
    <div className="roster-vital flex-1">
      <i style={{ width: `${pct}%`, background: pct < 30 ? 'var(--color-danger)' : tone }} />
    </div>
  )
}

interface CharacterListProps {
  characters: Character[]
  selectedId: string | null
  onSelect: (character: Character) => void
  onEdit: (character: Character) => void
  onDelete: (characterId: string) => void | Promise<void>
}

/**
 * 左页名录：一人一行，按姓名/职业/规则筛。
 *
 * 这里刻意只给「认出这个人」所需的最少信息——头像、姓名、职业、要害。详细数据在右页，
 * 名录再塞属性格就成了两份角色卡对着看，翻页也就没意义了。
 */
export function CharacterList({
  characters,
  selectedId,
  onSelect,
  onEdit,
  onDelete,
}: CharacterListProps) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const normalizedQuery = query.trim().toLowerCase()
  const filtered = characters.filter((character) => {
    if (!normalizedQuery) return true
    const occupation = String(character.system_data?.occupation ?? '').toLowerCase()
    return character.name.toLowerCase().includes(normalizedQuery)
      || occupation.includes(normalizedQuery)
      || character.rule_system.toLowerCase().includes(normalizedQuery)
  })
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pageItems = filtered.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="tome-page-head">
        <GiScrollUnfurled size={13} />
        名录
        <span className="tome-page-head-count">{filtered.length} 人</span>
      </div>

      <input
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setPage(1)
        }}
        placeholder="搜索角色名 / 职业 / 规则…"
        className="input mb-2 w-full !py-1 !text-[length:var(--text-xs)]"
      />

      <div className="min-h-0 flex-1 overflow-y-auto pr-0.5">
        {pageItems.length === 0 && (
          <p
            className="py-6 text-center text-xs"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {characters.length === 0
              ? '名录还是空的，点上方「创建角色」写第一页'
              : '没有匹配的角色'}
          </p>
        )}
        {pageItems.map((character) => {
          const hitPoints = (character.system_data?.hitPoints as {
            current: number
            max: number
          }) || { current: 0, max: 0 }
          const sanity = (character.system_data?.sanity as {
            current: number
            max: number
          }) || { current: 0, max: 0 }
          const occupation = String(character.system_data?.occupation ?? '')
          const archived = character.experiences?.length ?? 0
          const isActive = selectedId === character.id
          return (
            <div
              key={character.id}
              className="character-card roster-item cursor-pointer"
              data-active={isActive}
              onClick={() => onSelect(character)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onSelect(character)
              }}
              role="button"
              tabIndex={0}
            >
              <CharacterPortrait
                name={character.name}
                avatarUrl={character.avatar_url}
                size="sm"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-1.5">
                  <h3 className="roster-name truncate">{character.name}</h3>
                  {archived > 0 && (
                    <span
                      className="flex-shrink-0"
                      style={{ fontSize: '0.6rem', color: 'var(--color-text-accent)', opacity: 0.85 }}
                      title={`已跑完 ${archived} 个模组`}
                    >
                      ×{archived}
                    </span>
                  )}
                </div>
                <div className="roster-sub truncate">
                  {occupation || character.rule_system.toUpperCase()}
                </div>
                {Boolean(hitPoints.max) && (
                  <div className="mt-1 flex items-center gap-1">
                    <MiniVital
                      current={hitPoints.current}
                      max={hitPoints.max}
                      tone="var(--color-danger-deep)"
                    />
                    <MiniVital
                      current={sanity.current}
                      max={sanity.max}
                      tone="var(--color-accent)"
                    />
                  </div>
                )}
              </div>
              {/* 操作按钮 hover 才浮现，静息态的名录才干净 */}
              <div className="character-card-actions flex flex-shrink-0 items-center gap-1">
                <button
                  onClick={(event) => {
                    event.stopPropagation()
                    onEdit(character)
                  }}
                  className="chip chip--accent chip-btn chip-btn--accent !px-1 !py-0 !text-[0.6rem]"
                >
                  编辑
                </button>
                <ConfirmDialog
                  title="删除角色"
                  description={`确定要删除「${character.name}」吗？此操作不可恢复。`}
                  confirmLabel="删除"
                  onConfirm={() => onDelete(character.id)}
                >
                  {(open) => (
                    <button
                      onClick={(event) => {
                        event.stopPropagation()
                        open()
                      }}
                      className="chip chip--danger chip-btn chip-btn--danger !px-1 !py-0 !text-[0.6rem]"
                    >
                      删除
                    </button>
                  )}
                </ConfirmDialog>
              </div>
            </div>
          )
        })}
      </div>

      {totalPages > 1 && (
        <div className="mt-2 flex flex-shrink-0 items-center justify-center gap-2 text-xs">
          <button
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={currentPage <= 1}
            className="btn-secondary !px-2 !py-0.5 !text-xs disabled:opacity-40"
          >
            上一页
          </button>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            {currentPage} / {totalPages}
          </span>
          <button
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            disabled={currentPage >= totalPages}
            className="btn-secondary !px-2 !py-0.5 !text-xs disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
