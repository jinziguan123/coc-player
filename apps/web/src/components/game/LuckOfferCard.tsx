// 幸运消费询价卡：检定差一点点时，问这一骰的主人要不要花幸运买回来。
//
// 这是「放水」唯一的正规出口——KP 暗中降难度一旦被察觉，整个骰子系统就不可信了；
// 幸运花的是玩家自己的资源、由玩家自己拍板，花掉多少明写在卡上。
//
// 这张卡**停住了整条结算链**（物品发货、线索记账、KP 续写都等着它），所以两个按钮
// 都得给得干脆：不买也要点一下「认了」，不能让人以为可以放着不管。
import { GiRollingDices } from 'react-icons/gi'

export function LuckOfferCard({
  actor, skill, cost, available, mine, busy, onDecide,
}: {
  actor: string
  skill: string
  /** 买下这次成功要花几点——刚好够翻盘，一点不多花 */
  cost: number
  /** 该角色当前的幸运值 */
  available: number
  /** 这一骰是不是我的（花的是我的幸运，只能我拍板） */
  mine: boolean
  busy?: boolean
  onDecide: (spend: boolean) => void
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
            {actor}的「{skill}」差 {cost} 点
          </span>
          {mine && (
            <>
              <button
                onClick={() => onDecide(true)}
                disabled={busy}
                className="btn-primary text-xs !px-2.5 !py-1 flex-shrink-0"
                style={busy ? { opacity: 0.5 } : undefined}
              >
                花 {cost} 点幸运
              </button>
              <button
                onClick={() => onDecide(false)}
                disabled={busy}
                className="btn-secondary text-xs !px-2.5 !py-1 flex-shrink-0"
                style={busy ? { opacity: 0.5 } : undefined}
              >
                认了
              </button>
            </>
          )}
        </div>
        <div className="text-xs mt-1.5 pl-[1.85rem]" style={{ color: 'var(--color-text-secondary)' }}>
          {mine
            ? `当前幸运 ${available}，花掉后剩 ${available - cost}；买来的成功不计技能成长。`
            : `等 ${actor} 决定要不要动用幸运。`}
        </div>
      </div>
    </div>
  )
}
