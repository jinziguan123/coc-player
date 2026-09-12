// 方格战场：地面层（底图/灯光/点阵/坐标尺） + CSS-grid 格子层（地形/高亮）
// + 绝对定位令牌层（战棋，transform 补间滑动）。
//
// 令牌是**微缩模型**而不是头像：站立剪影 + 俯视底座（阵营色盘缘 + 沿盘缘的 HP 弧）+ 名牌。
// 曾经用「圆圈 + 姓名首字」，那是 Web 应用的头像占位符（Gmail 那种彩色字母圆）——放在战棋图上
// 既不像人，也认不出是谁（名字首字撞车时尤其）。剪影与底座的透视故意不一致：底座俯视、
// 小人平视，真实的战棋摆拍就是这样；强行统一成一个正圆就退回成了头像。
//
// 地面那几层解决的是另一个问题：一张 12×8 的网格里九成格子是空的，均匀实线画满整幅时
// 读起来像表格。改成交点点阵 + 每 5 格一道重线（数距离用）+ 一盏跟着人走的灯，
// 空格的视觉重量就降了一个数量级。坐标尺让网格线真正有用——KP 能直接说「挪到 F4」。
//
// 移动模式下高亮可达格（Chebyshev ≤ 剩余移动力、避开占用/障碍），点格移动；点敌方令牌选目标。
// 纯 --color-* 变量，gothic/parchment 两主题自适应，不引第三方战棋库。
import { useMemo } from 'react'
import { GiBrickWall, GiShield, GiCrackedShield, GiCrosshair, GiFlame } from 'react-icons/gi'
import type { Combatant, CombatGridInfo, HpDiff } from './types'
import { TOKEN_STATUS_META, isOut, pctOf, sideColor } from './meta'
import { figureOf } from './figures'
import { useDeathFx, usePrefersReducedMotion } from './useHpDiff'

const DEFAULT_CELL = 46   // 单格边长（px）——嵌入面板的战棋格；沉浸布局经 cell prop 放大（至多 64）
const COL_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

/** 沿底座盘缘走的 HP 弧。pathLength=100 把椭圆周长归一化，免去算椭圆周长。 */
function BaseDisc({ w, h, pct }: { w: number; h: number; pct: number }) {
  const rx = w / 2 - 2
  const ry = h / 2 - 1.5
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="mini-base" aria-hidden="true">
      <ellipse cx={w / 2} cy={h / 2} rx={rx} ry={ry} className="mini-base-fill" />
      <ellipse cx={w / 2} cy={h / 2} rx={rx} ry={ry}
        fill="none" stroke="currentColor" strokeWidth={1} opacity={0.45} />
      <ellipse cx={w / 2} cy={h / 2} rx={rx} ry={ry}
        fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round"
        pathLength={100} strokeDasharray={`${pct} 100`}
        transform={`rotate(180 ${w / 2} ${h / 2})`} />
    </svg>
  )
}

// 单个战棋令牌：外层 wrapper 只管棋盘定位（transform 补间），内层 fx 壳播抖动/死亡动画，
// 两层 transform 分离，滑动补间与受击抖动互不打架。
function CombatToken({ c, isTurn, isTarget, moveActive, reduced, diff, dieSeq, cell, onClick }: {
  c: Combatant
  isTurn: boolean
  isTarget: boolean
  moveActive: boolean
  reduced: boolean
  diff?: HpDiff
  dieSeq?: number
  cell: number
  onClick: () => void
}) {
  const CELL = cell
  const out = isOut(c)
  const col = sideColor(c)
  const pct = pctOf(c)
  const dmg = diff && diff.delta < 0
  const sm = c.status !== 'ok' ? TOKEN_STATUS_META[c.status] : null
  const burning = !out && (c.conditions || []).includes('burning')
  // fx 壳的动画类与 key：死亡瞬间播 token-die；掉血播 token-hit（key 换才会重播）。
  const fxClass = dieSeq ? 'token-die' : dmg ? 'token-hit' : ''
  const fxKey = `${dieSeq ?? 0}-${diff?.seq ?? 0}`
  const Figure = figureOf(c)
  // 底座压扁到 0.42 才读成「躺在地面上」；剪影的脚落在**盘心**而不是盘缘，
  // 落在盘缘会看成整个人浮在圈上方。
  const baseW = cell * 0.74
  const baseH = baseW * 0.42
  const figSize = cell * 0.66
  const baseMid = cell * 0.34
  const showName = cell >= 42

  return (
    <div
      style={{
        position: 'absolute', left: 0, top: 0, width: CELL, height: CELL,
        transform: `translate(${c.pos!.x * CELL}px, ${c.pos!.y * CELL}px)`,
        transition: reduced ? 'none' : 'transform 0.35s ease',
        zIndex: isTurn ? 3 : 2,
        pointerEvents: 'none',
      }}
    >
      {/* 棋盘上的伤害/治疗飘字（key=seq 播一次） */}
      {diff && diff.delta !== 0 && (
        <span key={diff.seq} className={`token-float ${dmg ? 'token-float--dmg' : 'token-float--heal'}`}>
          {diff.delta > 0 ? `+${diff.delta}` : diff.delta}
        </span>
      )}
      <div key={fxKey} className={fxClass} style={{ width: '100%', height: '100%' }}>
        <button
          onClick={onClick}
          title={`${c.name} ${c.hp}/${c.max_hp}`}
          className={`mini ${isTurn && !out ? 'mini--turn' : ''} ${out ? 'mini--out' : ''}`}
          style={{
            width: '100%', height: '100%', padding: 0,
            color: col,
            cursor: moveActive ? 'default' : 'pointer',
            pointerEvents: 'auto',
          }}
        >
          {/* 剪影：脚落在盘心，两层投影让它「站在」地面上而不是贴平 */}
          <span className="mini-fig" style={{ bottom: baseMid }}>
            <Figure size={figSize} aria-hidden="true" />
          </span>
          <span className="mini-base-wrap" style={{ bottom: baseMid - baseH / 2 }}>
            <BaseDisc w={baseW} h={baseH} pct={pct} />
          </span>
          {showName && (
            <span className="mini-name" style={{ bottom: baseMid - baseH / 2 - 12 }}>{c.name}</span>
          )}
        </button>
      </div>
      {/* 状态小徽标（右上角）：dead/dying/unconscious/major_wound/fled */}
      {sm && (
        <span title={sm.label} style={{
          position: 'absolute', top: -3, right: -1, width: 15, height: 15, borderRadius: '50%',
          background: 'var(--color-bg-primary)', border: `1px solid ${sm.color}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 4,
        }}>
          <sm.Icon size={9} style={{ color: sm.color }} />
        </span>
      )}
      {/* 着火条件徽标（左上角） */}
      {burning && (
        <span title="着火" style={{
          position: 'absolute', top: -3, left: -1, width: 15, height: 15, borderRadius: '50%',
          background: 'var(--color-bg-primary)', border: '1px solid var(--color-danger)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 4,
        }}>
          <GiFlame size={9} style={{ color: 'var(--color-danger)' }} />
        </span>
      )}
      {/* 被选为攻击目标：叠准星标记（替代红描边） */}
      {isTarget && !out && (
        <GiCrosshair className="token-target" style={{
          position: 'absolute', left: '50%', top: '50%', width: CELL * 0.92, height: CELL * 0.92,
          color: 'var(--color-danger)', zIndex: 5, pointerEvents: 'none',
        }} />
      )}
    </div>
  )
}

export function CombatGrid({ grid, order, turn, myCharId, moveActive, budget, dash, targetId, fx, onCellMove, onPieceClick, cell = DEFAULT_CELL, backdrop = '', backdropLevel = 'normal' }: {
  grid: CombatGridInfo
  order: Combatant[]
  turn: string | null
  myCharId: string | null
  moveActive: boolean
  budget: number
  dash: boolean
  targetId: string
  fx: Record<string, HpDiff>   // useHpDiff 产出（与右侧卡片共用），驱动令牌受击/飘字
  onCellMove: (x: number, y: number) => void
  onPieceClick: (c: Combatant) => void
  cell?: number                // 单格边长（px）：沉浸布局按容器自适应放大（46~64）
  /** 俯视战术底图（world_state.battle_maps，经 /locations 下发）；空＝纯色地面 */
  backdrop?: string
  /** 底图浓淡 faint/normal/strong：生成质量差异极大，一个常数伺候不了所有图 */
  backdropLevel?: string
}) {
  const CELL = cell
  const reduced = usePrefersReducedMotion()
  const deathFx = useDeathFx(order)
  // 灯心＝还站着的人的重心：战线往哪边推，光就跟到哪边，KP 该先看的地方永远最亮。
  const live = order.filter((c) => c.pos && !isOut(c))
  const light = useMemo(() => {
    if (!live.length) return { x: 50, y: 50 }
    const sx = live.reduce((s, c) => s + c.pos!.x + 0.5, 0) / live.length
    const sy = live.reduce((s, c) => s + c.pos!.y + 0.5, 0) / live.length
    return { x: (sx / grid.cols) * 100, y: (sy / grid.rows) * 100 }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- live 每次渲染新建数组，按内容取依赖
  }, [order, grid.cols, grid.rows])
  const lightSpread = Math.max(46, Math.min(92, (live.length > 1 ? 34 : 26) + live.length * 7))
  // 点阵跟着灯明暗，但远处**不归零**——网格是用来数距离的，全淡掉就等于没有网格。
  const lightMask = `radial-gradient(ellipse ${lightSpread}% ${lightSpread * 1.25}% at ${light.x}% ${light.y}%, #000 30%, rgba(0,0,0,0.5) 65%, rgba(0,0,0,0.28) 100%)`
  const me = myCharId ? order.find((c) => c.id === myCharId) : null
  const occupied = new Set(order.filter((c) => c.pos && !isOut(c)).map((c) => `${c.pos!.x},${c.pos!.y}`))
  const blocked = new Set(grid.blocked || [])
  const reach = new Set<string>()
  const threat = new Set<string>()   // 与存活敌方相邻的格：移动到此会进入近战/被夹击
  if (moveActive && me?.pos && budget > 0) {
    const b = budget
    for (let y = 0; y < grid.rows; y++) {
      for (let x = 0; x < grid.cols; x++) {
        const k = `${x},${y}`
        if (k === `${me.pos.x},${me.pos.y}` || occupied.has(k) || blocked.has(k)) continue
        if (Math.max(Math.abs(x - me.pos.x), Math.abs(y - me.pos.y)) <= b) reach.add(k)
      }
    }
    const meEnemyCamp = me.side === 'enemy'
    for (const f of order) {
      if (!f.pos || isOut(f) || (f.side === 'enemy') === meEnemyCamp) continue
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        const nx = f.pos.x + dx, ny = f.pos.y + dy
        if (nx >= 0 && nx < grid.cols && ny >= 0 && ny < grid.rows) threat.add(`${nx},${ny}`)
      }
    }
  }
  return (
    <div className="overflow-x-auto py-1">
      <div className="combat-field-wrap" style={{ ['--cf-cell' as string]: `${CELL}px` }}>
        <div className="combat-ruler-corner" />
        <div className="combat-ruler combat-ruler--x" style={{ width: grid.cols * CELL }}>
          {Array.from({ length: grid.cols }, (_, i) => (
            <span key={i} style={{ width: CELL }}>{COL_LETTERS[i] || i + 1}</span>
          ))}
        </div>
        <div className="combat-ruler combat-ruler--y" style={{ height: grid.rows * CELL }}>
          {Array.from({ length: grid.rows }, (_, i) => (
            <span key={i} style={{ height: CELL }}>{i + 1}</span>
          ))}
        </div>

      <div className={`combat-field ${backdrop ? 'combat-field--art' : ''}`} style={{
        width: grid.cols * CELL, height: grid.rows * CELL,
        display: 'grid',
        gridTemplateColumns: `repeat(${grid.cols}, ${CELL}px)`,
        gridTemplateRows: `repeat(${grid.rows}, ${CELL}px)`,
      }}>
        {/* 地面三层，顺序有讲究：底图 → 灯 → 格线。
            灯压在格线上会把光心那一片网格冲掉，而那正是最该看清的地方。 */}
        {backdrop && (
          <div className={`combat-art combat-art--${backdropLevel}`}
            style={{ backgroundImage: `url("${backdrop}")` }} />
        )}
        <div className="combat-light" style={{
          background: `radial-gradient(ellipse ${lightSpread}% ${lightSpread * 1.2}% at ${light.x}% ${light.y}%, var(--cf-glow), transparent 70%)`,
        }} />
        <div className="combat-dots" style={{
          // 三层：每 5 格一道竖重线、每 5 格一道横重线、每格交点一颗点。
          // 重线是战棋垫的老做法——数格子以 5 为一段，比一格一格数快得多。
          backgroundSize: `${CELL * 5}px 100%, 100% ${CELL * 5}px, ${CELL}px ${CELL}px`,
          backgroundPosition: `0 0, 0 0, ${-CELL / 2}px ${-CELL / 2}px`,
          maskImage: lightMask, WebkitMaskImage: lightMask,
        }} />

        {(grid.blocked || []).map((k) => {
          const [x, y] = k.split(',').map(Number)
          return (
            <div key={`b${k}`} title="障碍（阻挡移动与视线）"
              style={{ gridColumn: x + 1, gridRow: y + 1, background: 'var(--color-bg-tertiary)',
                boxShadow: 'inset 0 0 0 1px var(--color-border-strong)',
                display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <GiBrickWall style={{ color: 'var(--color-text-secondary)', fontSize: CELL * 0.55 }} />
            </div>
          )
        })}
        {Object.entries(grid.cover || {}).map(([k, kind]) => {
          const [x, y] = k.split(',').map(Number)
          const full = kind === 'full'
          const CoverIcon = full ? GiShield : GiCrackedShield
          return (
            <div key={`c${k}`} title={full ? '全掩体（阻挡视线）' : '半掩体（射击 -1）'}
              style={{ gridColumn: x + 1, gridRow: y + 1, position: 'relative',
                background: `repeating-linear-gradient(45deg, color-mix(in srgb, var(--color-text-secondary) ${full ? 40 : 22}%, transparent) 0 3px, transparent 3px 6px)`,
                boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--color-border-strong) 60%, transparent)' }}>
              {/* 掩体等级角标：全掩体完整盾 / 半掩体裂纹盾 */}
              <CoverIcon size={12} style={{
                position: 'absolute', right: 2, bottom: 2, opacity: 0.85,
                color: full ? 'var(--color-text-secondary)' : 'color-mix(in srgb, var(--color-text-secondary) 75%, transparent)',
              }} />
            </div>
          )
        })}
        {[...reach].map((k) => {
          const [x, y] = k.split(',').map(Number)
          return (
            <button key={`r${k}`} onClick={() => onCellMove(x, y)}
              title={threat.has(k) ? (dash ? '冲刺到此格（进入敌方近战范围、独占本回合）' : '移动到此格（进入敌方近战范围）')
                : (dash ? '冲刺到此格（独占本回合）' : '移动到此格')}
              style={{ gridColumn: x + 1, gridRow: y + 1, border: 'none', cursor: 'pointer',
                background: threat.has(k)
                  ? 'color-mix(in srgb, var(--color-danger) 26%, transparent)'
                  : dash
                    ? 'color-mix(in srgb, var(--color-danger) 13%, transparent)'
                    : 'color-mix(in srgb, var(--color-accent) 22%, transparent)' }} />
          )
        })}
        {/* 令牌层：绝对定位 + transform 补间，pos 变化即平滑滑动 */}
        {order.filter((c) => c.pos).map((c) => (
          <CombatToken
            key={c.id}
            c={c}
            isTurn={c.id === turn}
            isTarget={c.id === targetId}
            moveActive={moveActive}
            reduced={reduced}
            diff={fx[c.id]}
            dieSeq={deathFx[c.id]}
            cell={CELL}
            onClick={() => onPieceClick(c)}
          />
        ))}
        <div className="combat-vignette" />
      </div>
      </div>
    </div>
  )
}
