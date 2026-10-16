// 人物卡：背景故事、资产、装备，以及克苏鲁神话 / 人际关系 / 经历过的模组。
import {
  AssetsPanel, ModuleHistoryEditor, MythosEditor, RelationsEditor,
} from '@/components/character/CharacterExtraEditors'
import { WeaponsEditor } from '@/components/character/WeaponsEditor'
import { CREDIT } from './creationRules'
import { STORY_FIELDS, type useSheet } from './useSheet'

type Sheet = ReturnType<typeof useSheet>

export function SheetStory({ sheet }: { sheet: Sheet }) {
  const { state, dispatch } = sheet
  return (
    <div className="cs-story">
      {STORY_FIELDS.map(([key, label, isKey]) => (
        <label key={key} className={key === 'investigatorHistory' ? 'cs-wide' : undefined}>
          <span className="cs-hint">{isKey ? <b>{label}（关键背景，KP 会在剧情里用到）</b> : label}</span>
          <textarea className={`input text-sm${isKey ? ' key' : ''}`} value={state.story[key]}
            onChange={(e) => dispatch({ type: 'story', key, value: e.target.value })} />
        </label>
      ))}
    </div>
  )
}

export function SheetGear({ sheet }: { sheet: Sheet }) {
  const { state, dispatch, skillValue } = sheet
  const credit = state.imported ? state.imported.skills[CREDIT] ?? 0 : state.credit
  return (
    <div className="space-y-4">
      <AssetsPanel creditRating={credit} value={state.assets}
        onChange={(assets) => dispatch({ type: 'set', patch: { assets, assetsTouched: true } })} />
      <div>
        <div className="cs-hint" style={{ marginBottom: 4 }}>武器</div>
        <WeaponsEditor weapons={state.weapons} skillValueOf={skillValue}
          onChange={(weapons) => dispatch({ type: 'set', patch: { weapons } })} />
      </div>
      <label className="block">
        <span className="cs-hint">随身物品（用、分隔）</span>
        <input className="input w-full text-sm" value={state.equipText} placeholder="如：怀表、笔记本、手电筒"
          onChange={(e) => dispatch({ type: 'set', patch: { equipText: e.target.value } })} />
      </label>
    </div>
  )
}

export function SheetExtras({ sheet }: { sheet: Sheet }) {
  const { state, dispatch } = sheet
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <div><div className="cs-hint" style={{ marginBottom: 4 }}>克苏鲁神话</div>
        <MythosEditor value={state.mythos} onChange={(mythos) => dispatch({ type: 'set', patch: { mythos } })} /></div>
      <div><div className="cs-hint" style={{ marginBottom: 4 }}>人际关系</div>
        <RelationsEditor value={state.relations} onChange={(relations) => dispatch({ type: 'set', patch: { relations } })} /></div>
      <div><div className="cs-hint" style={{ marginBottom: 4 }}>经历过的模组</div>
        <ModuleHistoryEditor value={state.moduleHistory} onChange={(moduleHistory) => dispatch({ type: 'set', patch: { moduleHistory } })} /></div>
    </div>
  )
}
