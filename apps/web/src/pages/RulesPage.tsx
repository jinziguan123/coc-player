// 规则：两个标签——「本桌规则」决定骰子怎么判，「规则书」是给 AI 守秘人查阅的参考。
//
// 两者名字像、作用完全不同，所以职责边界直接写在标签上（见 RulesTabBar）。
// 本桌规则的草稿由本页持有：切标签不丢改动；底部草稿条在两个标签下都在；
// 离开页面前没保存会拦一下。设计稿见 tmp/ui-mockups/d-rules-workbench.html。
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Download, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { api, getServerUrl } from '@/api/client'
import { ArchiveHead } from '@/components/layout/ArchiveHead'
import { Switch } from '@/components/ui/switch'
import { RulebookLibrary } from '@/features/rules/RulebookLibrary'
import { ImportDialog, LeaveDialog } from '@/features/rules/RulesDialogs'
import { DraftBar } from '@/features/rules/RulesParts'
import { RulesTabBar, TabScope, type RulesTab } from '@/features/rules/RulesTabs'
import { RulesWorkbenchView } from '@/features/rules/RulesWorkbenchView'
import { diffCounts, useRulesWorkbench } from '@/features/rules/useRulesWorkbench'
import { useLeaveGuard } from '@/lib/navGuard'

const RULE_SYSTEM = 'coc'

export function RulesPage() {
  const wb = useRulesWorkbench(RULE_SYSTEM)
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab: RulesTab = params.get('tab') === 'books' ? 'books' : 'table'
  const [books, setBooks] = useState(0)
  const [importing, setImporting] = useState(false)
  const [leaveTo, setLeaveTo] = useState<string | null>(null)
  // 联机进来的客人：改规则的接口限房主本机，界面上就别让人以为能改
  const readOnly = Boolean(getServerUrl())

  useLeaveGuard(wb.dirty, setLeaveTo)

  // 页头的册数：没切到「规则书」标签也要有；切过去后由书库组件随上传/删除更新
  useEffect(() => {
    api.get<unknown[]>('/rulebooks').then((list) => setBooks(Array.isArray(list) ? list.length : 0)).catch(() => {})
  }, [])

  const pickTab = (t: RulesTab) => setParams(t === 'books' ? { tab: 'books' } : {}, { replace: true })

  const save = async () => {
    try {
      await wb.save()
      toast.success(wb.enabled ? '规则已保存，下一次结算起生效' : '已保存：本桌规则停用中，全部照规则书原文跑')
      return true
    } catch (e: unknown) {
      const lines = (e instanceof Error ? e.message : '保存失败').split('\n').filter(Boolean)
      toast.error(lines[0], { description: lines.slice(1, 6).join('\n') || undefined })
      return false
    }
  }

  const exportRules = async () => {
    if (wb.dirty) toast.warning('导出的是已保存的规则；草稿里的改动保存后才会包含在内')
    try {
      const r = await api.get<{ filename: string; text: string }>(`/rulebooks/village-rules/${RULE_SYSTEM}/export`)
      const url = URL.createObjectURL(new Blob([r.text], { type: 'application/x-yaml;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = r.filename
      a.click()
      URL.revokeObjectURL(url)
      toast.success('房规已导出（含参数、数据表、桌面约定）')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '导出失败')
    }
  }

  if (wb.loadError) {
    return <div className="text-sm py-10 text-center" style={{ color: 'var(--color-danger)' }}>{wb.loadError}</div>
  }

  const diffTotal = wb.manifest ? Object.values(diffCounts(wb)).reduce((a, b) => a + b, 0) : 0

  return (
    <div className="rules-page">
      <ArchiveHead
        title="规则"
        stats={[
          { label: '本桌规则与原文不同', value: diffTotal },
          { label: '规则书已装载', value: books },
        ]}
        actions={tab === 'table' && !readOnly ? (
          <div className="flex items-center gap-2 flex-wrap">
            <button type="button" className="btn-secondary btn-sm inline-flex items-center gap-1.5" onClick={() => setImporting(true)}>
              <Upload size={13} aria-hidden /> 导入房规
            </button>
            <button type="button" className="btn-secondary btn-sm inline-flex items-center gap-1.5" onClick={() => void exportRules()}>
              <Download size={13} aria-hidden /> 导出房规
            </button>
            {/* 总开关：一眼看出本桌规则此刻算不算数。关掉不清配置，随时能开回来 */}
            <div className={`rules-master ${wb.enabled ? '' : 'off'}`}>
              <div>
                <div className="t">本桌规则</div>
                <div className="d">{wb.enabled ? '正在生效' : '已停用：全部照规则书原文'}</div>
              </div>
              <Switch label="启用本桌规则" checked={wb.enabled} onChange={wb.setEnabled} />
            </div>
          </div>
        ) : undefined}
      />

      <RulesTabBar tab={tab} onPick={pickTab} />
      <TabScope tab={tab} />

      <div id={`rules-panel-${tab}`} role="tabpanel" className="rules-panel">
        {tab === 'table'
          ? <RulesWorkbenchView wb={wb} ruleSystem={RULE_SYSTEM} readOnly={readOnly} />
          : <div className="rules-books"><RulebookLibrary onCount={setBooks} /></div>}
      </div>

      <DraftBar pending={wb.pending} saving={wb.saving} onSave={() => void save()} onDiscard={wb.discard} />

      <ImportDialog ruleSystem={RULE_SYSTEM} open={importing} onClose={() => setImporting(false)} onApply={wb.applyImport} />
      <LeaveDialog
        pending={wb.pending} target={leaveTo} saving={wb.saving}
        onStay={() => setLeaveTo(null)}
        onDiscard={() => { const to = leaveTo; wb.discard(); setLeaveTo(null); if (to) navigate(to) }}
        onSave={() => { const to = leaveTo; void save().then((ok) => { if (ok) { setLeaveTo(null); if (to) navigate(to) } }) }}
      />
    </div>
  )
}
