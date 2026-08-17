import { useMemo, useState, type CSSProperties } from 'react'
import { Check, Crown, PenLine, Search, Sparkles } from 'lucide-react'
import { CharacterPortrait } from '@/components/character/CharacterPortrait'
import { CharacterGuidanceCard } from '@/components/module/CharacterGuidanceCard'
import { hasGuidance, type CharacterGuidance } from '@/stores/moduleStore'
import { ageOf, occupationOf, topSkills, vitalOf } from '@/lib/characterDigest'

export interface LobbyCharacter {
  id: string
  name: string
  module_id?: string | null
  rule_system?: string
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
  avatar_url?: string | null
}

export type CharacterSource = 'mine' | 'local'

export interface CharacterPick {
  char: LobbyCharacter
  source: CharacterSource
}

interface CharacterSelectStageProps {
  mine: LobbyCharacter[]
  local: LobbyCharacter[]
  preview: CharacterPick | null
  busy: boolean
  changingChar: boolean
  currentCharName?: string | null
  moduleTitle?: string
  guidance?: CharacterGuidance | null
  allowKpClaim?: boolean
  onPick: (char: LobbyCharacter, source: CharacterSource) => void
  onGenerate: () => void
  onQuickCreate: () => void
  onClaimKp: () => void
  onCancelChange: () => void
}

/**
 * 大厅里的「选人舞台」：真人玩家挑调查员时的主视觉。
 *
 * 从前角色卡只是一小片嵌在席位卡底部的小列表——位置太低，而且视觉上像表单而不是选人。
 * 这里把「选人」单独做成守望先锋/杀戮尖塔式的界面：居中大标题、卡牌阵列、选中高亮、
 * 逐张浮入，右侧档案区负责最终确认。
 */
export function CharacterSelectStage({
  mine,
  local,
  preview,
  busy,
  changingChar,
  currentCharName,
  moduleTitle,
  guidance,
  allowKpClaim = false,
  onPick,
  onGenerate,
  onQuickCreate,
  onClaimKp,
  onCancelChange,
}: CharacterSelectStageProps) {
  const [filter, setFilter] = useState('')
  const total = mine.length + local.length

  const roster = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const matches = (c: LobbyCharacter) => {
      if (!q) return true
      return c.name.toLowerCase().includes(q) || occupationOf(c).toLowerCase().includes(q)
    }
    return [
      ...mine.filter(matches).map((char) => ({ char, source: 'mine' as const })),
      ...local.filter(matches).map((char) => ({ char, source: 'local' as const })),
    ]
  }, [mine, local, filter])

  return (
    <section className="hero-select" aria-label="选择你的调查员">
      {/* 顶部中央的舞台光：纯装饰，给选人界面一点「角色登场」的仪式感 */}
      <span className="hero-select-light" aria-hidden="true" />

      <div className="hero-select-top">
        <div className="hero-select-eyebrow">SELECT YOUR INVESTIGATOR</div>
        <h2 className="hero-select-title">{changingChar ? '更换你的调查员' : '选择你的调查员'}</h2>
        <p className="hero-select-sub">
          {changingChar
            ? `正在替换「${currentCharName || '当前角色'}」。点选新卡后，在右侧档案里确认。`
            : '点击一张角色卡检视完整档案；确认之后才会真正入座。'}
        </p>
        <div className="hero-select-top-actions">
          {changingChar && (
            <button
              type="button"
              onClick={onCancelChange}
              className="btn-secondary !px-3 !py-1 text-xs"
            >
              取消更换
            </button>
          )}
          {allowKpClaim && (
            <button
              type="button"
              onClick={onClaimKp}
              disabled={busy}
              className="btn-secondary inline-flex items-center gap-1.5 !px-3 !py-1 text-xs"
            >
              <Crown size={13} /> 以真人 KP 身份加入
            </button>
          )}
        </div>
      </div>

      {hasGuidance(guidance) && (
        <details className="hero-guidance">
          <summary>查看本模组的车卡建议</summary>
          <div className="hero-guidance-body">
            <CharacterGuidanceCard guidance={guidance!} moduleTitle={moduleTitle} />
          </div>
        </details>
      )}

      <div className="hero-select-bar">
        <span className="hero-select-count" title="角色管理显示你的全部角色；这里只显示未被其他房间或游戏占用的角色">
          {total} 张可入座 · 已排除使用中
        </span>
        {total > 6 && (
          <label className="hero-search">
            <Search size={13} aria-hidden="true" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="按姓名或职业搜索"
            />
          </label>
        )}
        <button
          type="button"
          onClick={onQuickCreate}
          disabled={busy}
          className="hero-create"
          title="快速创建一张空白角色卡，复用角色编辑表单"
        >
          <PenLine size={14} /> 快速创建
        </button>
        <button
          type="button"
          onClick={onGenerate}
          disabled={busy}
          className="hero-generate"
          title="写一句提示词，让 AI 现场生成一张调查员卡"
        >
          <Sparkles size={14} /> AI 现场生成
        </button>
      </div>

      {roster.length === 0 ? (
        <div className="hero-select-blank">
          {total === 0 ? (
            <>
              <div className="hero-select-blank-title">没有可用的角色卡</div>
              <p>还没有属于你、且未被其他游戏占用的调查员卡。可以快速创建一张空白卡，或让 AI 现场生成。</p>
              <div className="flex flex-wrap items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={onQuickCreate}
                  disabled={busy}
                  className="btn-secondary inline-flex items-center gap-1.5 !px-3 !py-1.5 text-sm"
                >
                  <PenLine size={14} /> 快速创建角色卡
                </button>
                <button
                  type="button"
                  onClick={onGenerate}
                  disabled={busy}
                  className="btn-primary inline-flex items-center gap-1.5 !px-3 !py-1.5 text-sm"
                >
                  <Sparkles size={14} /> 生成我的调查员
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="hero-select-blank-title">没有匹配的角色卡</div>
              <p>换一个姓名或职业关键词试试。</p>
            </>
          )}
        </div>
      ) : (
        <div className="hero-grid">
          {roster.map(({ char, source }, index) => {
            const on = preview?.char.id === char.id && preview.source === source
            const meta = [occupationOf(char), ageOf(char)].filter(Boolean).join(' · ')
            const hp = vitalOf(char, 'hitPoints')
            const san = vitalOf(char, 'sanity')
            const skills = topSkills(char)
            return (
              <button
                key={`${source}-${char.id}`}
                type="button"
                onClick={() => onPick(char, source)}
                disabled={busy}
                aria-pressed={on}
                className={`hero-card${on ? ' hero-card--on' : ''}`}
                style={{ '--hero-delay': `${Math.min(index, 14) * 40}ms` } as CSSProperties}
                title={source === 'local'
                  ? '本机角色。确认入座时会同步一份副本给房主，不会进入他的角色库'
                  : '点击查看这张卡，确认后再入座'}
              >
                <span className="hero-card-frame" aria-hidden="true" />
                <span className="hero-card-visual">
                  <span className="hero-card-aura" aria-hidden="true" />
                  <CharacterPortrait
                    name={char.name}
                    avatarUrl={char.avatar_url}
                    size="lg"
                    className="hero-card-portrait"
                  />
                  {source === 'local' && <span className="hero-card-badge">本机</span>}
                </span>
                <span className="hero-card-info">
                  <span className="hero-card-name">{char.name}</span>
                  {meta && <span className="hero-card-meta">{meta}</span>}
                  {(hp || san) && (
                    <span className="hero-card-vitals">
                      {hp && <span className="hero-vital hero-vital--hp">HP <b>{hp}</b></span>}
                      {san && <span className="hero-vital hero-vital--san">SAN <b>{san}</b></span>}
                    </span>
                  )}
                  {skills.length > 0 && (
                    <span className="hero-card-skills">{skills.join(' · ')}</span>
                  )}
                </span>
                {on && (
                  <span className="hero-card-picked" aria-hidden="true">
                    <Check size={15} />
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}

      <div className="hero-select-foot">
        {preview ? (
          <span className="hero-select-chosen">
            <span className="hero-select-chosen-label">已锁定</span>
            <b>{preview.char.name}</b>
            <span>· 在右侧档案里确认入座</span>
          </span>
        ) : (
          <span className="hero-select-foot-muted">尚未选择角色</span>
        )}
        <span className="hero-select-foot-muted">
          {total > 0 ? '点击卡牌只是预览，确认后才会真正入座' : '生成角色后可先过目、编辑，再决定是否入座'}
        </span>
      </div>
    </section>
  )
}
