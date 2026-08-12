import { Search } from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { MODULE_DIFFICULTIES } from '@/lib/module'
import type { GameSetupState } from './useGameSetup'

export function NewGamePanel({ setup }: { setup: GameSetupState }) {
  const {
    modules,
    filteredModules,
    filters,
    filterOptions,
    hasFilter,
    setFilter,
    resetFilters,
    moduleId,
    kpMode,
    setKpMode,
    error,
    onSelectModule,
    startGame,
  } = setup

  return (
    <div className="card mb-6">
      <h3 className="card-title">新游戏</h3>

      <div className="mb-3 space-y-2">
        <div className="relative">
          <Search
            size={14}
            className="absolute left-2 top-1/2 -translate-y-1/2"
            style={{ color: 'var(--color-text-secondary)' }}
          />
          <input
            value={filters.query}
            onChange={(event) => setFilter('query', event.target.value)}
            placeholder="搜索模组名、简介、标签、地区…"
            className="input w-full !pl-7"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div
            className="flex items-center gap-1"
            title="按玩家人数上下限筛选：保留推荐人数区间与该范围有交集的模组"
          >
            <input
              type="number"
              min={1}
              value={filters.playerMin}
              onChange={(event) => setFilter('playerMin', event.target.value)}
              placeholder="人数≥"
              className="input !w-20"
            />
            <span style={{ color: 'var(--color-text-secondary)' }}>–</span>
            <input
              type="number"
              min={1}
              value={filters.playerMax}
              onChange={(event) => setFilter('playerMax', event.target.value)}
              placeholder="人数≤"
              className="input !w-20"
            />
          </div>
          <Select
            value={filters.era || '__all'}
            onValueChange={(value) => setFilter('era', value === '__all' ? '' : value)}
          >
            <SelectTrigger className="!w-28"><SelectValue placeholder="年代" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__all">年代 · 全部</SelectItem>
              {filterOptions.eras.map((era) => (
                <SelectItem key={era} value={era}>{era}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={filters.difficulty || '__all'}
            onValueChange={(value) => setFilter(
              'difficulty',
              value === '__all' ? '' : value,
            )}
          >
            <SelectTrigger className="!w-28"><SelectValue placeholder="难度" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__all">难度 · 全部</SelectItem>
              {MODULE_DIFFICULTIES.map((difficulty) => (
                <SelectItem key={difficulty} value={difficulty}>{difficulty}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={filters.region || '__all'}
            onValueChange={(value) => setFilter('region', value === '__all' ? '' : value)}
          >
            <SelectTrigger className="!w-28"><SelectValue placeholder="地区" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__all">地区 · 全部</SelectItem>
              {filterOptions.regions.map((region) => (
                <SelectItem key={region} value={region}>{region}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {hasFilter && (
            <button onClick={resetFilters} className="btn-secondary !px-2 !py-1 text-xs">
              清除筛选
            </button>
          )}
          <span className="ml-auto text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            {filteredModules.length} / {modules.length} 个模组
          </span>
        </div>
      </div>

      <Select value={moduleId} onValueChange={onSelectModule}>
        <SelectTrigger className="mb-3 w-full">
          <SelectValue placeholder="— 选择模组 —" />
        </SelectTrigger>
        <SelectContent>
          {filteredModules.length === 0 ? (
            <div
              className="px-2 py-3 text-center text-sm"
              style={{ color: 'var(--color-text-secondary)' }}
            >
              无匹配模组
            </div>
          ) : filteredModules.map((module) => {
            const world = module.world_setting ?? {}
            const meta = [world.era, world.region, world.difficulty]
              .map((value) => String(value ?? ''))
              .filter(Boolean)
              .join(' · ')
            return (
              <SelectItem key={module.id} value={module.id}>
                {module.title}
                {meta ? (
                  <span style={{ color: 'var(--color-text-secondary)' }}>（{meta}）</span>
                ) : null}
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>

      {moduleId && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">KP 模式</span>
            <button
              type="button"
              onClick={() => setKpMode('ai')}
              className="btn-secondary !px-2.5 !py-1 text-xs"
              style={kpMode === 'ai' ? { borderColor: 'var(--color-accent)', color: 'var(--color-text-accent)' } : undefined}
            >
              AI KP
            </button>
            <button
              type="button"
              onClick={() => setKpMode('human')}
              className="btn-secondary !px-2.5 !py-1 text-xs"
              style={kpMode === 'human' ? { borderColor: 'var(--color-accent)', color: 'var(--color-text-accent)' } : undefined}
            >
              真人 KP
            </button>
            <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              {kpMode === 'human' ? '创建者只占 KP 席；玩家席等待其他真人用房间码加入。' : '由 AI 自动主持剧情。'}
            </span>
          </div>
          {/* 人数、谁坐哪、AI 队友用哪张卡——全部挪进大厅了。
              「模组是房间的身份（建房时定），座位是房间的内容（房间里配）」：
              这一屏只回答「跑哪个本子、谁当 KP」，其余进房间再说。 */}
          {error && (
            <p className="mb-2 text-sm" style={{ color: 'var(--color-danger)' }}>{error}</p>
          )}
          {/* 这里创建的是**房间**，不是直接开局——按钮得说它真正做的事。开局在大厅里，
              那也是换角色、放真人空席、发邀请码的地方。 */}
          <button
            onClick={() => void startGame()}
            className="btn-primary"
          >
            创建房间
          </button>
          <p className="mt-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            下一步进入房间：在那里定人数、选角色、把席位留给真人，确认后由你开局。
          </p>
        </>
      )}
    </div>
  )
}
