// 人物卡：属性（三种生成方式）与年龄修正。
import { useState } from 'react'
import { Minus, Plus } from 'lucide-react'
import { GiRollingDices } from 'react-icons/gi'
import { ageBand, ATTR_KEYS, ATTR_LABELS, deductLeft, PLUS_SIX, type AttrKey } from './creationRules'
import { baseAttrsOf, type useSheet } from './useSheet'

type Sheet = ReturnType<typeof useSheet>

const HINT = {
  roll: '3D6×5，体型 / 智力 / 教育为 (2D6+6)×5；一次掷三组，任选一组',
  buy: '自由分配点数；体型 / 智力 / 教育 40–90，其余 15–90',
  quick: '规则书「快速车卡」：把下面八个值各放进一项属性',
} as const

export function SheetAttributes({ sheet }: { sheet: Sheet }) {
  const { state, dispatch, attrs, rules } = sheet
  const base = baseAttrsOf(state)
  const [pick, setPick] = useState<number | null>(null)   // 快速车卡：手里拿着的那个值
  const [rolledAt, setRolledAt] = useState(0)

  if (state.imported) {
    return (
      <p className="cs-hint">这张卡是导入或 AI 填好的现成卡，属性按原样保留、不做年龄修正。要按规则重新车卡，在顶部切换建卡方式。</p>
    )
  }

  const usedQuick = ATTR_KEYS.map((k) => state.quick[k]).filter((v): v is number => v != null)
  const chipUsed = (i: number) => {
    const v = rules.quick_attrs[i]
    const before = rules.quick_attrs.slice(0, i).filter((x) => x === v).length
    return before < usedQuick.filter((x) => x === v).length
  }
  const onAttrClick = (k: AttrKey) => {
    if (state.method !== 'quick') return
    if (pick != null) { dispatch({ type: 'quickAttr', key: k, value: rules.quick_attrs[pick] }); setPick(null) }
    else if (state.quick[k] != null) dispatch({ type: 'quickAttr', key: k, value: null })
  }
  const buyUsed = ATTR_KEYS.reduce((t, k) => t + (state.buy[k] ?? 0), 0)

  return (
    <>
      <p className="cs-hint" style={{ marginBottom: 8 }}>{HINT[state.method]}</p>
      <div className="cs-attrs" key={rolledAt}>
        {ATTR_KEYS.map((k) => {
          const raw = base[k]
          const fin = attrs?.[k] ?? raw
          const delta = fin != null && raw != null ? fin - raw : 0
          const target = state.method === 'quick' && pick != null
          return (
            <div key={k} className={`cs-attr${target ? ' is-target' : ''}${rolledAt ? ' is-rolled' : ''}`}
              onClick={() => onAttrClick(k)} role={state.method === 'quick' ? 'button' : undefined}
              aria-label={state.method === 'quick' ? `${ATTR_LABELS[k]}：${raw ?? '未放值'}` : undefined}>
              <div className="cs-attr-nm"><b>{ATTR_LABELS[k]}</b><small>{k}</small></div>
              {delta !== 0 && <span className={`cs-attr-delta ${delta < 0 ? 'neg' : 'pos'}`}>{delta > 0 ? '+' : ''}{delta}</span>}
              <div className="cs-attr-v">
                {state.method === 'buy' ? (
                  <input aria-label={ATTR_LABELS[k]} type="number" value={raw ?? ''} step={5}
                    min={PLUS_SIX.has(k) ? 40 : 15} max={90}
                    onChange={(e) => {
                      const lo = PLUS_SIX.has(k) ? 40 : 15
                      dispatch({ type: 'buy', key: k, value: Math.max(lo, Math.min(90, Number(e.target.value) || lo)) })
                    }} />
                ) : (fin ?? <span className="cs-hint" style={{ fontSize: 12 }}>放值</span>)}
              </div>
              <div className="cs-attr-fr"><span>{fin != null ? Math.floor(fin / 2) : '—'}</span><span>{fin != null ? Math.floor(fin / 5) : '—'}</span></div>
            </div>
          )
        })}
        <div className="cs-attr">
          <div className="cs-attr-nm"><b>幸运</b><small>LUCK</small></div>
          <div className="cs-attr-v">{state.luck}</div>
          <div className="cs-attr-fr"><span>{Math.floor(state.luck / 2)}</span><span>{Math.floor(state.luck / 5)}</span></div>
        </div>
      </div>

      <div className="cs-tools">
        {state.method === 'roll' && (
          <>
            <button className="btn-secondary btn-sm inline-flex items-center gap-1"
              onClick={() => { dispatch({ type: 'reroll' }); setRolledAt(Date.now()) }}>
              <GiRollingDices aria-hidden /> 重掷三组
            </button>
            {state.rollSets.map((set, i) => (
              <button key={i} className="cs-set" aria-pressed={i === state.setIdx} onClick={() => dispatch({ type: 'pickSet', idx: i })}>
                第 {i + 1} 组 · 总和 {ATTR_KEYS.reduce((t, k) => t + set[k], 0)}
              </button>
            ))}
          </>
        )}
        {state.method === 'buy' && (
          <span className={`cs-pill ${buyUsed > rules.point_buy_total ? 'bad' : buyUsed === rules.point_buy_total ? 'ok' : ''}`}>
            已用 {buyUsed} / {rules.point_buy_total}
          </span>
        )}
        {state.method === 'quick' && (
          <>
            {rules.quick_attrs.map((v, i) => (
              <button key={i} className="cs-chip" aria-pressed={pick === i} disabled={chipUsed(i)}
                onClick={() => setPick(pick === i ? null : i)}>{v}</button>
            ))}
            <span className="cs-hint">先点一个值，再点要放的属性；点已放值的属性收回。</span>
          </>
        )}
        <span className="cs-hint" style={{ marginLeft: 'auto' }}>幸运 3D6×5{state.age <= 19 ? '（少年掷两次取高）' : ''}</span>
      </div>

      <AgePanel sheet={sheet} />
    </>
  )
}

function AgePanel({ sheet }: { sheet: Sheet }) {
  const { state, dispatch } = sheet
  const band = ageBand(state.age)
  const left = deductLeft(state.age, state.deductions)
  return (
    <div className="cs-age">
      <div>
        <div className="cs-hint"><b>{state.age} 岁</b>的年龄修正（规则书第三章）</div>
        <ul>{band.text.map((t) => <li key={t}>{t}</li>)}</ul>
        {state.eduRolls.length > 0 && (
          <p className="cs-hint" style={{ marginTop: 6 }}>
            教育增强检定：{state.eduRolls.map((r, i) => `第 ${i + 1} 次 D100=${r.roll}${r.gain ? `，超过当前教育，+${r.gain}` : '，未超过'}`).join('；')}
          </p>
        )}
      </div>
      <div>
        {band.deduct ? (
          <div className="cs-deduct">
            {band.deductFrom.map((k) => (
              <div key={k}>
                <b>{ATTR_LABELS[k]}</b>
                <span className="num">−{state.deductions[k] ?? 0}</span>
                <span className="cs-step">
                  <button aria-label={`${ATTR_LABELS[k]}少扣 5`} disabled={!state.deductions[k]}
                    onClick={() => dispatch({ type: 'deduct', key: k, delta: -5 })}><Minus size={12} /></button>
                  <button aria-label={`${ATTR_LABELS[k]}多扣 5`} disabled={left <= 0}
                    onClick={() => dispatch({ type: 'deduct', key: k, delta: 5 })}><Plus size={12} /></button>
                </span>
              </div>
            ))}
            <div style={{ borderStyle: 'dashed' }}>
              <b>还需分配</b>
              <span className={`num${left === 0 ? ' ok' : ''}`}>{left}</span>
              <span className="cs-hint">每次 5 点</span>
            </div>
          </div>
        ) : <p className="cs-hint">这个年龄段不用扣属性。</p>}
      </div>
    </div>
  )
}
