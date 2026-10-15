// 规则页的其余几块：分区导航、概览、数据表分区、桌面约定、草稿条。
import { useEffect } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { DataTableView } from '@/components/game/DataTableView'
import { spring } from '@/lib/motion'
import { diffFromRaw, officialRows, tableDiffCount, type Change, type RuleSection } from '@/lib/ruleDraft'
import { diffCounts, type RulesWorkbench } from './useRulesWorkbench'

export function RulesRail({ sections, active, pending, counts, onPick }: {
  sections: RuleSection[]
  active: string
  pending: Change[]
  counts: Record<string, number>
  onPick: (id: string) => void
}) {
  let last = ''
  return (
    <nav className="rules-rail" aria-label="规则分区">
      {sections.map((s) => {
        const head = s.group !== last ? <div className="rules-rail-grp">{s.group}</div> : null
        last = s.group
        const p = pending.filter((c) => c.section === s.id).length
        const d = counts[s.id] ?? 0
        return (
          <div key={s.id}>
            {head}
            <button type="button" className={active === s.id ? 'on' : ''} onClick={() => onPick(s.id)}
              aria-current={active === s.id ? 'true' : undefined}>
              {s.title}
              <AnimatePresence initial={false}>
                {(p > 0 || d > 0) && (
                  <m.span key={p > 0 ? 'p' : 'd'} className={`rules-cnt ${p > 0 ? 'pending' : ''}`}
                    title={p > 0 ? `${p} 处未保存` : `与规则书原文有 ${d} 处不同`}
                    initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }} transition={spring.pop}>
                    {p > 0 ? p : d}
                  </m.span>
                )}
              </AnimatePresence>
            </button>
          </div>
        )
      })}
    </nav>
  )
}

export function RulesOverview({ wb, onJump }: { wb: RulesWorkbench; onJump: (section: string, key?: string) => void }) {
  const diffs = diffFromRaw(wb.index, wb.options)
  const toggles = diffs.filter((d) => d.isToggle)
  const params = diffs.filter((d) => !d.isToggle)
  const pendingKeys = new Set(wb.pending.map((c) => c.key))
  const counts = diffCounts(wb)
  const dataLabels = (wb.saved?.labels ?? []).filter((l) => wb.tables.some((t) => l.startsWith(`${t.title}：`)))
  const fmt = (d: (typeof diffs)[number]) => {
    const v = wb.options[d.key]
    if (d.isToggle) return `${d.component.title} ${v ? '开' : '关'}`
    const f = d.field
    const text = f.kind === 'choice' ? (f.choices?.find((c) => c.value === v)?.label ?? String(v)) : f.kind === 'boolean' ? (v ? '开' : '关') : String(v)
    return `${f.title} ${text}`
  }
  const nothing = !diffs.length && !counts.data && !counts.notes
  return (
    <section id="sec-overview" className="rules-sec" aria-label="概览">
      <div className="rules-sec-head"><h3>概览</h3><span>这一桌和规则书原文不一样的地方</span></div>
      <div className="rules-sec-rule" />
      <div className="rules-ov">
        <div className="rules-ov-card"><div className="k">改过的参数</div><div className="v">{params.length}</div><div className="s">其余照规则书原文</div></div>
        <div className="rules-ov-card"><div className="k">开关过的规则</div><div className="v">{toggles.length}</div><div className="s">可选规则、变体与插件</div></div>
        <div className="rules-ov-card"><div className="k">数据表改动</div><div className="v">{counts.data}</div><div className="s">武器、职业、技能……</div></div>
      </div>
      <div className="rules-chips">
        {diffs.map((d) => (
          <button key={d.key} type="button" className={`rules-chip ${pendingKeys.has(d.key) ? 'pending' : ''}`}
            onClick={() => onJump(d.section, d.key)}>
            {pendingKeys.has(d.key) && <span className="dot" aria-label="未保存" />}{fmt(d)}
          </button>
        ))}
        {dataLabels.map((l) => (
          <button key={l} type="button" className="rules-chip" onClick={() => onJump('data')}>{l}</button>
        ))}
        {counts.notes > 0 && <button type="button" className="rules-chip" onClick={() => onJump('notes')}>桌面约定</button>}
        {nothing && <span className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>完全照规则书原文，没有改动。</span>}
      </div>
    </section>
  )
}

export function DataSection({ wb, active, onPick, query, onlyChanged }: {
  wb: RulesWorkbench
  active: string
  onPick: (name: string) => void
  query: string
  onlyChanged: boolean
}) {
  const table = wb.tables.find((t) => t.name === active) ?? wb.tables[0]
  const view = table ? wb.views[table.name] : undefined
  const rows = table ? wb.tableRows[table.name] : undefined
  const { loadTable } = wb
  useEffect(() => { if (table && !view) void loadTable(table.name) }, [table, view, loadTable])
  const counts = (name: string, key: string) => {
    const v = wb.views[name]
    const r = wb.tableRows[name]
    return v && r ? tableDiffCount(officialRows(v, key), r, key) : (wb.saved?.data[name]?.length ?? 0)
  }
  const pendingOf = (name: string) => wb.pending.filter((c) => c.key.startsWith(`${name}:`)).length
  return (
    <section id="sec-data" className="rules-sec wide" aria-label="数据表">
      <div className="rules-sec-head"><h3>数据表</h3><span>武器、职业、技能……改动只存与官方数据的差异</span></div>
      <div className="rules-sec-rule" />
      <div className="flex flex-wrap gap-1.5 mb-3" role="tablist" aria-label="数据表">
        {wb.tables.map((t) => {
          const p = pendingOf(t.name)
          const d = counts(t.name, t.key)
          return (
            <button key={t.name} type="button" role="tab" aria-selected={t.name === table?.name}
              title={t.description} onClick={() => onPick(t.name)}
              className={`rules-tab ${t.name === table?.name ? 'on' : ''}`}>
              {t.title}
              {(p > 0 || d > 0) && <span className={`rules-cnt ${p > 0 ? 'pending' : ''}`}>{p > 0 ? p : d}</span>}
            </button>
          )
        })}
      </div>
      {table && view && rows ? (
        <>
          <div className="text-xs mb-2" style={{ color: 'var(--color-text-secondary)' }}>{table.description}</div>
          <DataTableView key={table.name} table={table} official={officialRows(view, table.key)} rows={rows}
            onChange={(next) => wb.setTable(table.name, next)} query={query} onlyChanged={onlyChanged} />
        </>
      ) : (
        <div className="text-sm py-6 text-center" style={{ color: 'var(--color-text-secondary)' }}>读取中…</div>
      )}
    </section>
  )
}

const NOTES_MAX = 800

export function NotesSection({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <section id="sec-notes" className="rules-sec" aria-label="桌面约定">
      <div className="rules-sec-head"><h3>桌面约定</h3><span>参数管不到的规矩，写给守秘人看</span></div>
      <div className="rules-sec-rule" />
      {/* 界限必须写在界面上：它进的是 KP 的提示词，只改叙述，不改任何一次骰子结算 */}
      <p className="text-xs mb-2 leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
        「重调查轻战斗」「NPC 死亡不可逆」「别用现代词汇」这类口径。
        <b style={{ color: 'var(--color-accent)' }}>只影响怎么演，不改骰子结算</b>——要改判定请用上面的规则。
      </p>
      <textarea aria-label="桌面约定" value={value} maxLength={NOTES_MAX} rows={5}
        onChange={(e) => onChange(e.target.value)}
        placeholder="例：本局重调查轻战斗，能谈就别打；NPC 死了就是死了，不要复活。"
        className="input w-full text-sm resize-y" />
      <div className="text-right" style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}>
        {value.length}/{NOTES_MAX}
      </div>
    </section>
  )
}

export function DraftBar({ pending, saving, onSave, onDiscard }: {
  pending: Change[]
  saving: boolean
  onSave: () => void
  onDiscard: () => void
}) {
  return (
    <AnimatePresence>
      {pending.length > 0 && (
        <m.div className="rules-draft" role="region" aria-label="未保存的改动"
          initial={{ y: '100%' }} animate={{ y: 0 }} exit={{ y: '100%' }} transition={spring.soft}>
          <div className="n">{pending.length}</div>
          <div className="min-w-0 flex-1">
            <div className="what">
              未保存的改动：{pending.map((c, i) => <span key={c.key}>{i > 0 && '、'}<em>{c.text}</em></span>)}
            </div>
            <div className="scope">保存后对这一桌所有房间生效（含进行中的局），下一次结算起算数。</div>
          </div>
          <button type="button" className="btn-secondary btn-sm" onClick={onDiscard} disabled={saving}>放弃</button>
          <button type="button" className="btn-primary" onClick={onSave} disabled={saving}>
            {saving ? '保存中…' : '保存改动'}
          </button>
        </m.div>
      )}
    </AnimatePresence>
  )
}
