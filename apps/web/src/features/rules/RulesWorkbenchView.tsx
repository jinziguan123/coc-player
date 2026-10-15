// 「本桌规则」标签的主体：左侧分区导航 + 右侧可滚动的规则列表。
//
// 草稿（useRulesWorkbench）由页面持有——切到「规则书」标签再切回来，没保存的改动还在。
import { useCallback, useEffect, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { RulePluginsCard } from '@/components/game/RulePluginsCard'
import { Switch } from '@/components/ui/switch'
import { RulesSectionView } from './RuleControls'
import { DataSection, NotesSection, RulesOverview, RulesRail } from './RulesParts'
import { diffCounts, type RulesWorkbench } from './useRulesWorkbench'

/** 各规则分区标题旁的一句话（分区名来自组件声明，这里只补说明；插件新开的分区没有说明）。 */
const SECTION_HINT: Record<string, string> = {
  检定: '一次检定怎么判', 幸运: '失败之后还能不能挽回', 伤害与状态: '挨打之后怎么算',
  理智与疯狂: '掉 SAN 之后怎么算', 成长: '模组结束之后',
}

export function RulesWorkbenchView({ wb, ruleSystem, readOnly }: {
  wb: RulesWorkbench
  ruleSystem: string
  /** 联机进来的客人：能看不能改（改规则的接口限房主本机） */
  readOnly: boolean
}) {
  const mainRef = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState('overview')
  const [query, setQuery] = useState('')
  const [onlyChanged, setOnlyChanged] = useState(false)
  const [table, setTable] = useState('')

  useEffect(() => { if (!table && wb.tables[0]) setTable(wb.tables[0].name) }, [table, wb.tables])

  // 滚动时同步分区高亮：取最后一个顶边已滚过视口上沿的分区
  const onScroll = useCallback(() => {
    const main = mainRef.current
    if (!main) return
    let current = 'overview'
    for (const s of wb.sections) {
      const el = document.getElementById(`sec-${s.id}`)
      if (el && el.offsetTop - main.offsetTop <= main.scrollTop + 120) current = s.id
    }
    setActive(current)
  }, [wb.sections])

  const jump = (section: string, key?: string) => {
    const main = mainRef.current
    const el = (key && document.getElementById(`rule-${key}`)) || document.getElementById(`sec-${section}`)
    if (!main || !el) return
    main.scrollTo?.({ top: el.offsetTop - main.offsetTop - 16, behavior: 'smooth' })
    setActive(section)
    el.classList.remove('rules-flash')
    void el.offsetWidth   // 重新触发闪烁动画
    el.classList.add('rules-flash')
  }

  if (!wb.manifest || !wb.saved) {
    return <div className="text-sm py-10 text-center" style={{ color: 'var(--color-text-secondary)' }}>读取中…</div>
  }
  const counts = diffCounts(wb)
  const filter = { query: query.trim(), onlyChanged }

  return (
    <div className="rules-body">
      <RulesRail sections={wb.sections} active={active} pending={wb.pending} counts={counts} onPick={(id) => jump(id)} />
      <div className="rules-main" ref={mainRef} onScroll={onScroll}
        style={wb.enabled ? undefined : { opacity: 0.6 }}>
        <div className="rules-toolbar">
          <label className="rules-search">
            <Search size={14} aria-hidden />
            <input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="搜索规则"
              placeholder="搜索规则，如「大成功」「幸运」「流血」，也能搜数据表里的行" />
          </label>
          <label className="rules-only">
            <Switch label="只看改过的" checked={onlyChanged} onChange={setOnlyChanged} /> 只看改过的
          </label>
        </div>

        {readOnly && (
          <p className="rules-readonly" role="note">
            你是联机进来的客人：这里是房主这一桌的规则，你在这些规则下掷骰。只有房主能修改。
          </p>
        )}

        <RulesOverview wb={wb} onJump={jump} />
        {/* 客人只读：fieldset 一并禁用里面所有输入与按钮，导航与搜索仍可用 */}
        <fieldset disabled={readOnly} className="rules-fieldset">
          {wb.sections.filter((s) => s.kind === 'rules').map((s) => (
            <RulesSectionView key={s.id} section={s} index={wb.index} values={wb.options}
              onChange={wb.setOption} filter={filter} hint={SECTION_HINT[s.title]} />
          ))}
          <DataSection wb={wb} active={table} onPick={setTable} query={filter.query} onlyChanged={onlyChanged} />
          <NotesSection value={wb.notes} onChange={wb.setNotes} />
        </fieldset>
        <RulePluginsCard ruleSystem={ruleSystem} />
      </div>
    </div>
  )
}
