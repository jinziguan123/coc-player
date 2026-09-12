/**
 * 战场（原型版）。
 *
 * 上一版把力气花错了地方：给一块空地面叠噪点、晕影、辉光，那是装饰不是设计。
 * 这张图上真正的**内容是令牌**——地面是空的，一格terrain都没有。内容又小又细又低对比，
 * 地面再怎么加特效也撑不起画面。
 *
 * 所以这一版反过来：**把大胆全花在令牌上，让地面彻底退后。**
 *
 * 令牌不再是「圆圈 + 姓名首字」。那是 Web 应用的头像占位符（Gmail 那种彩色字母圆），
 * 放在战棋图上必然别扭：它既不是人，也认不出是谁——名字首字撞车时更是认不出。
 * 改成真正的战棋微缩模型：
 *
 *     剪影      ← 站立的人形/异类轮廓，一眼看出「这是个东西」
 *    ╭────╮
 *   ( 底座 )    ← 俯视椭圆盘：阵营色描边 + 沿盘缘走的 HP 弧
 *    ╰────╯
 *     ▁▁▁       ← 落在地面上的投影
 *    山田健太    ← 名牌，写全名，不截首字
 *
 * 地面这边只留三样必要的：点阵 + 每 5 格一道重线（数距离用）、坐标尺（能念出「F4」）、
 * 一点很淡的明暗。噪点层已经去掉——它是上一版用来掩盖「画面没内容」的补丁。
 *
 * 独立于 components/game/combat/CombatGrid：那个还在正式战斗面板里跑着，定稿前不动它。
 */
import { useMemo } from 'react'
import { GiBrickWall, GiCrackedShield, GiShield } from 'react-icons/gi'

import { isOut, pctOf, TOKEN_STATUS_META } from '@/components/game/combat/meta'
import type { CombatGridInfo, Combatant } from '@/components/game/CombatStage'

import { defaultFigureKey, figureIcon, type FigureKey } from './figures'

const COL_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

/**
 * 战棋专用的敌我取色，**不走 meta.sideColor**。
 *
 * 那个函数给己方用 `--color-accent`、给敌方用 `--color-danger`，在暗色主题下是「琥珀 vs 血红」，
 * 分得很开；可到了羊皮纸主题，两个变量分别是 #8b2500 和 #8b0000——实测棋子上一个
 * rgb(139,37,0) 一个 rgb(139,0,0)，肉眼分不出敌我。在别处这只是配色偏好，在战棋图上
 * 是功能故障：分清敌我是这张图存在的理由。
 *
 * 所以这里按「冷 / 暖」分而不是按「两种红」分——冷暖对比在任何主题、任何尺寸下都读得出。
 * 具体色值在 lab.css 里按主题给，这里只决定用哪一档。
 */
function sideVar(c: Combatant): string {
  if (c.side === 'enemy') return 'var(--lbm-foe)'
  if (c.side === 'ally') return 'var(--lbm-ally)'
  return 'var(--lbm-friend)'
}

/** 一枚战棋：底座 + 剪影 + 名牌。cell 是格边长，整枚棋按它缩放。 */
function Mini({ c, cell, isTurn, isTarget, figure, onClick }: {
  c: Combatant
  cell: number
  isTurn: boolean
  isTarget: boolean
  figure: FigureKey
  onClick: () => void
}) {
  const out = isOut(c)
  const col = sideVar(c)
  const sm = c.status !== 'ok' ? TOKEN_STATUS_META[c.status] : null
  const Figure = figureIcon(figure)

  // 底座是俯视的椭圆盘，压扁到 0.42 是为了读成「躺在地面上」而不是「立着的圆」。
  const baseW = cell * 0.74
  const baseH = baseW * 0.42
  const figSize = cell * 0.66
  const showName = cell >= 42
  // 盘心离格底的高度。剪影的脚要正好落在盘心上——落在盘缘会看成悬空（第一版就是这样，
  // 脚底离盘心 5px，整个人浮在圈上方）。
  const baseMid = cell * 0.34

  return (
    <button
      className={`lbm-mini ${isTurn ? 'lbm-mini--turn' : ''} ${out ? 'lbm-mini--out' : ''} ${isTarget ? 'lbm-mini--target' : ''}`}
      style={{
        width: cell, height: cell,
        transform: `translate(${c.pos!.x * cell}px, ${c.pos!.y * cell}px)`,
        // 靠下的棋压住靠上的棋，与真实视角一致
        zIndex: 10 + c.pos!.y * 2 + (isTurn ? 1 : 0),
        color: col,
      }}
      onClick={onClick}
      title={`${c.name} · ${c.hp}/${c.max_hp}`}
    >
      {/* 剪影：脚落在盘心。drop-shadow 让它从地面上「离开」一点，不贴平。 */}
      <span className="lbm-mini-fig" style={{ bottom: baseMid }}>
        <Figure size={figSize} aria-hidden="true" />
      </span>

      {/* 底座：外圈是阵营色的盘缘，内圈那道弧是 HP。
          pathLength=100 把椭圆周长归一化，dasharray 直接写百分比，免去算椭圆周长。 */}
      <svg
        className="lbm-mini-base"
        width={baseW} height={baseH}
        viewBox={`0 0 ${baseW} ${baseH}`}
        style={{ bottom: baseMid - baseH / 2 }}
        aria-hidden="true"
      >
        <ellipse
          cx={baseW / 2} cy={baseH / 2} rx={baseW / 2 - 2} ry={baseH / 2 - 1.5}
          className="lbm-mini-base-fill"
        />
        <ellipse
          cx={baseW / 2} cy={baseH / 2} rx={baseW / 2 - 2} ry={baseH / 2 - 1.5}
          fill="none" stroke="currentColor" strokeWidth={1} opacity={0.45}
        />
        <ellipse
          cx={baseW / 2} cy={baseH / 2} rx={baseW / 2 - 2} ry={baseH / 2 - 1.5}
          fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round"
          pathLength={100} strokeDasharray={`${pctOf(c)} 100`}
          transform={`rotate(180 ${baseW / 2} ${baseH / 2})`}
        />
      </svg>

      {showName && (
        <span className="lbm-mini-name" style={{ bottom: baseMid - baseH / 2 - 12 }}>
          {c.name}
        </span>
      )}

      {sm && (
        <span className="lbm-mini-badge" style={{ color: sm.color, bottom: baseMid + figSize * 0.5 }} title={sm.label}>
          <sm.Icon size={Math.max(10, cell * 0.22)} />
        </span>
      )}
    </button>
  )
}

export function LabBattleMap({ grid, order, turn, targetId, cell, backdrop, backdropLevel = 'normal', figures, onPieceClick }: {
  grid: CombatGridInfo
  order: Combatant[]
  turn: string | null
  targetId: string
  cell: number
  /** 俯视战术底图；空串＝还没生成，退回纯色地面 */
  backdrop?: string
  /** 底图浓淡，由 KP 现场调（生成质量差异太大，见 useBattleBackdrop） */
  backdropLevel?: string
  /** 参战方 id → 剪影 key；缺项走阵营默认 */
  figures?: Record<string, FigureKey>
  onPieceClick: (c: Combatant) => void
}) {
  const live = order.filter((c) => c.pos && !isOut(c))

  // 灯心＝还站着的人的重心。战线往哪边推，光就跟到哪边。
  const light = useMemo(() => {
    if (!live.length) return { x: 50, y: 50 }
    const sx = live.reduce((s, c) => s + c.pos!.x + 0.5, 0) / live.length
    const sy = live.reduce((s, c) => s + c.pos!.y + 0.5, 0) / live.length
    return { x: (sx / grid.cols) * 100, y: (sy / grid.rows) * 100 }
  }, [live, grid.cols, grid.rows])

  const W = grid.cols * cell
  const H = grid.rows * cell
  const spread = Math.max(46, Math.min(92, (live.length > 1 ? 34 : 26) + live.length * 7))
  // 点阵跟着灯一起明暗，但远处不归零——网格是用来数距离的，全淡掉等于没有网格。
  const lightMask = `radial-gradient(ellipse ${spread}% ${spread * 1.25}% at ${light.x}% ${light.y}%, #000 30%, rgba(0,0,0,0.5) 65%, rgba(0,0,0,0.28) 100%)`

  return (
    <div className="lbm" style={{ ['--lbm-cell' as string]: `${cell}px` }}>
      <div className="lbm-corner" />
      <div className="lbm-ruler lbm-ruler--x" style={{ width: W }}>
        {Array.from({ length: grid.cols }, (_, i) => (
          <span key={i} style={{ width: cell }}>{COL_LETTERS[i] || i + 1}</span>
        ))}
      </div>
      <div className="lbm-ruler lbm-ruler--y" style={{ height: H }}>
        {Array.from({ length: grid.rows }, (_, i) => (
          <span key={i} style={{ height: cell }}>{i + 1}</span>
        ))}
      </div>

      <div className={`lbm-field ${backdrop ? 'lbm-field--art' : ''}`} style={{ width: W, height: H }}>
        {/* 底图：压暗 + 降饱和之后再铺。原图的对比度是按「看插画」调的，直接铺上去
            棋子会被花纹淹掉——底图在这里是背景，不是主角。 */}
        {backdrop && (
          <div
            className={`lbm-art lbm-art--${backdropLevel}`}
            style={{ backgroundImage: `url("${backdrop}")` }}
          />
        )}
        {/* 灯在下、格线在上：光落在地面上，格线画在地面上。反过来会把光心那片网格冲掉，
            正好冲掉最该看清的地方。 */}
        <div
          className="lbm-light"
          style={{ background: `radial-gradient(ellipse ${spread}% ${spread * 1.2}% at ${light.x}% ${light.y}%, var(--lbm-glow), transparent 70%)` }}
        />
        <div
          className="lbm-dots"
          style={{
            // 三层依次是：每 5 格一道竖重线、每 5 格一道横重线、每格交点一颗点
            backgroundSize: `${cell * 5}px 100%, 100% ${cell * 5}px, ${cell}px ${cell}px`,
            backgroundPosition: `0 0, 0 0, ${-cell / 2}px ${-cell / 2}px`,
            maskImage: lightMask,
            WebkitMaskImage: lightMask,
          }}
        />

        {(grid.blocked || []).map((k) => {
          const [x, y] = k.split(',').map(Number)
          return (
            <div key={`b${k}`} className="lbm-cell lbm-cell--blocked" title="障碍（阻挡移动与视线）"
              style={{ width: cell, height: cell, transform: `translate(${x * cell}px, ${y * cell}px)` }}>
              <GiBrickWall style={{ fontSize: cell * 0.5 }} />
            </div>
          )
        })}
        {Object.entries(grid.cover || {}).map(([k, kind]) => {
          const [x, y] = k.split(',').map(Number)
          const full = kind === 'full'
          const CoverIcon = full ? GiShield : GiCrackedShield
          return (
            <div key={`c${k}`} className={`lbm-cell lbm-cell--cover ${full ? 'lbm-cell--cover-full' : ''}`}
              title={full ? '全掩体（阻挡视线）' : '半掩体（射击 -1）'}
              style={{ width: cell, height: cell, transform: `translate(${x * cell}px, ${y * cell}px)` }}>
              <CoverIcon size={Math.max(11, cell * 0.2)} />
            </div>
          )
        })}

        {order.filter((c) => c.pos).map((c) => (
          <Mini
            key={c.id}
            c={c}
            cell={cell}
            isTurn={c.id === turn}
            isTarget={c.id === targetId}
            figure={figures?.[c.id] || defaultFigureKey(c.side)}
            onClick={() => onPieceClick(c)}
          />
        ))}

        <div className="lbm-vignette" />
      </div>
    </div>
  )
}
