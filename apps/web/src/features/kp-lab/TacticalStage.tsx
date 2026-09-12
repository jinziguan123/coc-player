/**
 * 战术台：先攻序（竖）+ 战场（大）+ 行动者面板（右）。
 *
 * 重做的三个判断：
 *  1. **战斗不该是叙事流里的一张卡**。现状下它和普通消息抢同一条竖向空间，于是棋盘只能
 *     被压成一小块。战斗时 KP 的注意力整个转向空间和数值，那就把整个台面让给它。
 *  2. **先攻序改竖排**。它本来就是一个序列，竖向读天然就是时间顺序，而且一行放得下
 *     名字、血量和状态；横排芯片里这些一样都塞不下，先攻轨于是退化成了装饰。
 *  3. **当前行动者要有自己的面板**。「轮到谁」和「他能做什么」应该挨着，而不是让 KP
 *     先在芯片里找到高亮的那个，再去下面的表单里重新选一遍人。
 */
import { useEffect, useRef, useState } from 'react'
import { GiCrossedSwords, GiRun, GiShield, GiRollingDices, GiTreasureMap } from 'react-icons/gi'

import { isOut, sideColor, STATUS_META } from '@/components/game/combat/meta'
import type { CombatState, Combatant } from '@/components/game/CombatStage'

import { defaultFigureKey, figureIcon, type FigureKey } from './figures'
import { FigurePicker } from './FigurePicker'
import { LabBattleMap } from './LabBattleMap'
import { BACKDROP_LEVELS, useBattleBackdrop } from './useBattleBackdrop'

/**
 * 按容器实际尺寸算单格边长。写死格长的代价实测过：面板有七百多像素宽时棋盘只画四百多，
 * 右边空着三分之一还嫌它小——格子不看容器，容器再宽也白搭。
 *
 * RESERVE 是留给坐标尺的那一圈。少了它，算出来的格长会让「网格 + 坐标尺」整体比容器宽，
 * 而 .lbm 是 grid 容器不会自己缩，于是整张图撑出去压在右侧面板上——实测溢出 12px，
 * 行动者卡被盖掉半张。
 */
const RULER_RESERVE_X = 26   // 纵向坐标尺宽 + 间距
const RULER_RESERVE_Y = 24   // 横向坐标尺高 + 间距

function useFitCell(cols: number, rows: number, min = 22, max = 96) {
  const ref = useRef<HTMLDivElement>(null)
  const [cell, setCell] = useState(min)
  useEffect(() => {
    const el = ref.current
    if (!el || !cols || !rows) return
    const measure = () => {
      const { width, height } = el.getBoundingClientRect()
      if (!width || !height) return
      const w = width - RULER_RESERVE_X
      const h = height - RULER_RESERVE_Y
      setCell(Math.max(min, Math.min(max, Math.floor(Math.min(w / cols, h / rows)))))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [cols, rows, min, max])
  return { ref, cell }
}

const ACTOR_ACTS = [
  { key: 'attack', label: '攻击', Icon: GiCrossedSwords },
  { key: 'move', label: '移动', Icon: GiRun },
  { key: 'dodge', label: '闪避', Icon: GiShield },
  { key: 'roll', label: '掷骰', Icon: GiRollingDices },
] as const

function InitiativeRail({ order, turn, onPick, selectedId }: {
  order: Combatant[]
  turn: string | null
  onPick: (c: Combatant) => void
  selectedId: string
}) {
  const turnIdx = order.findIndex((c) => c.id === turn)
  // 「下一个」＝当前之后第一个未出局者（环形）。先攻序里这是 KP 最常问的一句话。
  let nextIdx = -1
  if (turnIdx >= 0) {
    for (let k = 1; k <= order.length; k++) {
      const i = (turnIdx + k) % order.length
      if (!isOut(order[i])) { nextIdx = i; break }
    }
  }
  return (
    <div className="kl-init">
      <div className="kl-section-label mb-0.5">先攻序</div>
      {order.map((c, i) => {
        const out = isOut(c)
        const active = c.id === turn
        const passed = turnIdx >= 0 && i < turnIdx && !active
        const cls = ['kl-init-row',
          active ? 'kl-init-row--active' : '',
          out ? 'kl-init-row--out' : passed ? 'kl-init-row--passed' : '',
          selectedId === c.id && !active ? 'ring-1' : ''].join(' ')
        return (
          <button key={c.id} className={cls} onClick={() => onPick(c)}>
            <span className="kl-dot" style={{ background: sideColor(c) }} />
            <span className="kl-init-name">{c.name}</span>
            {i === nextIdx && <span className="kl-init-next">下一个</span>}
            <span className="kl-init-hp">{c.hp}/{c.max_hp}</span>
          </button>
        )
      })}
    </div>
  )
}

export function TacticalStage({
  combat, selectedId, onSelect, onAct, rolls,
  sessionId, sceneName, moduleTitle, figures, onFigureChange,
}: {
  combat: CombatState
  selectedId: string
  onSelect: (id: string) => void
  onAct: (label: string, who: string) => void
  /** 最近几条结算行。棋盘按长宽比取边长，宽屏下底下必然剩一条空带——
   *  与其留白，不如放 KP 每一击之后都要确认的那句话。 */
  rolls: string[]
  sessionId?: string
  sceneName: string
  moduleTitle: string
  figures: Record<string, FigureKey>
  onFigureChange: (combatantId: string, key: FigureKey) => void
}) {
  const order = combat.order
  const active = order.find((c) => c.id === combat.turn) || null
  const focus = order.find((c) => c.id === selectedId) || active
  const { ref: boardRef, cell } = useFitCell(combat.grid?.cols ?? 0, combat.grid?.rows ?? 0)
  const backdrop = useBattleBackdrop(sessionId, sceneName, moduleTitle)
  const [picking, setPicking] = useState(false)
  useEffect(() => { setPicking(false) }, [selectedId, combat.turn])
  // side 有 player / ally / enemy 三个值，「敌我」不等于「side 不同」——
  // player 与 ally 分属不同 side 却是同一边，照「不同即敌」算会把队友列成攻击目标。
  const targets = focus
    ? order.filter((c) => !isOut(c) && (focus.side === 'enemy' ? c.side !== 'enemy' : c.side === 'enemy'))
    : []

  return (
    <div className="kl-tac">
      <InitiativeRail
        order={order}
        turn={combat.turn}
        selectedId={selectedId}
        onPick={(c) => onSelect(c.id)}
      />

      <div className="kl-board-col">
      <div className="kl-board" ref={boardRef}>
        {combat.grid ? (
          <LabBattleMap
            grid={combat.grid}
            order={order}
            turn={combat.turn}
            targetId={selectedId}
            cell={cell}
            backdrop={backdrop.url}
            backdropLevel={backdrop.level}
            figures={figures}
            onPieceClick={(c) => onSelect(c.id)}
          />
        ) : (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>这一场没有方格战场。</p>
        )}
      </div>
        <div className="kl-rolls">
          <div className="kl-section-label mb-1">
            结算回放
            {/* 底图入口放这儿而不是顶栏：它是这块棋盘的属性，跟着棋盘走。
                走 preview 端点——只生成不落库，不会写进正在跑的团。 */}
            <span className="ml-auto flex items-center gap-1">
              {backdrop.url && (
                <span className="kl-levels">
                  {BACKDROP_LEVELS.map((l) => (
                    <button
                      key={l.key}
                      className={`kl-level ${backdrop.level === l.key ? 'kl-level--on' : ''}`}
                      onClick={() => backdrop.setLevel(l.key)}
                      title={`底图浓淡：${l.label}`}
                    >
                      {l.label}
                    </button>
                  ))}
                </span>
              )}
              <button
                className="kl-chip !py-0.5"
                onClick={backdrop.url ? backdrop.clear : backdrop.generate}
                disabled={backdrop.busy}
                title={backdrop.url ? '换回纯色地面' : '按当前场景生成一张俯视战术底图（只生成，不写进存档）'}
              >
                <GiTreasureMap size={12} aria-hidden="true" />
                {backdrop.busy ? '正在出图…' : backdrop.url ? '去掉底图' : '生成战场底图'}
              </button>
            </span>
          </div>
          {backdrop.error && (
            <div className="kl-roll-line" style={{ color: 'var(--color-danger)', whiteSpace: 'normal' }}>
              {backdrop.error}
            </div>
          )}
          {rolls.map((r, i) => (
            <div key={i} className="kl-roll-line" style={{ opacity: 1 - i * 0.22 }}>{r}</div>
          ))}
        </div>
      </div>

      <div className="kl-actor">
        <div className="kl-section-label">
          <span className="kl-round">
            第<span className="kl-round-num">{combat.round}</span>轮
          </span>
        </div>

        {focus ? (
          <>
            <div className="kl-actor-card">
              <div className="kl-actor-name" style={{ color: sideColor(focus) }}>{focus.name}</div>
              <div className="kl-actor-sub">
                {focus.id === combat.turn ? '正在行动' : '待命'}
                {focus.status !== 'ok' && ` · ${STATUS_META[focus.status].label}`}
              </div>
              <div className="mt-2 flex items-baseline gap-1">
                <span className="font-mono text-lg" style={{ color: 'var(--color-text-accent)' }}>{focus.hp}</span>
                <span className="font-mono text-xs" style={{ color: 'var(--color-text-secondary)' }}>/{focus.max_hp} HP</span>
              </div>
              {focus.weapon && (
                <div className="kl-actor-sub mt-1">武器 · {focus.weapon}</div>
              )}

              {/* 轮廓：机器猜不出「不明存在」长什么样，这一下由 KP 点。 */}
              <div className="kl-figrow">
                {(() => {
                  const key = figures[focus.id] || defaultFigureKey(focus.side)
                  const Icon = figureIcon(key)
                  return (
                    <button className="kl-figrow-btn" onClick={() => setPicking((v) => !v)}>
                      <Icon size={15} aria-hidden="true" />
                      <span>换轮廓</span>
                    </button>
                  )
                })()}
                {picking && (
                  <FigurePicker
                    name={focus.name}
                    current={figures[focus.id] || defaultFigureKey(focus.side)}
                    onPick={(k) => { onFigureChange(focus.id, k); setPicking(false) }}
                    onClose={() => setPicking(false)}
                  />
                )}
              </div>
            </div>

            <div className="kl-actor-acts">
              {ACTOR_ACTS.map(({ key, label, Icon }) => (
                <button key={key} className="kl-actor-act" onClick={() => onAct(label, focus.name)}>
                  <Icon size={12} aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>

            {/* 目标平铺而不是塞进下拉：战斗里「打谁」是每一轮都要做的选择，
                它值一个常驻列表——顺带把各人剩多少血一并摆出来，省一次回看。 */}
            <div className="kl-section-label mt-1">目标</div>
            <div className="flex flex-col gap-1">
              {targets.map((c) => (
                <button key={c.id} className="kl-target" onClick={() => onAct(`打 ${c.name}`, focus.name)}>
                  <span className="kl-dot" style={{ background: sideColor(c) }} />
                  <span className="flex-1 truncate text-left">{c.name}</span>
                  <span className="kl-init-hp">{c.hp}/{c.max_hp}</span>
                </button>
              ))}
              {targets.length === 0 && <p className="kl-brief-empty">对面没有还站着的人。</p>}
            </div>
          </>
        ) : (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>先在先攻序里选一个人。</p>
        )}
      </div>
    </div>
  )
}
