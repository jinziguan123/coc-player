import { useState } from 'react'
import { UserRound } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import type { Character } from './api'

const PAGE_SIZE = 8

const ATTRIBUTE_LABELS: Record<string, string> = {
  STR: '力量',
  CON: '体质',
  SIZ: '体型',
  DEX: '敏捷',
  APP: '外貌',
  INT: '智力',
  POW: '意志',
  EDU: '教育',
}

/** 要害条：低于三成转血红，让「快没了」在列表里一眼可见。 */
function VitalBar({
  label,
  current,
  max,
  tone,
}: { label: string; current: number; max: number; tone: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (current / max) * 100)) : 0
  const low = pct < 30
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-0.5 flex items-baseline justify-between gap-1">
        <span
          className="text-[length:var(--text-2xs)] tracking-wider"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          {label}
        </span>
        <span className="font-mono text-[length:var(--text-2xs)] tabular-nums">
          {current}/{max}
        </span>
      </div>
      <div
        className="h-1 overflow-hidden rounded-full"
        style={{ background: 'var(--surface-sunken)' }}
      >
        <div
          className="stat-bar-fill h-full rounded-full"
          style={{ width: `${pct}%`, background: low ? 'var(--color-danger)' : tone }}
        />
      </div>
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
    <div>
      <div className="mb-3 flex items-center gap-2">
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setPage(1)
          }}
          placeholder="搜索角色名 / 职业 / 规则…"
          className="input w-full max-w-md"
        />
        <span
          className="whitespace-nowrap text-xs"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          {filtered.length} 个角色
        </span>
        <span aria-hidden="true" className="flex-1" />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {pageItems.length === 0 && (
          <p
            className="col-span-full py-6 text-center text-sm"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {characters.length === 0
              ? '暂无角色，点右上角「创建角色」开始'
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
          const isActive = selectedId === character.id
          return (
            <div
              key={character.id}
              className="card character-card cursor-pointer !p-0 overflow-hidden"
              style={{
                borderColor: isActive ? 'var(--color-accent)' : undefined,
                boxShadow: isActive
                  ? '0 0 0 1px var(--color-accent), 0 4px 14px var(--shadow-color-strong)'
                  : undefined,
              }}
              onClick={() => onSelect(character)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onSelect(character)
              }}
              role="button"
              tabIndex={0}
            >
              {/* 抬头：首字纹章 + 姓名 + 职业/规则，操作按钮 hover 才浮现，静息态更干净 */}
              <div
                className="flex items-start gap-2.5 px-3 pt-3 pb-2.5"
                style={{ borderBottom: '1px solid var(--color-border)' }}
              >
                <span className="char-sigil" aria-hidden="true">
                  {character.name.trim().charAt(0) || <UserRound className="h-4 w-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="card-title !mb-0.5 truncate !text-[length:var(--text-base)]">
                    {character.name}
                  </h3>
                  <div className="flex flex-wrap items-center gap-1">
                    {occupation && <span className="chip">{occupation}</span>}
                    <span className="chip chip--accent">
                      {character.rule_system.toUpperCase()}
                    </span>
                  </div>
                </div>
                <div className="character-card-actions flex flex-shrink-0 items-center gap-1">
                  <button
                    onClick={(event) => {
                      event.stopPropagation()
                      onEdit(character)
                    }}
                    className="chip chip--accent hover:!bg-[var(--color-accent)] hover:!text-[var(--color-on-accent)] transition-colors"
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
                        className="chip chip--danger hover:!bg-[var(--color-danger-deep)] hover:!text-[var(--color-on-danger)] transition-colors"
                      >
                        删除
                      </button>
                    )}
                  </ConfirmDialog>
                </div>
              </div>

              <div className="px-3 py-2.5">
                {Boolean(hitPoints.max) && (
                  <div className="mb-2.5 flex gap-3">
                    <VitalBar
                      label="HP"
                      current={hitPoints.current}
                      max={hitPoints.max}
                      tone="var(--color-danger-deep)"
                    />
                    <VitalBar
                      label="SAN"
                      current={sanity.current}
                      max={sanity.max}
                      tone="var(--color-accent)"
                    />
                  </div>
                )}
                <div className="grid grid-cols-4 gap-1">
                  {Object.entries(character.base_attributes).map(([key, value]) => (
                    <div key={key} className="stat-tile">
                      <div className="stat-tile-label">{ATTRIBUTE_LABELS[key] || key}</div>
                      <div className="stat-tile-value">{value}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-2 text-sm">
          <button
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={currentPage <= 1}
            className="btn-secondary !px-2 !py-1 disabled:opacity-40"
          >
            上一页
          </button>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            {currentPage} / {totalPages}
          </span>
          <button
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            disabled={currentPage >= totalPages}
            className="btn-secondary !px-2 !py-1 disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
