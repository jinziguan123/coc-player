/**
 * 一页式人物卡（新建调查员）。整张卡始终可见、实时联动；右栏是建卡清单，底栏汇总还差什么。
 * 违规（点数超支、选择组没选够、超建卡上限……）才禁止创建，没花完的点只提醒；创建时后端用
 * 同一套规则再校验一遍（见 creationRules.ts 与 server 的 coc7.creation）。
 */
import { useEffect, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { driver } from 'driver.js'
import 'driver.js/dist/driver.css'
import { CircleHelp } from 'lucide-react'
import { api } from '@/api/client'
import { deriveAssets, RuleModuleContext } from '@/components/character/useCocData'
import { useModuleStore } from '@/stores/moduleStore'
import { createCharacter, type Character } from '@/features/characters/api'
import { buildCharacterPayload } from '@/features/characters/characterPayload'
import { creationRecord, CREDIT, type Method } from './creationRules'
import { DRAFT_KEY, STORY_FIELDS, useSheet, type SheetState } from './useSheet'
import { SheetIdentity } from './SheetIdentity'
import { SheetAttributes } from './SheetAttributes'
import { SheetSkills } from './SheetSkills'
import { SheetExtras, SheetGear, SheetStory } from './SheetStory'
import { SheetRail } from './SheetRail'
import { jumpTo } from './jump'
import { OccupationDialog } from './OccupationDialog'
import './sheet.css'

const METHODS: Array<[Method, string]> = [['roll', '掷骰'], ['buy', '点数购买'], ['quick', '快速车卡']]

const TOUR = [
  ['#cs-info', '基础信息', '先起名、定年龄；选了所属模组会给出这个本子的车卡建议。也可以导入现成的 Excel 卡或让 AI 填好。'],
  ['#cs-mode', '三种建卡方式', '掷骰：一次三组任选；点数购买：自由分配；快速车卡：把八个定值分给八项属性，五分钟出卡。'],
  ['#cs-attr', '属性与年龄修正', '每项属性同时显示原值、一半、五分之一。改年龄后这里列出该年龄段的规则，要扣的属性点由你自己分配。'],
  ['#cs-occ', '选择职业', '职业表按大类分组，每张卡写明本职技能、信用评级与点数公式，并按你的属性算好职业点。'],
  ['#cs-skills', '本职技能与加点', '固定本职技能自动勾上；「一项社交技能」这类选择组、「任意 N 项」要你自己在「本职」列勾。职业点只能加本职技能，兴趣点加哪都行。'],
  ['#cs-story', '背景故事', '「重要之人」等关键背景会在剧情里被 KP 用到。'],
  ['.cs-rail', '建卡清单', '右栏逐项打勾，点某项跳到对应区块；红字是要处理的问题，处理完才能创建。'],
] as const

function Card({ id, title, en, extra, children }: { id: string; title: string; en: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="cs-card" aria-label={title}>
      <div className="cs-card-h"><h2>{title}</h2><span className="en">{en}</span><span className="cs-grow" />{extra}</div>
      <div className="cs-card-b">{children}</div>
    </section>
  )
}

export function CharacterSheet({ initial, onCreated, onCancel, cancelLabel = '返回列表', saveDraft = true }: {
  initial?: SheetState
  /** 建好之后：交回新卡（大厅据此直接放进预览等待入座） */
  onCreated: (character: Character) => void
  onCancel: () => void
  cancelLabel?: string
  /** 角色页才存草稿；大厅里嵌的那张不存，免得覆盖角色页没建完的卡 */
  saveDraft?: boolean
}) {
  const sheet = useSheet(initial, { saveDraft })
  const { state, dispatch, occupations, categories, attrs, check, occ, savedAt } = sheet
  const { modules, fetchModules } = useModuleStore()
  const [occOpen, setOccOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [evalResult, setEvalResult] = useState<{ warnings: string[]; suggestions: string[] } | null>(null)

  useEffect(() => { if (modules.length === 0) void fetchModules() }, [modules.length, fetchModules])

  // 现金 / 消费水平 / 资产跟着信用评级走（7 版换算表）；玩家手改过就不再覆盖
  const credit = state.imported ? state.imported.skills[CREDIT] ?? 0 : state.credit
  useEffect(() => {
    if (state.assetsTouched || credit <= 0) return
    const d = deriveAssets(credit)
    dispatch({ type: 'set', patch: { assets: { cash: d.cash, spendingLevel: d.spendingLevel, assets: `约 $${d.assets.toLocaleString()}` } } })
  }, [credit, state.assetsTouched, dispatch])

  const startTour = () => {
    driver({
      showProgress: true, nextBtnText: '下一步', prevBtnText: '上一步', doneBtnText: '完成', progressText: '{{current}} / {{total}}',
      steps: TOUR.map(([element, title, description]) => ({ element, popover: { title, description } })),
    }).drive()
  }

  const finalSkills = (): Record<string, number> => {
    if (state.imported) {
      const base: Record<string, number> = {}
      for (const n of sheet.skillNames) base[n] = sheet.skillStartOf(n)
      return { ...base, ...state.imported.skills }
    }
    return { ...sheet.build.skills }
  }

  const doCreate = async () => {
    if (!attrs) return
    setCreating(true)
    try {
      const sd: Record<string, unknown> = { ...Object.fromEntries(STORY_FIELDS.map(([k]) => [k, state.story[k]]).filter(([, v]) => v)) }
      const occName = state.imported?.occupation || occ?.name
      if (occName) sd.occupation = occName
      for (const k of ['gender', 'residence', 'birthplace'] as const) if (state[k]) sd[k] = state[k]
      if (credit > 0) sd.creditRating = credit
      sd.luck = state.luck
      if (state.assets.cash) sd.cash = state.assets.cash
      if (state.assets.spendingLevel) sd.spendingLevel = state.assets.spendingLevel
      if (state.assets.assets.trim()) sd.assets = state.assets.assets
      const m = state.mythos
      if (m.spells.length || m.tomes.length || m.encounters.length) sd.mythos = m
      const rels = state.relations.filter((r) => r.name.trim() || r.relation.trim())
      if (rels.length) sd.relations = rels
      const hist = state.moduleHistory.filter((h) => h.module.trim() || h.experience.trim())
      if (hist.length) sd.moduleHistory = hist
      const backstory = STORY_FIELDS.filter(([k]) => state.story[k]).map(([k, label]) => `【${label}】${state.story[k]}`).join('\n')
      const created = await createCharacter({
        ...buildCharacterPayload({
          name: state.name, moduleId: state.moduleId, age: state.age, baseAttributes: attrs, skills: finalSkills(),
          backstory, systemData: sd, equipmentText: state.equipText, weapons: state.weapons,
        }),
        creation: state.imported ? null : creationRecord(sheet.build),
      })
      if (saveDraft) localStorage.removeItem(DRAFT_KEY)
      toast.success(`调查员「${state.name}」创建成功`)
      onCreated(created)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  /** 选了模组就先让 AI 看一眼年代与背景契合度；有顾虑时摊开给玩家看，可以仍然创建。 */
  const create = async () => {
    if (!state.moduleId) { await doCreate(); return }
    setCreating(true)
    try {
      const r = await api.post<{ compatible: boolean; warnings: string[]; suggestions: string[] }>('/characters/evaluate', {
        module_id: state.moduleId, name: state.name, occupation: state.imported?.occupation || occ?.name || '',
        backstory: STORY_FIELDS.map(([k]) => state.story[k]).filter(Boolean).join('\n'),
      })
      setCreating(false)
      if (r.compatible && r.warnings.length === 0) await doCreate()
      else setEvalResult(r)
    } catch {
      setCreating(false)
      await doCreate()
    }
  }

  const missingName = !state.name.trim()
  const blocked = check.problems.length > 0 || missingName || !attrs
  const footMsg = missingName ? '还没起名'
    : check.problems.length ? `还有 ${check.problems.length} 处要处理：${check.problems[0].text}`
      : check.warnings.length ? `可以创建了（${check.warnings.map((w) => w.text).join('；')}）` : '一切就绪'

  return (
    <RuleModuleContext.Provider value={state.moduleId || null}>
      <div className="cs-head">
        <div className="cs-seg" id="cs-mode" role="group" aria-label="建卡方式">
          {METHODS.map(([m, label]) => (
            <button key={m} aria-pressed={!state.imported && state.method === m} onClick={() => dispatch({ type: 'method', method: m })}>{label}</button>
          ))}
        </div>
        <button className="btn-secondary btn-sm inline-flex items-center gap-1" onClick={startTour}><CircleHelp size={13} aria-hidden /> 引导</button>
        <span className="cs-grow" />
        {saveDraft && savedAt && <span className="cs-hint">草稿已自动保存</span>}
      </div>

      <div className="cs">
        <div className="cs-main">
          <Card id="cs-info" title="基础信息" en="Investigator"><SheetIdentity sheet={sheet} onPickOccupation={() => setOccOpen(true)} /></Card>
          <Card id="cs-attr" title="属性" en="Characteristics"><SheetAttributes sheet={sheet} /></Card>
          <Card id="cs-skills" title="技能" en="Skills"><SheetSkills sheet={sheet} /></Card>
          <Card id="cs-gear" title="资产与装备" en="Possessions"><SheetGear sheet={sheet} /></Card>
          <Card id="cs-story" title="背景故事" en="Backstory"><SheetStory sheet={sheet} /></Card>
          <Card id="cs-extra" title="神话、关系与经历" en="Mythos · Companions"><SheetExtras sheet={sheet} /></Card>

          {evalResult && (
            <div className="cs-card" style={{ borderColor: 'var(--color-accent)' }}>
              <div className="cs-card-b" style={{ paddingTop: 12 }}>
                <b>与所选模组的契合度有些顾虑</b>
                <ul className="cs-hint" style={{ margin: '6px 0 10px 18px', listStyle: 'disc' }}>
                  {[...evalResult.warnings, ...evalResult.suggestions].map((w) => <li key={w}>{w}</li>)}
                </ul>
                <div className="flex gap-2">
                  <button className="btn-secondary btn-sm" onClick={() => setEvalResult(null)}>回去修改</button>
                  <button className="btn-primary btn-sm" disabled={creating} onClick={() => { setEvalResult(null); void doCreate() }}>仍然创建</button>
                </div>
              </div>
            </div>
          )}

          <div className="cs-foot">
            <span className={`msg${blocked ? ' bad' : ''}`}>{footMsg}</span>
            <span className="cs-grow" />
            {blocked && check.problems[0] && (
              <button className="btn-secondary btn-sm" onClick={() => jumpTo(check.problems[0].area === 'occ' || check.problems[0].area === 'skill' ? 'cs-skills' : 'cs-attr')}>去处理</button>
            )}
            <button className="btn-secondary btn-sm" onClick={onCancel}>{cancelLabel}</button>
            <button className="btn-primary btn-sm" disabled={blocked || creating} onClick={() => void create()}>
              {creating ? '创建中…' : '创建调查员'}
            </button>
          </div>
        </div>
        <SheetRail sheet={sheet} />
      </div>

      <OccupationDialog open={occOpen} onOpenChange={setOccOpen} occupations={occupations} categories={categories}
        attrs={attrs} current={state.occName}
        onPick={(o) => { dispatch({ type: 'occupation', occ: o }); setOccOpen(false); toast.success(`就职：${o.name}，固定本职技能已勾上`) }} />
    </RuleModuleContext.Provider>
  )
}
