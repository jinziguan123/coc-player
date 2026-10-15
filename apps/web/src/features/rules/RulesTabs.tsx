// 规则页的两个标签：「本桌规则」与「规则书」，以及两者的职责边界。
//
// 这两样东西名字像、作用完全不同，最容易被混为一谈：以为上传一本规则书就改了判定，
// 或者以为改了村规、AI 读到的规则书原文也会跟着变。所以边界直接写在标签上，
// 展开还有一张逐项对照表——这是房主做选择的地方，说明必须在这里，而不是在文档里。
import { useState } from 'react'
import { m } from 'motion/react'
import { GiBookCover, GiRuleBook } from 'react-icons/gi'
import { Collapse } from '@/components/ui/Collapse'
import { spring } from '@/lib/motion'

export type RulesTab = 'table' | 'books'

const TABS: Array<{ id: RulesTab; title: string; tagline: string; Icon: typeof GiRuleBook }> = [
  { id: 'table', title: '本桌规则', tagline: '决定骰子怎么判', Icon: GiRuleBook },
  { id: 'books', title: '规则书', tagline: '给 AI 守秘人查阅的参考', Icon: GiBookCover },
]

/** 职责对照：每行一个维度，左「本桌规则」右「规则书」。 */
const COMPARE: Array<[string, string, string]> = [
  ['是什么', '这一桌在官方规则之上的改动：参数、可选规则、数据表、桌面约定、插件', '你上传的规则书 PDF，系统在本地切块建立检索索引'],
  ['谁在用', '系统的结算代码：每一次检定、理智、伤害、战斗、追逐都按它算', 'AI 守秘人：叙事中遇到拿不准的细则，查几段原文作参考'],
  ['影响什么', '骰子判定的结果；也会告诉 AI 本桌改了哪些规则，让它说话跟判定对得上', 'AI 怎么描述、选哪个技能、定什么难度、引用哪条细则'],
  ['不影响什么', '规则书原文（AI 查到的仍是书上的字）', '任何一次掷骰结果——换一本、删一本，判定都不变'],
  ['什么时候生效', '保存后，下一次结算起；对这一桌所有房间生效（含进行中的局）', '建好索引后，下一次 AI 回复起'],
  ['不设置会怎样', '完全照规则书原文结算', '照常能玩，AI 按内置的裁定手册与它对规则的了解裁定'],
]

export function RulesTabBar({ tab, onPick }: { tab: RulesTab; onPick: (t: RulesTab) => void }) {
  const [compareOpen, setCompareOpen] = useState(false)
  return (
    <div className="rules-tabs-wrap">
      <div className="rules-tabs" role="tablist" aria-label="规则">
        {TABS.map(({ id, title, tagline, Icon }) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} aria-controls={`rules-panel-${id}`}
            className={`rules-tabbtn ${tab === id ? 'on' : ''}`} onClick={() => onPick(id)}>
            {tab === id && <m.span layoutId="rules-tab-active" className="rules-tabbtn-bg" transition={spring.snappy} />}
            <Icon className="rules-tabbtn-icon" aria-hidden />
            <span className="rules-tabbtn-text">
              <span className="t">{title}</span>
              <span className="d">{tagline}</span>
            </span>
          </button>
        ))}
        <button type="button" className="rules-compare-toggle" aria-expanded={compareOpen}
          onClick={() => setCompareOpen((v) => !v)}>
          {compareOpen ? '收起对照' : '两者有什么区别？'}
        </button>
      </div>
      <Collapse open={compareOpen}>
        <div className="rules-compare" role="region" aria-label="本桌规则与规则书的区别">
          <table>
            <thead>
              <tr><th aria-label="维度" /><th>本桌规则</th><th>规则书</th></tr>
            </thead>
            <tbody>
              {COMPARE.map(([k, a, b]) => (
                <tr key={k}><th scope="row">{k}</th><td>{a}</td><td>{b}</td></tr>
              ))}
            </tbody>
          </table>
          <p>
            <b>两者冲突时以本桌规则为准</b>：判定永远按本桌规则算；AI 读到的规则书原文与本桌规则不一致时，
            系统会告诉它「机制已按本桌规则结算，别照规则书原文解释」。
          </p>
        </div>
      </Collapse>
    </div>
  )
}

/** 每个标签页顶部的一句职责说明。 */
export function TabScope({ tab }: { tab: RulesTab }) {
  return (
    <p className="rules-scope">
      {tab === 'table' ? (
        <>系统<b>按这里结算</b>每一次检定、理智、伤害与战斗，并把改动告诉 AI 守秘人。不改任何一项时，完全照规则书原文。</>
      ) : (
        <>AI 守秘人在叙事中<b>查阅</b>这里的原文作参考（怪物、法术、细则……）。<b>不会改变任何一次掷骰结果</b>——要改判定，请到「本桌规则」。</>
      )}
    </p>
  )
}
