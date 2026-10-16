// 人物卡：技能——点数面板、职业要求、本职勾选、加点表。
import { useMemo, useState } from 'react'
import { Check, Plus, Search } from 'lucide-react'
import { SpecializationDialog } from '@/components/character/SpecializationDialog'
import {
  canPick, CREDIT, formulaText, LOCKED_SKILLS, pickCounts, pointUsage, skillBase, skillRole,
} from './creationRules'
import type { useSheet } from './useSheet'

type Sheet = ReturnType<typeof useSheet>
type Filter = '全部' | '本职' | '已加点'
const SPEC_BASES = ['母语', '外语', '格斗', '射击', '科学', '生存', '技艺', '驾驶']

export function SheetSkills({ sheet }: { sheet: Sheet }) {
  const { state, dispatch, occ, rules, table, specs, build, skillNames, isOcc, skillValue, skillStartOf } = sheet
  const [filter, setFilter] = useState<Filter>('全部')
  const [query, setQuery] = useState('')
  const [specBase, setSpecBase] = useState<string | null>(null)
  const usage = pointUsage(build, table, specs)
  const quick = state.method === 'quick' && !state.imported

  const rows = useMemo(() => {
    const related = (n: string) => (occ ? skillRole(occ, n) !== 'free' || state.picks.includes(n) : false)
    return skillNames
      .filter((n) => !query || n.includes(query.trim()))
      .filter((n) => filter === '全部' || (filter === '本职' ? related(n) || n === CREDIT : skillValue(n) > skillStartOf(n)))
      .sort((a, b) => {
        const ra = a === CREDIT ? 0 : related(a) ? 1 : 2
        const rb = b === CREDIT ? 0 : related(b) ? 1 : 2
        return ra - rb || a.localeCompare(b, 'zh')
      })
  }, [skillNames, query, filter, occ, state.picks, skillValue, skillStartOf])

  const counts = occ ? pickCounts(occ, state.picks) : null
  const capOf = (n: string) => (isOcc(n) ? rules.skill_cap_occupation : rules.skill_cap_other)

  return (
    <>
      {!state.imported && (
        <div className="cs-pts">
          {quick ? (
            <>
              <div className="cs-pt"><div><div className="cs-pt-l">本职技能定值</div><div className="cs-hint">{rules.quick_skills.join(' / ')}，逐项挑</div></div>
                <div className="cs-grow" /><div className="cs-pt-big">{Object.keys(state.quickValues).length}<span className="cs-hint"> / {rules.quick_skills.length}</span></div></div>
              <div className="cs-pt"><div><div className="cs-pt-l">兴趣技能 +{rules.quick_boost}</div><div className="cs-hint">挑 {rules.quick_boosts} 项非本职技能</div></div>
                <div className="cs-grow" /><div className="cs-pt-big">{state.quickBoosts.length}<span className="cs-hint"> / {rules.quick_boosts}</span></div></div>
            </>
          ) : (
            <>
              <Pool label="职业点" hint={occ && usage ? `${formulaText(occ.skill_formula)} = ${usage.occTotal}` : '先选职业'}
                total={usage?.occTotal ?? 0} used={usage?.occUsed ?? 0} />
              <Pool label="兴趣点" hint={usage ? `智力×2 = ${usage.intTotal}` : ''}
                total={usage?.intTotal ?? 0} used={usage?.intUsed ?? 0} />
            </>
          )}
        </div>
      )}

      {occ && counts && !state.imported && (
        <div className="cs-req">
          <b style={{ color: 'var(--color-verdigris, var(--color-success))' }}>{occ.name}</b>
          　固定本职：{occ.skills.join('、') || '无'}　信用评级 {occ.credit_min}–{occ.credit_max}
          <div>
            {occ.groups.map((g, i) => (
              <span key={g.label + i} className="grp"><span className={`cs-pill ${counts.groups[i] === g.pick ? 'ok' : 'bad'}`}>
                {g.label}（{g.options.join('/')}）{counts.groups[i]}/{g.pick}</span></span>
            ))}
            {occ.choices > 0 && (
              <span className="grp"><span className={`cs-pill ${counts.free === occ.choices ? 'ok' : 'bad'}`}>
                任意 {occ.choices} 项其他技能 {counts.free}/{occ.choices}</span></span>
            )}
            <span className="cs-hint">在下表「本职」列勾选</span>
          </div>
        </div>
      )}

      <div className="cs-filters">
        {(['全部', '本职', '已加点'] as Filter[]).map((f) => (
          <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f}</button>
        ))}
        <label className="relative" style={{ marginLeft: 8 }}>
          <Search size={13} aria-hidden className="absolute left-2 top-1/2 -translate-y-1/2" style={{ color: 'var(--color-text-secondary)' }} />
          <input className="input text-sm" style={{ paddingLeft: 24, width: 150 }} placeholder="搜索技能" aria-label="搜索技能"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <span className="cs-grow" />
        <span className="cs-hint">添加专精：</span>
        {SPEC_BASES.map((b) => (
          <button key={b} onClick={() => setSpecBase(b)} className="inline-flex items-center gap-1"><Plus size={11} aria-hidden />{b}</button>
        ))}
      </div>

      <table className="cs-sk">
        <thead><tr>
          <th style={{ width: 44 }}>本职</th><th className="l">技能</th><th>起始</th>
          <th>{state.imported ? '' : quick ? '定值' : '加点'}</th><th>成功率</th><th>困难</th><th>极难</th>
        </tr></thead>
        <tbody>
          {rows.map((n) => {
            const v = skillValue(n)
            const start = n === CREDIT ? 0 : skillStartOf(n)
            const over = n !== CREDIT && v > Math.max(capOf(n), start) && !state.imported
            const occRow = isOcc(n)
            return (
              <tr key={n} className={occRow ? 'is-occ' : ''}>
                <td><PickCell sheet={sheet} name={n} /></td>
                <td className="l">{n}<Tag name={n} sheet={sheet} /></td>
                <td className="cs-fr">{n === CREDIT ? '—' : start}</td>
                <td><ValueCell sheet={sheet} name={n} start={start} /></td>
                <td><span className={`cs-tot${over ? ' over' : ''}`} title={over ? `超过建卡上限 ${capOf(n)}` : undefined}>{v}</span></td>
                <td className="cs-fr">{Math.floor(v / 2)}</td>
                <td className="cs-fr">{Math.floor(v / 5)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {specBase && (
        <SpecializationDialog base={specBase} open onOpenChange={(o) => { if (!o) setSpecBase(null) }}
          disabledItems={skillNames.filter((k) => skillBase(k) === specBase).map((k) => k.slice(specBase.length + 1, -1))}
          onConfirm={(spec) => { dispatch({ type: 'addSkill', skill: `${specBase}(${spec})` }); setSpecBase(null) }} />
      )}
    </>
  )
}

function Pool({ label, hint, total, used }: { label: string; hint: string; total: number; used: number }) {
  const left = total - used
  return (
    <div className="cs-pt">
      <div><div className="cs-pt-l">{label}</div><div className="cs-hint">{hint}</div></div>
      <div className="cs-bar"><i style={{ width: `${total ? Math.min(100, (used / total) * 100) : 0}%` }} /></div>
      <div className={`cs-pt-big${left < 0 ? ' over' : ''}`}>{left}</div><span className="cs-hint">剩余</span>
    </div>
  )
}

function Tag({ name, sheet }: { name: string; sheet: Sheet }) {
  const { occ } = sheet
  if (skillBase(name) === '母语') return <span className="cs-tag free">起始 = 教育，不花点</span>
  if (name === '闪避') return <span className="cs-tag free">起始 = 敏捷/2，不花点</span>
  if (name === CREDIT) return <span className="cs-tag">只用职业点{occ ? ` · ${occ.credit_min}–${occ.credit_max}` : ''}</span>
  if (LOCKED_SKILLS.has(name)) return <span className="cs-tag">建卡不可加</span>
  return null
}

function PickCell({ sheet, name }: { sheet: Sheet; name: string }) {
  const { occ, state, dispatch } = sheet
  if (!occ || state.imported || name === CREDIT || LOCKED_SKILLS.has(name)) return null
  const role = skillRole(occ, name)
  if (role === 'fixed') {
    return <span className="cs-ck is-fixed" title="固定本职技能" aria-label="固定本职技能"><Check size={12} strokeWidth={3} /></span>
  }
  const on = state.picks.includes(name)
  const label = role === 'free' ? '任意其他技能' : occ.groups[Number(role.slice(6))].label
  return (
    <button className="cs-ck" role="checkbox" aria-checked={on} aria-label={`把${name}算作本职（${label}）`} title={label}
      disabled={!on && !canPick(occ, state.picks, name)} onClick={() => dispatch({ type: 'togglePick', skill: name })}>
      {on && <Check size={12} strokeWidth={3} />}
    </button>
  )
}

function ValueCell({ sheet, name, start }: { sheet: Sheet; name: string; start: number }) {
  const { state, dispatch, isOcc, rules, occ } = sheet
  if (state.imported) {
    return (
      <input className="cs-num" type="number" aria-label={`${name}数值`} value={state.imported.skills[name] ?? start}
        onChange={(e) => dispatch({ type: 'set', patch: { imported: { ...state.imported!, skills: { ...state.imported!.skills, [name]: Math.max(0, Math.min(99, Number(e.target.value) || 0)) } } } })} />
    )
  }
  if (name === CREDIT) {
    return (
      <input className="cs-num" type="number" aria-label="信用评级" disabled={!occ} value={state.credit}
        onChange={(e) => dispatch({ type: 'credit', value: Number(e.target.value) || 0 })} />
    )
  }
  if (LOCKED_SKILLS.has(name)) return null
  if (state.method === 'quick') {
    if (isOcc(name)) {
      const taken = Object.entries(state.quickValues).filter(([k]) => k !== name).map(([, v]) => v)
      const pool = [...rules.quick_skills]
      for (const t of taken) { const i = pool.indexOf(t); if (i >= 0) pool.splice(i, 1) }
      const options = [...new Set(pool)].sort((a, b) => b - a)
      const cur = state.quickValues[name]
      return (
        <select className="cs-num" style={{ width: 64 }} aria-label={`${name}定值`} value={cur ?? ''}
          onChange={(e) => dispatch({ type: 'quickValue', skill: name, value: e.target.value ? Number(e.target.value) : null })}>
          <option value="">—</option>
          {cur != null && !options.includes(cur) && <option value={cur}>{cur}</option>}
          {options.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      )
    }
    const on = state.quickBoosts.includes(name)
    return (
      <button className="cs-ck" role="checkbox" aria-checked={on} aria-label={`${name}兴趣 +${rules.quick_boost}`}
        disabled={!on && state.quickBoosts.length >= rules.quick_boosts}
        onClick={() => dispatch({ type: 'toggleBoost', skill: name, max: rules.quick_boosts })}>
        {on && <Check size={12} strokeWidth={3} />}
      </button>
    )
  }
  return (
    <input className="cs-num" type="number" min={0} aria-label={`${name}加点`} value={state.raises[name] || ''}
      placeholder="0" onChange={(e) => dispatch({ type: 'raise', skill: name, value: Number(e.target.value) || 0 })} />
  )
}
