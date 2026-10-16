// 人物卡右栏：派生值、建卡清单（点哪项跳到哪块）、本桌的建卡规则。
import { Check } from 'lucide-react'
import { useDerivedStats } from '@/components/character/useCocData'
import type { Area } from './creationRules'
import type { useSheet } from './useSheet'
import { jumpTo } from './jump'

type Sheet = ReturnType<typeof useSheet>

const ITEMS: Array<{ area: Area | 'story'; title: string; section: string }> = [
  { area: 'attr', title: '属性与幸运', section: 'cs-attr' },
  { area: 'age', title: '年龄修正', section: 'cs-attr' },
  { area: 'occ', title: '职业与本职技能', section: 'cs-skills' },
  { area: 'skill', title: '技能加点', section: 'cs-skills' },
  { area: 'story', title: '背景与重要之人', section: 'cs-story' },
]

export function SheetRail({ sheet }: { sheet: Sheet }) {
  const { state, attrs, check, rules } = sheet
  const d = useDerivedStats(attrs, state.age, state.moduleId || null)
  const cells: Array<[string, string | number]> = d
    ? [['生命', d.hp], ['魔法', d.mp], ['理智', d.san], ['移动', d.mov], ['伤害加值', d.db], ['体格', d.build], ['幸运', state.luck], ['闪避', d.dodge]]
    : []
  const houseCaps = rules.skill_cap_occupation !== 90 || rules.skill_cap_other !== 90

  return (
    <aside className="cs-rail" aria-label="建卡清单">
      <div className="cs-derived">
        {cells.map(([k, v]) => <div key={k} className="cs-dv"><span className="num">{v}</span><small>{k}</small></div>)}
      </div>
      <h4>建卡清单</h4>
      {state.imported ? (
        <p className="cs-hint">现成卡不按建卡规则检查，照原样创建。</p>
      ) : ITEMS.map((it) => {
        const bad = check.problems.filter((p) => p.area === it.area)
        const warn = check.warnings.filter((p) => p.area === it.area)
        const storyWarn = it.area === 'story' && !state.story.significantPeople.trim()
        const done = it.area === 'story' ? !storyWarn : bad.length === 0
        return (
          <button key={it.area} className={`cs-chk ${done ? 'done' : it.area === 'story' ? '' : 'bad'}`} onClick={() => jumpTo(it.section)}>
            <span className="dot">{done && <Check size={11} strokeWidth={3} />}</span>
            <span>
              {it.title}
              {bad.length > 0 && <small>{bad.map((b) => b.text).join('；')}</small>}
              {bad.length === 0 && warn.length > 0 && <small>{warn.map((w) => w.text).join('；')}</small>}
              {storyWarn && <small>建议写一位重要之人：KP 会在剧情里用到</small>}
            </span>
          </button>
        )
      })}
      <div className="cs-rail-sec">
        <h4>本桌建卡规则</h4>
        <p className="cs-hint">
          技能建卡上限：<b>本职 {rules.skill_cap_occupation} / 其他 {rules.skill_cap_other}</b>
          {houseCaps ? '（本桌规则）' : '（规则书默认）'}<br />
          起始值本就更高的技能（如母语 = 教育）不算超限，只是不能再加点。<br />
          房主可在「规则」页的「建卡」里修改。
        </p>
      </div>
    </aside>
  )
}
