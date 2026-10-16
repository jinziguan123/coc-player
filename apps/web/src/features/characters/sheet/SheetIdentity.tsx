// 人物卡：基础信息、所属模组与车卡建议、导入 / AI 填表。
import { useState } from 'react'
import { toast } from 'sonner'
import { ChevronDown } from 'lucide-react'
import { GiDiceSixFacesSix, GiUpCard } from 'react-icons/gi'
import { uploadFile } from '@/api/client'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CharacterGuidanceCard } from '@/components/module/CharacterGuidanceCard'
import { hasGuidance, useModuleStore, type CharacterGuidance } from '@/stores/moduleStore'
import { generateCharacter } from '@/features/characters/api'
import { applyBuilt, applyImported, type ImportedCharacterData } from './importCard'
import type { useSheet } from './useSheet'

type Sheet = ReturnType<typeof useSheet>
const NONE = '__none'

export function SheetIdentity({ sheet, onPickOccupation }: { sheet: Sheet; onPickOccupation: () => void }) {
  const { state, dispatch } = sheet
  const { modules } = useModuleStore()
  const selectedModule = modules.find((m) => m.id === state.moduleId) ?? null
  const set = (patch: Partial<typeof state>) => dispatch({ type: 'set', patch })
  const occName = state.imported ? state.imported.occupation || state.occName : state.occName

  return (
    <>
      <div className="cs-info">
        <label><span>姓名</span><input className="input w-full" value={state.name} placeholder="调查员姓名"
          onChange={(e) => set({ name: e.target.value })} /></label>
        <div>
          <label htmlFor="cs-occ">职业</label>
          <button id="cs-occ" className="input cs-occ-btn" onClick={onPickOccupation} disabled={!!state.imported}>
            <span style={{ color: occName ? undefined : 'var(--color-text-secondary)' }}>{occName || '点此选择职业'}</span>
            <ChevronDown size={14} aria-hidden />
          </button>
        </div>
        <label><span>年龄</span><input className="input w-full" type="number" min={15} max={89} value={state.age}
          onChange={(e) => dispatch({ type: 'age', age: Math.max(15, Math.min(89, Number(e.target.value) || 15)) })} /></label>
        <label><span>性别</span><input className="input w-full" value={state.gender} placeholder="如：男 / 女"
          onChange={(e) => set({ gender: e.target.value })} /></label>
        <label><span>现居地</span><input className="input w-full" value={state.residence}
          onChange={(e) => set({ residence: e.target.value })} /></label>
        <label><span>出生地</span><input className="input w-full" value={state.birthplace}
          onChange={(e) => set({ birthplace: e.target.value })} /></label>
        <div className="cs-wide">
          <label>所属模组（可选，只用于归类与车卡建议）</label>
          <Select value={state.moduleId || NONE} onValueChange={(v) => set({ moduleId: v === NONE ? '' : v })}>
            <SelectTrigger className="w-full"><SelectValue placeholder="— 不指定 —" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>— 不指定 —</SelectItem>
              {modules.map((m) => (
                <SelectItem key={m.id} value={m.id}>{m.title}{m.world_setting?.era ? `（${m.world_setting.era}）` : ''}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {selectedModule && hasGuidance(selectedModule.character_guidance) && (
        <div style={{ marginTop: 12 }}>
          <CharacterGuidanceCard guidance={selectedModule.character_guidance as CharacterGuidance} moduleTitle={selectedModule.title} />
        </div>
      )}
      <ImportTools sheet={sheet} />
    </>
  )
}

/** 现成卡：Excel 导入、AI 按模组生成。两者都要先选模组（导入要按模组的规则表解析）。 */
function ImportTools({ sheet }: { sheet: Sheet }) {
  const { state, dispatch, table, specs } = sheet
  const [busy, setBusy] = useState<'' | 'excel' | 'ai'>('')
  const [hint, setHint] = useState('')
  const load = (data: ImportedCharacterData) => dispatch({ type: 'load', state: applyImported(state, data) })
  const loadBuilt = (data: ImportedCharacterData) => dispatch({ type: 'load', state: applyBuilt(state, data, table, specs) })

  const importExcel = async (file: File) => {
    if (!state.moduleId) { toast.error('请先选择模组'); return }
    setBusy('excel')
    try {
      const form = new FormData()
      form.append('file', file)
      const data = await uploadFile<ImportedCharacterData>(`/characters/import-excel?module_id=${encodeURIComponent(state.moduleId)}`, form)
      load(data)
      toast.success(`已导入「${data.name ?? '角色'}」，请检查后创建`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导入失败')
    } finally { setBusy('') }
  }

  const aiGenerate = async () => {
    if (!state.moduleId) { toast.error('请先选择模组'); return }
    setBusy('ai')
    try {
      const data = await generateCharacter<ImportedCharacterData & { _fallback?: boolean }>({ module_id: state.moduleId, hint: hint.trim() })
      loadBuilt(data)
      if (data._fallback) toast.warning('AI 暂时不可用，已用规则生成一张草稿，请检查后调整')
      else toast.success(`AI 已填好「${data.name ?? '角色'}」，请检查后创建`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'AI 生成失败')
    } finally { setBusy('') }
  }

  return (
    <div className="cs-tools" style={{ marginTop: 12 }}>
      <GiUpCard aria-hidden style={{ color: 'var(--color-text-accent)' }} />
      <span className="cs-hint">有现成角色卡？</span>
      <label className="btn-secondary btn-sm cursor-pointer">
        {busy === 'excel' ? '导入中…' : '导入 Excel'}
        <input type="file" accept=".xlsx" className="hidden" disabled={!!busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void importExcel(f); e.target.value = '' }} />
      </label>
      <GiDiceSixFacesSix aria-hidden style={{ color: 'var(--color-text-accent)', marginLeft: 12 }} />
      <input className="input text-sm" style={{ flex: 1, minWidth: 160 }} value={hint} disabled={!!busy}
        placeholder="让 AI 建卡：角色概念（可选，如：胆小的图书管理员）" onChange={(e) => setHint(e.target.value)} />
      <button className="btn-secondary btn-sm" disabled={!!busy || !state.moduleId} onClick={() => void aiGenerate()}>
        {busy === 'ai' ? '生成中…' : 'AI 生成'}
      </button>
      {state.imported && (
        <span className="cs-pill">现成卡 · 不按建卡规则约束</span>
      )}
    </div>
  )
}
