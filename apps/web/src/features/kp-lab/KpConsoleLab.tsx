/**
 * 真人 KP 值班台 —— 设计原型。
 *
 * 这一页**只读**：所有按钮都不会真的发出去，跑着的团不受影响。它的用途是把
 * 「监控视角」的布局放在真实数据上看一眼，定稿之后再接进 GameSessionPage。
 *
 * 与现状的结构性差别，一句话一条：
 *  - 态势常驻：全队 HP/SAN/MP 在左列不走，不再散在角色卡、队伍条、大地图三处；
 *  - 动作平铺：12 个动作全部可见，没有「更多…」下拉；直接打字就是写旁白；
 *  - 战斗独占台面：开战时主台整个换成战术台，不再和叙事流抢竖向空间；
 *  - 干预贴着状态：要对谁做什么，手就落在他那张卡上，省掉「先选人」这一步。
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { GiCrossedSwords, GiQuillInk, GiReturnArrow, GiScrollUnfurled } from 'react-icons/gi'
import { Eye } from 'lucide-react'

import { api } from '@/api/client'
import { stripCommandTags } from '@/features/game-session/derive'

import { CommandDeck, type DeckAction } from './CommandDeck'
import { loadFigures, saveFigure, type FigureKey } from './figures'
import { InvestigatorMonitor, type ActorAction } from './InvestigatorMonitor'
import { SceneBrief } from './SceneBrief'
import { TacticalStage } from './TacticalStage'
import { useLabData, type LabActor, type LabEvent } from './useLabData'
import './lab.css'

/** 原型页不发任何写请求。点到哪一步都只回一句，免得误以为真的发出去了。 */
function inert(what: string) {
  toast(`原型页：「${what}」不会真的发出去`, { description: '定稿后再接进正式控制台' })
}

const ACT_PREFILL: Record<ActorAction['key'], { action: DeckAction; draft: (n: string) => string }> = {
  check: { action: 'dice_check', draft: (n) => `${n} ` },
  san: { action: 'san_check', draft: (n) => `${n} 1/1d6` },
  damage: { action: 'hp_change', draft: (n) => `${n} -` },
  move: { action: 'move_actor', draft: (n) => `${n} → ` },
}

const FEED_KIND: Record<string, string> = {
  dice: 'dice', system: 'system', dialogue: 'say', say: 'say',
}

/** 叙事流只留「刚发生了什么」：KP 回看时要的是线索，不是重读一遍全文。 */
function feedRows(events: LabEvent[]): LabEvent[] {
  return events.filter((e) => e.content?.trim()).slice(-40)
}

export function KpConsoleLab() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const { session, actors, events, combat, turn, places, sceneName, loading, error } = useLabData(sessionId)

  const [mode, setMode] = useState<'narrative' | 'tactical'>('narrative')
  const [action, setAction] = useState<DeckAction>('narration')
  const [draft, setDraft] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [playerView, setPlayerView] = useState(false)
  // KP 给各参战方挑的战棋轮廓。原型期落 localStorage，见 figures.ts 的说明。
  const [figures, setFigures] = useState<Record<string, FigureKey>>({})
  useEffect(() => { setFigures(sessionId ? loadFigures(sessionId) : {}) }, [sessionId])
  const pickFigure = (id: string, key: FigureKey) => {
    setFigures((m) => ({ ...m, [id]: key }))
    if (sessionId) saveFigure(sessionId, id, key)
  }

  // 开战即切战术台：KP 不该在打起来之后还要自己去按一下切换。
  useEffect(() => { if (combat) setMode('tactical') }, [combat])

  const rows = useMemo(() => feedRows(events), [events])
  // 最近的掷骰结算，新的在前。战术台底下那条回放用它。
  const rolls = useMemo(
    () => events.filter((e) => e.type === 'dice' && e.content?.trim())
      .slice(-4).reverse().map((e) => e.content.trim()),
    [events],
  )

  const onActorAct = (a: LabActor, act: ActorAction) => {
    const pre = ACT_PREFILL[act.key]
    setAction(pre.action)
    setDraft(pre.draft(a.name))
  }

  if (!sessionId) return <SessionPicker />
  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm"
      style={{ color: 'var(--color-text-secondary)' }}>正在接入值班台…</div>
  }
  if (error || !session) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm">
        <span style={{ color: 'var(--color-danger)' }}>{error || '这一局读不到'}</span>
        <button className="btn-secondary" onClick={() => navigate('/lab/kp')}>换一局</button>
      </div>
    )
  }

  const pendingCount = turn ? Math.max(0, turn.total - turn.confirmed_ids.length) : 0

  return (
    <div className="kl-root">
      {/* ---- 值班条 ---- */}
      <div className="kl-rail-top">
        <button className="btn-secondary !px-2 !py-1" onClick={() => navigate('/lab/kp')} title="换一局">
          <GiReturnArrow size={14} aria-hidden="true" />
        </button>
        <span className="kl-title">{session.module_title || '未命名模组'}</span>
        <span className="kl-locus">
          场景 <b>{sceneName || '—'}</b>
          {combat && <> · 战斗 第 <b>{combat.round}</b> 轮</>}
        </span>

        <span className="ml-auto flex items-center gap-2">
          <span className="kl-modes">
            <button
              className={`kl-mode ${mode === 'narrative' ? 'kl-mode--on' : ''}`}
              onClick={() => setMode('narrative')}
            >
              <GiQuillInk size={13} aria-hidden="true" />叙事
            </button>
            <button
              className={`kl-mode ${mode === 'tactical' ? 'kl-mode--on' : ''}`}
              onClick={() => setMode('tactical')}
              disabled={!combat}
              style={!combat ? { opacity: 0.4 } : undefined}
            >
              <GiCrossedSwords size={13} aria-hidden="true" />战术
            </button>
          </span>
          <button
            className={`kl-chip ${playerView ? 'kl-chip--on' : ''}`}
            onClick={() => setPlayerView((v) => !v)}
          >
            <Eye size={12} aria-hidden="true" />玩家视角
          </button>
          <button className="kl-chip" onClick={() => inert('打开模组资料')}>
            <GiScrollUnfurled size={12} aria-hidden="true" />资料
          </button>
          <span className="kl-proto-badge">原型</span>
        </span>
      </div>

      {/* ---- 监控列 + 主台 ---- */}
      <div className="kl-body">
        <aside className="kl-rail-left">
          <div>
            <div className="kl-section-label mb-1.5">调查员</div>
            <InvestigatorMonitor actors={actors} onAct={onActorAct} />
          </div>

          <div>
            <div className="kl-section-label mb-1.5">信号</div>
            <div className="flex flex-col gap-1">
              <div className={`kl-signal ${pendingCount ? 'kl-signal--alert' : ''}`}>
                <span className="kl-dot" />
                {pendingCount ? `${pendingCount} 人还没交这一回合` : '本回合没有待办'}
              </div>
              <div className={`kl-signal ${combat ? 'kl-signal--alert' : ''}`}>
                <span className="kl-dot" />
                {combat ? `战斗中 · 轮到 ${combat.order.find((c) => c.id === combat.turn)?.name || '—'}` : '未开战'}
              </div>
              {actors.some((a) => a.san && a.san.max > 0 && a.san.current / a.san.max < 0.3) && (
                <div className="kl-signal kl-signal--alert">
                  <span className="kl-dot" />有人理智濒临崩溃
                </div>
              )}
            </div>
          </div>
        </aside>

        <main className="kl-main">
          <div className="kl-stage">
            <div className="kl-grid-bg" />
            {mode === 'tactical' && combat ? (
              <TacticalStage
                combat={combat}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onAct={(label, who) => inert(`${who} · ${label}`)}
                rolls={rolls}
                sessionId={sessionId}
                sceneName={sceneName}
                moduleTitle={session.module_title || ''}
                figures={figures}
                onFigureChange={pickFigure}
              />
            ) : (
              <div className="kl-narrative">
                <div className="kl-feed">
                  {rows.length === 0 && (
                    <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>这一局还没有记录。</p>
                  )}
                  {rows.map((e) => (
                    <div key={e.id} className={`kl-feed-row kl-feed-row--${FEED_KIND[e.type] || 'narration'}`}>
                      <span className="kl-feed-who">{e.actor_name || 'KP'}</span>
                      <span className="kl-feed-text">
                        {playerView ? stripCommandTags(e.content) : e.content}
                      </span>
                    </div>
                  ))}
                </div>
                <SceneBrief
                  places={places}
                  onUnlock={(p) => { setAction('unlock_place'); setDraft(p.name) }}
                  onReveal={(clue) => { setAction('narration'); setDraft(`${clue}——`) }}
                />
              </div>
            )}
          </div>

          <CommandDeck
            action={action}
            onActionChange={setAction}
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={() => inert(draft.trim() ? draft.trim().slice(0, 18) : '空内容')}
          />
        </main>
      </div>
    </div>
  )
}

/** 没带存档号时先挑一局。只列真人 KP 的局——这个台面是给真人 KP 用的。 */
function SessionPicker() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<{ id: string; module_title?: string; kp_mode?: string }[]>([])
  useEffect(() => {
    let stale = false
    api.get<{ id: string; module_title?: string; kp_mode?: string }[]>('/sessions')
      .then((r) => { if (!stale) setRows(r.filter((s) => s.kp_mode === 'human')) })
      .catch(() => {})
    return () => { stale = true }
  }, [])
  return (
    <div className="mx-auto max-w-lg p-6">
      <h1 className="mb-1 text-lg" style={{ fontFamily: 'var(--font-display)' }}>KP 值班台 · 原型</h1>
      <p className="mb-4 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
        挑一局真人 KP 的存档看效果。这一页只读，不会改动正在跑的团。
      </p>
      {rows.length === 0 && <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>没有真人 KP 的存档。</p>}
      <div className="flex flex-col gap-1.5">
        {rows.map((s) => (
          <button key={s.id} className="card w-full text-left" onClick={() => navigate(`/lab/kp/${s.id}`)}>
            {s.module_title || '未命名模组'}
          </button>
        ))}
      </div>
    </div>
  )
}
