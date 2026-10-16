// 人物卡：选职业。按大类分组，每张卡写明本职技能、选择组、信用评级区间，并按当前属性算好职业点。
import { useState } from 'react'
import { Search } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { formulaText, occupationPoints, type Attrs, type Occupation } from './creationRules'

export function OccupationDialog({
  open, onOpenChange, occupations, categories, attrs, current, onPick,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  occupations: Occupation[]
  categories: string[]
  attrs: Attrs | null
  current: string | null
  onPick: (occ: Occupation) => void
}) {
  const [cat, setCat] = useState('全部')
  const [query, setQuery] = useState('')
  const cats = ['全部', ...categories.filter((c) => occupations.some((o) => (o.category || '其他') === c))]
  const q = query.trim()
  const list = occupations.filter((o) => (cat === '全部' || (o.category || '其他') === cat)
    && (!q || o.name.includes(q) || o.skills.some((s) => s.includes(q))
      || o.groups.some((g) => g.options.some((s) => s.includes(q)))))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="!max-w-5xl">
        <DialogHeader>
          <DialogTitle>选择职业</DialogTitle>
          <div className="flex items-center gap-3">
            <p className="cs-hint">职业点按你现在的属性算出；换职业会清空本职勾选与职业定值。</p>
            <label className="relative ml-auto">
              <Search size={13} aria-hidden className="absolute left-2 top-1/2 -translate-y-1/2" style={{ color: 'var(--color-text-secondary)' }} />
              <input className="input text-sm" style={{ paddingLeft: 24, width: 200 }} placeholder="搜职业或技能"
                aria-label="搜职业或技能" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
          </div>
        </DialogHeader>
        <div className="cs-occ-dlg">
          <nav className="cs-occ-cats" aria-label="职业大类">
            {cats.map((c) => <button key={c} aria-pressed={c === cat} onClick={() => setCat(c)}>{c}</button>)}
          </nav>
          <div className="cs-occ-list">
            {list.map((o) => (
              <div key={o.name} className="cs-occ" aria-current={o.name === current}>
                <h3>
                  {o.name}<span className="cs-pill">{o.category || '其他'}</span>
                  <span className="cs-grow" />
                  <button className="btn-primary btn-sm" onClick={() => onPick(o)}>{o.name === current ? '已就职' : '就职'}</button>
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  <span className="cs-pill">信用评级 {o.credit_min}–{o.credit_max}</span>
                  <span className="cs-pill">
                    {formulaText(o.skill_formula)}{attrs ? ` = ${occupationPoints(o.skill_formula, attrs)}` : ''}
                  </span>
                </div>
                <div className="sk">
                  {o.skills.join('、')}
                  {o.groups.map((g, i) => <em key={g.label + i}>{o.skills.length || i ? '、' : ''}{g.label}（{g.options.join('/')}）</em>)}
                  {o.choices > 0 && <em>{'、'}任意 {o.choices} 项其他技能</em>}
                </div>
              </div>
            ))}
            {list.length === 0 && <p className="cs-hint">没有匹配的职业。</p>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
