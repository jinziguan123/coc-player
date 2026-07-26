// 单张参战方卡片：名字 + HP 条/数字 + 状态/条件徽标 + 武器，带掉血/回血动画。
import { GiShield, GiCrosshair, GiDeathSkull } from 'react-icons/gi'
import type { Combatant, HpDiff } from './types'
import { CONDITION_META, STATUS_META, isOut, pctOf } from './meta'

export function CombatantCard({ c, mine, active, diff }: {
  c: Combatant
  mine: boolean
  active: boolean
  diff?: HpDiff
}) {
  const out = isOut(c)
  const hpColor = c.side === 'enemy' ? 'var(--color-danger)' : 'var(--color-accent)'
  const sm = c.status !== 'ok' ? STATUS_META[c.status] : null
  const conds = (c.conditions || []).filter((k) => CONDITION_META[k])
  // 动画类：delta<0 掉血（红闪+抖）、>0 回血（绿涨）。用 seq 做 key 让连续同向变化也重播。
  const dmg = diff && diff.delta < 0
  const heal = diff && diff.delta > 0

  const hpPct = pctOf(c)
  // 血量档位：过半安稳、三成内告警。颜色只在敌我基色与告警色之间切，避免满屏红。
  const critical = !out && hpPct < 30

  return (
    <div
      className={`combatant-card relative rounded ${dmg ? 'hp-hit' : ''} ${active ? 'combatant-card--active' : ''}`}
      style={{
        opacity: out ? 0.42 : 1,
        filter: out ? 'grayscale(0.7)' : 'none',
        background: active ? 'var(--surface-3)' : 'var(--surface-1)',
        border: active ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
        boxShadow: active ? '0 0 10px color-mix(in srgb, var(--color-accent) 34%, transparent)' : 'none',
        // 左侧阵营色带：不占空间就把敌我分开
        ['--side-color' as string]: hpColor,
      }}
      title={`${c.name} · ${c.hp}/${c.max_hp}`}
    >
      {/* 浮动伤害/治疗数字（key=seq 触发一次动画） */}
      {diff && (diff.delta !== 0) && (
        <span key={diff.seq} className={`hp-float ${dmg ? 'hp-float--dmg' : 'hp-float--heal'}`}>
          {diff.delta > 0 ? `+${diff.delta}` : diff.delta}
        </span>
      )}
      <div className="px-2.5 py-2">
        <div className="flex items-baseline gap-1.5 mb-1">
          {out && c.status === 'dead' && <GiDeathSkull size={12} style={{ color: 'var(--color-danger-deep)', flexShrink: 0, alignSelf: 'center' }} />}
          <span
            className="truncate font-semibold"
            style={{
              fontSize: 'var(--text-sm)',
              color: mine ? 'var(--color-text-accent)' : 'var(--color-text-primary)',
            }}
          >
            {c.name}{mine ? '（我）' : ''}
          </span>
          {/* 血量升为卡片第二主角：等宽、右对齐、告警时转血红 */}
          <span
            className="ml-auto flex-shrink-0 font-mono tabular-nums font-bold"
            style={{
              fontSize: 'var(--text-sm)',
              color: critical ? 'var(--color-danger)' : 'var(--color-text-primary)',
            }}
          >
            {c.hp}
            <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)', fontWeight: 400 }}>
              /{c.max_hp}
            </span>
          </span>
        </div>
        {/* 血条：底层填充始终平滑过渡宽度（不换 key，保住 transition:width）；
            红闪/绿涨的颜色脉冲另起一层叠加，只有它带 seq key 重挂、播一次动画 → 宽度不瞬跳。 */}
        <div className="relative h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-sunken)' }}>
          <div className="stat-bar-fill h-full" style={{ width: `${hpPct}%`, background: critical ? 'var(--color-danger)' : hpColor }} />
          {(dmg || heal) && (
            <div
              key={diff?.seq}
              className={`stat-bar-fill absolute inset-y-0 left-0 h-full ${dmg ? 'hp-bar-dmg' : 'hp-bar-heal'}`}
              style={{ width: `${hpPct}%`, background: hpColor }}
            />
          )}
        </div>
        {(sm || (!!c.armor && c.armor > 0 && !out) || (c.aim && !out) || conds.length > 0) && (
          <div className="flex items-center gap-1 flex-wrap mt-1.5">
            {sm && <span className="chip" style={{ color: sm.color, borderColor: sm.color }}>{sm.label}</span>}
            {!!c.armor && c.armor > 0 && !out && (
              <span className="chip" title={`护甲 ${c.armor}：每次物理伤害先扣 ${c.armor} 点`}>
                <GiShield size={10} /> {c.armor}
              </span>
            )}
            {c.aim && !out && (
              <span className="chip chip--accent"><GiCrosshair size={10} /> 瞄准</span>
            )}
            {conds.map((k) => {
              const { label, Icon } = CONDITION_META[k]
              return (
                <span key={k} className="chip chip--danger">
                  <Icon size={10} /> {label}
                </span>
              )
            })}
          </div>
        )}
        {c.weapon && (
          <div className="truncate mt-1" style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)', opacity: 0.8 }}>
            {c.weapon}
          </div>
        )}
      </div>
    </div>
  )
}
