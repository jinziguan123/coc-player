import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { GiCrossedSwords, GiHourglass, GiRollingDices, GiScrollUnfurled } from 'react-icons/gi'

import { api } from '@/api/client'
import type { PartyVital } from '@/features/game-session/usePartyVitals'
import { useKpPrefill } from '@/stores/kpPrefill'
import type { ChatMessage } from '@/stores/sessionStore'

import type { CombatState } from './combat/types'
import {
  buildKpLedgerEntries,
  entryMatchesFilter,
  formatLedgerTime,
  kpLedgerCounts,
  kpLedgerRole,
  type KpLedgerEntry,
  type KpLedgerFilter,
} from './kpLedgerModel'
import { useKpDesk, type KpDeskCheckRequest, type KpDeskPendingCheck } from './useKpDesk'

interface KpCombatLedgerProps {
  sessionId: string
  messages: ChatMessage[]
  combat: CombatState
  combatLogSince: number | null
  party: PartyVital[]
  /** 在账页里点「发检定」时，把目标角色与缘由交给写作台 */
  onCheck: (actorName: string, content: string) => void
  /** 点「回复」时预填底条的场外回复 */
  onReply: (actorName: string) => void
  /** 「填入检定」需要展开完整写作台 */
  onOpenWriter: () => void
  /** 待办操作完成后让页面重拉会话/体征 */
  onChanged: () => void
  /** 左缘先攻梯点人时递进来的定位请求 */
  locate?: { name: string; nonce: number } | null
}

const FILTERS: Array<{ id: KpLedgerFilter; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'todo', label: '待办' },
  { id: 'player', label: '玩家' },
  { id: 'ooc', label: '场外' },
  { id: 'roll', label: '掷骰' },
]

const DIFFICULTY_LABEL: Record<string, string> = { normal: '普通', hard: '困难', extreme: '极难' }
const NO_CHECKS: KpDeskPendingCheck[] = []
const NO_REQUESTS: KpDeskCheckRequest[] = []

function checkSkillLabel(skill: string, kind: string, difficulty: string): string {
  const display = kind === 'san_check' ? '理智' : skill
  return display + '（' + (DIFFICULTY_LABEL[difficulty] || difficulty || '普通') + '）'
}

/**
 * 观局台右页：桌边记录。
 *
 * 战斗里的玩家发言、场外、掷骰与战斗机械结算本来散在三处（聊天流 / 战斗日志抽屉 /
 * 写作台待办）。这一列把它们折进同一条时间轴，并且把「现在要处理的检定」固定在最上面。
 * 只负责读与发起（代投 / 撤销 / 填入检定 / 预填场外回复），不改 KP 的裁定权。
 */
export function KpCombatLedger({
  sessionId, messages, combat, combatLogSince, party,
  onCheck, onReply, onOpenWriter, onChanged, locate,
}: KpCombatLedgerProps) {
  const [filter, setFilter] = useState<KpLedgerFilter>('all')
  const [reloadTick, setReloadTick] = useState(0)
  const [busy, setBusy] = useState('')
  const [newCount, setNewCount] = useState(0)
  const roomTick = useKpPrefill((state) => state.deskTick)
  const desk = useKpDesk(sessionId, reloadTick + roomTick + messages.length)

  const entries = useMemo(
    () => buildKpLedgerEntries(messages, combat, combatLogSince),
    [messages, combat, combatLogSince],
  )
  const pendingChecks = desk?.pending_checks ?? NO_CHECKS
  const checkRequests = desk?.check_requests ?? NO_REQUESTS
  const pendingIds = useMemo(() => new Set(pendingChecks.map((item) => item.id)), [pendingChecks])
  const checkById = useMemo(() => new Map(pendingChecks.map((item) => [item.id, item])), [pendingChecks])
  const todoCount = pendingChecks.length + checkRequests.length
  const counts = useMemo(() => kpLedgerCounts(entries, pendingIds, todoCount), [entries, pendingIds, todoCount])
  const filtered = useMemo(
    () => entries.filter((entry) => entryMatchesFilter(entry, filter, pendingIds)),
    [entries, filter, pendingIds],
  )

  const scrollRef = useRef<HTMLDivElement>(null)
  const prevLen = useRef(0)

  const scrollToLatest = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    setNewCount(0)
  }

  // 新条目：贴底就跟随，翻上去读旧记录时只亮一枚「回到最新」。
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 90
    if (prevLen.current === 0 || nearBottom) {
      const timer = window.setTimeout(() => { el.scrollTop = el.scrollHeight }, 0)
      setNewCount(0)
      prevLen.current = filtered.length
      return () => window.clearTimeout(timer)
    }
    if (filtered.length > prevLen.current) {
      setNewCount((count) => count + filtered.length - prevLen.current)
    }
    prevLen.current = filtered.length
  }, [filtered.length])

  useEffect(() => {
    prevLen.current = 0
    scrollToLatest()
  }, [filter])

  // 左缘点人：滚到该角色最近一条账页记录；当前筛选里没有就切回全部再找。
  useEffect(() => {
    if (!locate) return
    const scrollToActor = (): boolean => {
      const el = scrollRef.current
      if (!el) return false
      const nodes = Array.from(el.querySelectorAll('[data-ledger-actor]'))
      const target = nodes.reverse().find((node) => node.getAttribute('data-ledger-actor') === locate.name)
      if (!target) return false
      target.scrollIntoView({ block: 'center', behavior: 'smooth' })
      return true
    }
    if (scrollToActor()) return
    setFilter('all')
    const timer = window.setTimeout(scrollToActor, 80)
    return () => window.clearTimeout(timer)
  }, [locate])

  const refreshDesk = () => {
    setReloadTick((tick) => tick + 1)
    useKpPrefill.getState().bumpDesk()
    onChanged()
  }

  const proxyRoll = async (checkId: string) => {
    if (busy) return
    setBusy('proxy:' + checkId)
    try {
      await api.post('/sessions/' + sessionId + '/kp/checks/' + checkId + '/roll')
      toast.success('已代投')
      refreshDesk()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '代投失败')
    } finally {
      setBusy('')
    }
  }

  const cancelCheck = async (checkId: string) => {
    if (busy) return
    setBusy('cancel:' + checkId)
    try {
      await api.post('/sessions/' + sessionId + '/kp/action', {
        action: 'cancel_check', payload: { check_id: checkId },
      })
      toast.success('已撤销这一掷')
      refreshDesk()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '撤销失败')
    } finally {
      setBusy('')
    }
  }

  const dismissRequest = async (requestId: string) => {
    if (busy) return
    setBusy('dismiss:' + requestId)
    try {
      await api.post('/sessions/' + sessionId + '/kp/action', {
        action: 'dismiss_request', payload: { request_id: requestId },
      })
      toast.success('已忽略这条申请')
      refreshDesk()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '忽略失败')
    } finally {
      setBusy('')
    }
  }

  const adoptRequest = (request: { id: string; actor_id: string; actor_name: string; skill: string; intent: string }) => {
    const fields: Record<string, string> = {
      skill: request.skill,
      reason: request.intent,
      request_id: request.id,
    }
    if (request.actor_id) fields.char = 'character:' + request.actor_id
    useKpPrefill.getState().ask('dice_check', fields)
    onOpenWriter()
    toast.success('已把「' + request.actor_name + '」的检定填进写作台，确认后发布')
  }

  const stampFor = (entry: KpLedgerEntry): { label: string; cls: string } | null => {
    if (entry.pendingCheckId) {
      return pendingIds.has(entry.pendingCheckId)
        ? { label: '待掷骰', cls: 'kp-ledger-stamp--hot' }
        : { label: '已处理', cls: 'kp-ledger-stamp--muted' }
    }
    if (entry.kind === 'ooc') return { label: '场外', cls: 'kp-ledger-stamp--muted' }
    if (entry.kind === 'dice') {
      if (entry.opposedResult) return { label: entry.opposedResult, cls: 'kp-ledger-stamp--roll' }
      if (entry.hit === true) return { label: '命中', cls: 'kp-ledger-stamp--roll' }
      if (entry.hit === false) return { label: '未命中', cls: 'kp-ledger-stamp--muted' }
      return { label: '掷骰', cls: 'kp-ledger-stamp--roll' }
    }
    if (entry.mechanical) return { label: '结算', cls: 'kp-ledger-stamp--muted' }
    return null
  }

  const renderEntry = (entry: KpLedgerEntry) => {
    const pendingCheck = entry.pendingCheckId ? checkById.get(entry.pendingCheckId) : undefined
    const checkActor = pendingCheck
      ? combat.order.find((actor) => actor.name === pendingCheck.actor_name)
      : null
    const displayActor = pendingCheck?.actor_name || entry.actorName || (entry.kind === 'system' ? '系统' : '')
    const displayNo = entry.actorNo ?? (checkActor ? combat.order.indexOf(checkActor) + 1 : null)
    const role = pendingCheck ? '检定请求' : kpLedgerRole(entry, party)
    const stamp = stampFor(entry)
    const canCheck = entry.isPlayer && (entry.kind === 'action' || entry.kind === 'dialogue')
    const canReply = entry.isPlayer && (entry.kind === 'action' || entry.kind === 'dialogue' || entry.kind === 'ooc')
    const badgeText = displayNo != null
      ? String(displayNo)
      : entry.kind === 'narration'
        ? 'KP'
        : ''
    return (
      <article
        key={entry.id}
        className={
          'kp-ledger-entry kp-ledger-entry--' + entry.kind
          + (entry.pendingCheckId ? ' is-check' : '')
          + (entry.pendingCheckId && pendingIds.has(entry.pendingCheckId) ? ' is-pending' : '')
        }
        data-ledger-actor={displayActor}
      >
        <span className={'kp-ledger-badge kp-ledger-badge--' + (entry.actor?.side || (entry.kind === 'dice' || entry.kind === 'system' ? 'roll' : 'kp'))}>
          {badgeText || (entry.kind === 'dice' || entry.kind === 'system' ? <GiRollingDices size={11} aria-hidden /> : '')}
        </span>
        <div className="kp-ledger-entry-body">
          <header className="kp-ledger-meta">
            <span className="kp-ledger-who">{displayActor}</span>
            {role && <span className="kp-ledger-role">{role}</span>}
            {formatLedgerTime(entry.ts) && <time className="kp-ledger-time">{formatLedgerTime(entry.ts)}</time>}
            {stamp && <span className={'kp-ledger-stamp ' + stamp.cls}>{stamp.label}</span>}
          </header>

          {entry.kind === 'narration' && <p className="kp-ledger-prose">{entry.content}</p>}
          {entry.kind === 'dialogue' && <p className="kp-ledger-speech">{entry.content}</p>}
          {entry.kind === 'action' && <p className="kp-ledger-action">{entry.content}</p>}
          {entry.kind === 'ooc' && <p className="kp-ledger-ooc">{entry.content}</p>}
          {(entry.kind === 'dice' || entry.kind === 'system') && (
            <div className={'kp-ledger-card ' + (entry.mechanical ? 'kp-ledger-card--mech' : '')}>
              <div className="kp-ledger-card-head">
                <GiScrollUnfurled size={11} aria-hidden />
                <span>{entry.kind === 'dice' ? '骰果' : entry.pendingCheckId ? '检定请求' : '桌面结算'}</span>
                {entry.opposedResult && <b>{entry.opposedResult}</b>}
              </div>
              <p className="kp-ledger-card-body">{entry.content}</p>
              {pendingCheck && (
                <p className="kp-ledger-card-note">
                  等 {pendingCheck.actor_name} 投 {checkSkillLabel(pendingCheck.skill, pendingCheck.kind, pendingCheck.difficulty)}
                  {pendingCheck.reason ? ' · ' + pendingCheck.reason : ''}
                </p>
              )}
            </div>
          )}

          {(canCheck || canReply) && (
            <div className="kp-ledger-acts">
              {canCheck && (
                <button type="button" className="kp-ledger-act kp-ledger-act--primary" onClick={() => onCheck(entry.actorName, entry.content)}>
                  发检定
                </button>
              )}
              {canReply && (
                <button type="button" className="kp-ledger-act" onClick={() => onReply(entry.actorName)}>
                  回复
                </button>
              )}
            </div>
          )}
        </div>
      </article>
    )
  }

  return (
    <aside className="kp-ledger" aria-label="桌边记录">
      <header className="kp-ledger-head">
        <div className="kp-ledger-head-main">
          <div className="kp-ledger-title">桌边记录</div>
          <div className="kp-ledger-sub">发言、场外、掷骰与结算，同一条时间轴</div>
          <div className="kp-ledger-live"><span className="kp-ledger-live-dot" />战斗记录实时汇入</div>
        </div>
        <div className="kp-ledger-tally">
          <span><b>{entries.length}</b> 条</span>
          <span>{todoCount} 待办</span>
        </div>
      </header>

      <div className="kp-ledger-filters" role="tablist" aria-label="筛选桌边记录">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={filter === item.id}
            className={filter === item.id ? 'kp-ledger-chip is-on' : 'kp-ledger-chip'}
            onClick={() => setFilter(item.id)}
          >
            {item.label}
            <b>{counts[item.id]}</b>
          </button>
        ))}
      </div>

      {(pendingChecks.length > 0 || checkRequests.length > 0) && (
        <section className="kp-ledger-todo" aria-label="待你处理">
          <div className="kp-ledger-todo-head">
            <GiHourglass size={11} aria-hidden />
            待你处理
            <b>{pendingChecks.length + checkRequests.length}</b>
          </div>
          {pendingChecks.map((check) => (
            <div key={check.id} className="kp-ledger-todo-row">
              <GiRollingDices size={12} aria-hidden />
              <span className="kp-ledger-todo-text">
                等 <b>{check.actor_name}</b> 投 {checkSkillLabel(check.skill, check.kind, check.difficulty)}
                {check.group && <em> · 群检</em>}
              </span>
              <button type="button" className="kp-ledger-act kp-ledger-act--primary" disabled={!!busy} onClick={() => void proxyRoll(check.id)}>
                代投
              </button>
              <button type="button" className="kp-ledger-act kp-ledger-act--quiet" disabled={!!busy} onClick={() => void cancelCheck(check.id)}>
                撤销
              </button>
            </div>
          ))}
          {checkRequests.map((request) => (
            <div key={request.id} className="kp-ledger-todo-row">
              <GiCrossedSwords size={12} aria-hidden />
              <span className="kp-ledger-todo-text">
                <b>{request.actor_name}</b> 申请「{request.skill}」
                {request.intent && <em>：{request.intent}</em>}
              </span>
              <button type="button" className="kp-ledger-act kp-ledger-act--primary" onClick={() => adoptRequest(request)}>
                填入检定
              </button>
              <button type="button" className="kp-ledger-act kp-ledger-act--quiet" disabled={!!busy} onClick={() => void dismissRequest(request.id)}>
                忽略
              </button>
            </div>
          ))}
        </section>
      )}

      <div className="kp-ledger-scroll" ref={scrollRef} aria-label="桌边记录时间轴">
        <div className="kp-ledger-roundbar">
          <span className="kp-ledger-round-no">第 {combat.round} 轮</span>
          <span className="kp-ledger-round-state">进行中</span>
          <span className="kp-ledger-round-count">{filtered.length} 条</span>
        </div>
        {filtered.length === 0 && (
          <div className="kp-ledger-empty">
            {filter === 'todo'
              ? '当前没有落在时间轴上的待投检定；上面的待办区仍会显示玩家申请。'
              : '这一条筛选下暂时没有记录。'}
          </div>
        )}
        {filtered.map(renderEntry)}
        <div className="kp-ledger-tail">本场战斗的记录都在这里；点左缘名字可定位到他的发言。</div>
      </div>

      {newCount > 0 && (
        <button type="button" className="kp-ledger-new" onClick={scrollToLatest}>
          还有 {newCount} 条新记录 · 回到最新
        </button>
      )}

      <footer className="kp-ledger-foot">
        <span>自动跟到最新</span>
        <span>R{String(combat.round).padStart(2, '0')}</span>
      </footer>
    </aside>
  )
}
