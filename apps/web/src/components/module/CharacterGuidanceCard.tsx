import { GiScrollUnfurled } from 'react-icons/gi'

import { hasGuidance, type CharacterGuidance } from '@/stores/moduleStore'

/**
 * 车卡建议卡片：玩家选中模组建角色时看到的取向与限制。
 *
 * 内容由模组设定派生、存在模组上（见后端 `Module.character_guidance`），房主可在
 * 模组详情页改写。这里只负责展示——四个字段各自独立，缺哪块就不渲染哪块，
 * 因为历史模组可能只补生成了一部分。
 */
export function CharacterGuidanceCard({
  guidance,
  moduleTitle,
}: {
  guidance: CharacterGuidance
  moduleTitle?: string
}) {
  if (!hasGuidance(guidance)) return null

  return (
    <div className="guidance-card">
      <div className="guidance-card__head">
        <GiScrollUnfurled aria-hidden="true" />
        <span>车卡建议{moduleTitle ? ` · ${moduleTitle}` : ''}</span>
      </div>

      {guidance.summary && <p className="guidance-card__summary">{guidance.summary}</p>}

      {!!guidance.recommended?.length && (
        <div className="guidance-card__block">
          <span className="guidance-card__label">适合</span>
          <div className="guidance-card__chips">
            {guidance.recommended.map((item) => (
              <span key={item} className="guidance-chip">{item}</span>
            ))}
          </div>
        </div>
      )}

      {!!guidance.avoid?.length && (
        <div className="guidance-card__block">
          <span className="guidance-card__label">不建议</span>
          <ul className="guidance-card__list">
            {guidance.avoid.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}

      {!!guidance.notes?.length && (
        <div className="guidance-card__block">
          <span className="guidance-card__label">要点</span>
          <ul className="guidance-card__list">
            {guidance.notes.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
