// 本桌规矩的一眼摘要：只列**与规则书原文不同**的那几项。
//
// 这一页此前把整张村规配置表铺在主区，可改村规是房主的低频动作（联机进来的客人连改
// 都改不了，端点限本机）。玩家来这里想知道的只有一件事：**我在什么规则下掷骰**。
// 那件事本该一眼看完，而不是拿十一个开关去跟规则书原文逐条对照。
import { ruleDiffLabels } from '@/lib/villageRules'

export function VillageRulesSummary({ options, notes, enabled }: {
  /** 后端存下的**差异项**（不是 effective）——没改过的项不该出现在摘要里 */
  options: Record<string, unknown>
  notes: string
  enabled: boolean
}) {
  const labels = enabled ? ruleDiffLabels(options) : []
  const stopped = !enabled && (Object.keys(options || {}).length > 0 || !!notes)

  return (
    <div className="text-sm">
      <div style={{ color: 'var(--color-text-secondary)' }}>
        {stopped
          ? '村规已停用，这一桌完全照规则书原文跑。'
          : labels.length === 0
            ? '完全照规则书原文跑，没有改动。'
            : `与规则书原文有 ${labels.length} 处不同，其余照原文：`}
      </div>

      {labels.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {labels.map((text) => <span key={text} className="chip">{text}</span>)}
        </div>
      )}

      {enabled && notes.trim() && (
        // 桌面约定只影响怎么演、不改骰子结算——和上面的参数分开写，别让人以为它会改判定
        <div className="mt-2.5 text-xs rounded-md px-2.5 py-2"
          style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-secondary)' }}>
          <span style={{ color: 'var(--color-text-primary)' }}>桌面约定</span>
          （只影响叙述，不改判定）：{notes.trim()}
        </div>
      )}
    </div>
  )
}
