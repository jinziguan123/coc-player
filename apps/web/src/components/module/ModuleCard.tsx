import { useNavigate } from 'react-router-dom'
import { GiArchiveResearch } from 'react-icons/gi'
import { Loader2 } from 'lucide-react'
import { getServerUrl } from '@/api/client'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { DecoSunburst } from '@/components/ui/deco'
import { TiltCard } from '@/components/ui/TiltCard'
import type { Module } from '@/stores/moduleStore'

/** 难度着色：走主题语义色，两套主题下都能读 */
const DIFFICULTY_TONE: Record<string, string> = {
  入门: 'var(--color-success)',
  普通: 'var(--color-text-secondary)',
  困难: 'var(--color-dice-gold)',
  噩梦: 'var(--color-danger)',
}

function imageUrl(src: string): string {
  return /^https?:\/\//i.test(src) ? src : `${getServerUrl()}${src}`
}

/**
 * 模组卡：做成一本书的封面。
 *
 * 封面取**开场场景**（scenes[0]）的配图——那是玩家进门第一眼就会看到的地方，不算剧透；
 * 后面的场景一概不碰。没有配图时是日芒纹 + 标题首字，书脊感靠右上的规则体系铭牌。
 *
 * 题材信息（年代 · 地点 · 人数 · 难度）排成一行文字而不是一排胶囊：七八个同权胶囊并排时
 * 眼睛无处落脚，读成一句话反而快。
 */
export function ModuleCard({
  module: m, index, onRebuildRag, onDelete,
}: {
  module: Module
  index: number
  onRebuildRag: (id: string) => void
  onDelete: (id: string) => void
}) {
  const navigate = useNavigate()
  const ws = m.world_setting ?? {}
  const cover = typeof m.scenes?.[0]?.image === 'string' ? (m.scenes[0].image as string) : ''
  const facts = [ws.era, ws.region, ws.player_count && `${String(ws.player_count)} 人`]
    .filter(Boolean)
    .map(String)
  const difficulty = ws.difficulty ? String(ws.difficulty) : ''
  const tags = Array.isArray(ws.tags) ? (ws.tags as string[]) : []

  return (
    <TiltCard index={index} className="card entity-card module-card">
      <div className={`module-cover ${cover ? '' : 'module-cover--bare'}`}>
        {cover ? (
          <img src={imageUrl(cover)} alt="" className="module-cover-img" loading="lazy" />
        ) : (
          <>
            <DecoSunburst className="module-cover-sunburst" />
            <span className="module-cover-initial" aria-hidden>{[...m.title][0]}</span>
          </>
        )}
        <div className="module-cover-scrim" aria-hidden />
        <span className="module-cover-system">{m.rule_system.toUpperCase()}</span>
        <RagBadge status={m.rag_status} />
        <h3 className="module-cover-title" title={m.title}>{m.title}</h3>
      </div>

      <div className="module-body">
        {(facts.length > 0 || difficulty) && (
          <p className="module-facts">
            {facts.join(' · ')}
            {difficulty && (
              <>
                {facts.length > 0 && ' · '}
                <span style={{ color: DIFFICULTY_TONE[difficulty] }}>{difficulty}</span>
              </>
            )}
          </p>
        )}
        <p className="module-desc">{m.description}</p>
        {tags.length > 0 && <p className="module-tags">{tags.map((t) => `#${t}`).join('  ')}</p>}

        <div className="module-stats">
          <span><b>{m.scenes?.length ?? 0}</b>场景</span>
          <span><b>{m.npcs?.length ?? 0}</b>NPC</span>
          <span><b>{m.clues?.length ?? 0}</b>线索</span>
        </div>
      </div>

      {/* 操作：静息隐形，悬停或键盘聚焦时从底边滑上来 */}
      <div className="module-actions entity-card-actions">
        {m.rag_status !== 'indexing' && (
          <button
            onClick={() => onRebuildRag(m.id)}
            className="chip chip--accent chip-btn chip-btn--accent"
            title="（重）建模组原文索引：让 KP 跑团时能检索并引用模组原文"
          >
            <GiArchiveResearch /> 重建索引
          </button>
        )}
        <ConfirmDialog
          title="查看 / 编辑模组（含剧透）"
          description={`「${m.title}」的内容包含 NPC 秘密、线索与剧情真相。若你打算亲自游玩本模组，请不要查看。确定继续吗？`}
          confirmLabel="继续查看"
          onConfirm={() => navigate(`/modules/${m.id}`)}
        >
          {(open) => (
            <button onClick={open} className="chip chip--accent chip-btn chip-btn--accent">查看/编辑</button>
          )}
        </ConfirmDialog>
        <ConfirmDialog
          title="删除模组"
          description={`确定要删除「${m.title}」吗？此操作不可恢复。`}
          confirmLabel="删除"
          onConfirm={() => onDelete(m.id)}
        >
          {(open) => (
            <button onClick={open} className="chip chip--danger chip-btn chip-btn--danger">删除</button>
          )}
        </ConfirmDialog>
      </div>
    </TiltCard>
  )
}

function RagBadge({ status }: { status?: string }) {
  if (status === 'ready') {
    return (
      <span className="module-cover-rag" title="模组原文已建索引，跑团时 KP 可引用原文">
        <GiArchiveResearch /> 原文索引
      </span>
    )
  }
  if (status === 'indexing') {
    return (
      <span className="module-cover-rag" title="正在为模组原文建索引">
        <Loader2 className="animate-spin" size={11} /> 索引中
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span className="module-cover-rag module-cover-rag--failed" title="原文索引构建失败，可点「重建索引」重试">
        <GiArchiveResearch /> 索引失败
      </span>
    )
  }
  return null
}
