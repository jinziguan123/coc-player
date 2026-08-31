// 幸运消费询价卡：检定失败时，问这一骰的主人要不要动用幸运。
//
// 两种用法：**补差额**买下这次成功（官方，第七版规则书 p.85「花费幸运值」，1:1 抵掉差值），
// 和**燃运重骰**烧固定点数整骰重来（规则书里没有这条，是村规——原文的重掷机制叫孤注一掷，
// 它不花幸运，且与花幸运二选一）。差得太多补不起时往往还烧得起，所以两个按钮各自独立出现。
//
// 这是「放水」唯一的正规出口——KP 暗中降难度一旦被察觉，整个骰子系统就不可信了；
// 幸运花的是玩家自己的资源、由玩家自己拍板，花掉多少明写在卡上。
//
// 这张卡**停住了整条结算链**（物品发货、线索记账、KP 续写都等着它），所以两个按钮
// 都得给得干脆：不买也要点一下「放弃」，不能让人以为可以放着不管。
import { GiRollingDices } from 'react-icons/gi'

export function LuckOfferCard({
  actor, skill, cost, rerollCost = 0, available, mine, busy, onDecide,
}: {
  actor: string
  skill: string
  /** 买下这次成功要花几点——刚好够翻盘，一点不多花。0 = 补不起或没开这条 */
  cost: number
  /** 烧掉整骰重掷一次要花几点（村规）。0 = 没开或烧不起 */
  rerollCost?: number
  /** 该角色当前的幸运值 */
  available: number
  /** 这一骰是不是我的（花的是我的幸运，只能我拍板） */
  mine: boolean
  busy?: boolean
  onDecide: (action: 'spend' | 'reroll' | 'give_up') => void
}) {
  return (
    <div data-tour="luck-offer" className="chat-msg py-1 flex justify-center">
      <div
        className="rounded-md px-3 py-2 text-sm"
        style={{
          background: 'var(--color-bg-tertiary)',
          borderLeft: '3px solid var(--color-dice-gold)',
          maxWidth: '100%',
        }}
      >
        <div className="flex items-center gap-3">
          <GiRollingDices
            style={{ color: 'var(--color-dice-gold)', fontSize: '1.1rem', flexShrink: 0 }}
          />
          <span className="whitespace-pre-wrap">
            {actor}的「{skill}」{cost ? `差 ${cost} 点` : '失败了'}
          </span>
          {mine && (
            <>
              {cost > 0 && (
                <button
                  onClick={() => onDecide('spend')}
                  disabled={busy}
                  className="btn-primary text-xs !px-2.5 !py-1 flex-shrink-0"
                  style={busy ? { opacity: 0.5 } : undefined}
                >
                  花 {cost} 点补上
                </button>
              )}
              {rerollCost > 0 && (
                <button
                  onClick={() => onDecide('reroll')}
                  disabled={busy}
                  className="btn-secondary text-xs !px-2.5 !py-1 flex-shrink-0"
                  style={busy ? { opacity: 0.5 } : undefined}
                >
                  烧 {rerollCost} 点重掷
                </button>
              )}
              <button
                onClick={() => onDecide('give_up')}
                disabled={busy}
                className="btn-secondary text-xs !px-2.5 !py-1 flex-shrink-0"
                style={busy ? { opacity: 0.5 } : undefined}
              >
                放弃
              </button>
            </>
          )}
        </div>
        <div className="text-xs mt-1.5 pl-[1.85rem]" style={{ color: 'var(--color-text-secondary)' }}>
          {mine
            ? [
                `当前幸运 ${available}`,
                cost > 0 ? `补上要花 ${cost}（买来的成功不计技能成长）` : '',
                // 重掷是买机会不是买成功，说清楚才不会有人以为烧了就稳过
                rerollCost > 0 ? `重掷要烧 ${rerollCost}，新骰点照单全收` : '',
              ].filter(Boolean).join('；') + '。'
            : `等 ${actor} 决定要不要动用幸运。`}
        </div>
      </div>
    </div>
  )
}
