import { useState, useRef, useEffect } from 'react'

interface VitalMeta {
  label: string
  sublabel: string
  toneClass: 'hp' | 'san' | 'mp'
  alertLabel?: string
  isLow: (pct: number) => boolean
  tooltip: (current: number, max: number, pct: number) => string
}

const VITAL_CONFIG: Record<'hp' | 'san' | 'mp', VitalMeta> = {
  hp: {
    label: 'HP',
    sublabel: '生命',
    toneClass: 'hp',
    alertLabel: '危',
    isLow: (pct) => pct < 30,
    tooltip: (current, max, pct) =>
      pct <= 0
        ? `生命值 (HP): 0/${max} · 濒死或失去意识`
        : pct < 30
          ? `生命值 (HP): ${current}/${max} (${Math.round(pct)}%) · 重伤濒危`
          : `生命值 (HP): ${current}/${max} (${Math.round(pct)}%) · 状态良好`,
  },
  san: {
    label: 'SAN',
    sublabel: '理智',
    toneClass: 'san',
    alertLabel: '狂',
    isLow: (pct) => pct < 30,
    tooltip: (current, max, pct) =>
      pct <= 0
        ? `理智值 (SAN): 0/${max} · 永久疯狂`
        : pct < 30
          ? `理智值 (SAN): ${current}/${max} (${Math.round(pct)}%) · 精神崩溃边缘`
          : pct < 50
            ? `理智值 (SAN): ${current}/${max} (${Math.round(pct)}%) · 心智动摇`
            : `理智值 (SAN): ${current}/${max} (${Math.round(pct)}%) · 理智稳定`,
  },
  mp: {
    label: 'MP',
    sublabel: '魔法',
    toneClass: 'mp',
    alertLabel: '竭',
    isLow: (pct) => pct < 20,
    tooltip: (current, max, pct) =>
      pct <= 0
        ? `魔法值 (MP): 0/${max} · 法力枯竭`
        : pct < 20
          ? `魔法值 (MP): ${current}/${max} (${Math.round(pct)}%) · 法力匮乏`
          : `魔法值 (MP): ${current}/${max} (${Math.round(pct)}%) · 充沛`,
  },
}

export interface GameVitalBarProps {
  type: 'hp' | 'san' | 'mp'
  current: number
  max: number
}

/** 羊皮纸/暗黑哥特风 HUD 属性条：素雅刻度凹槽 + 受击残影 + 原生主题深色填充 */
export function GameVitalBar({ type, current, max }: GameVitalBarProps) {
  const conf = VITAL_CONFIG[type]
  const pct = max > 0 ? Math.max(0, Math.min(100, (current / max) * 100)) : 0
  const isLow = conf.isLow(pct)

  // 受击/消耗残影缓动：数值下降时，底层 ghost 延迟平滑缩减
  const [ghostPct, setGhostPct] = useState(pct)
  const prevPctRef = useRef(pct)

  useEffect(() => {
    if (pct < prevPctRef.current) {
      setGhostPct(prevPctRef.current)
      const timer = setTimeout(() => {
        setGhostPct(pct)
      }, 120)
      prevPctRef.current = pct
      return () => clearTimeout(timer)
    } else {
      setGhostPct(pct)
      prevPctRef.current = pct
    }
  }, [pct])

  return (
    <div className="game-vital-item" title={conf.tooltip(current, max, pct)}>
      <div className="game-vital-header">
        <div className="game-vital-title-group">
          <span className="game-vital-label">{conf.label}</span>
          <span className="game-vital-sublabel">{conf.sublabel}</span>
          {isLow && (
            <span className="chip chip--solid-danger text-[10px] !py-0 !px-1 leading-tight">
              {conf.alertLabel}
            </span>
          )}
        </div>

        <div className="game-vital-values">
          <span className={`game-vital-cur ${isLow ? 'game-vital-cur--low' : ''}`}>
            {current}
          </span>
          <span className="game-vital-max">/{max}</span>
        </div>
      </div>

      {/* 刻度由 .game-vital-track::after 画（十等分细密刻线），不再摆三个定位 span */}
      <div className="game-vital-track">
        {/* 受击/消耗残影层 */}
        <div
          className={`game-vital-ghost game-vital-ghost--${conf.toneClass}`}
          style={{ width: `${ghostPct}%` }}
        />

        {/* 主填充条（原生深色系） */}
        <div
          className={`game-vital-fill game-vital-fill--${conf.toneClass} ${
            isLow ? 'game-vital-fill--low' : ''
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/** 保持对旧 StatBar 调用的兼容 */
export function StatBar({ label, current, max }: { label: string; current: number; max: number; tone?: string }) {
  const norm = label.toLowerCase()
  const type: 'hp' | 'san' | 'mp' = norm.includes('hp') ? 'hp' : norm.includes('san') ? 'san' : 'mp'
  return <GameVitalBar type={type} current={current} max={max} />
}
