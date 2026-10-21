import { useState } from 'react'
import { getSceneBackdropEnabled, setSceneBackdropEnabled, type GameDisplayMode } from '@/lib/theme'
import { useGameDisplay } from '@/features/game-session/vn/useGameDisplay'

const MODES: Array<{ value: GameDisplayMode; label: string; desc: string }> = [
  { value: 'chat', label: '聊天流', desc: '消息一条条往下排，场景图在流里出现一次，之后糊成氛围底' },
  { value: 'dialogue', label: '对话框', desc: '场景图清晰铺满，文字在底部对话框里一段段读；往回看点「历史」' },
]

/**
 * 局内显示方式 + 场景氛围底。两者是同一个问题的两面——场景图怎么出现——所以放一张卡里：
 * 氛围底只对聊天流有意义，对话框模式下场景图总是清晰铺满。
 * 对局顶栏的「对话框」按钮改的是同一份偏好（真人 KP 席不受影响，始终聊天流）。
 */
export function GameDisplayPanel() {
  const [mode, setMode] = useGameDisplay()
  const [backdrop, setBackdrop] = useState(() => getSceneBackdropEnabled())
  return (
    <div className="card">
      <h3 className="card-title">局内显示</h3>
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}>
        玩家视角的阅读方式。对局中也能在顶栏随时切换。
      </p>
      <div className="display-modes" role="radiogroup" aria-label="局内显示方式">
        {MODES.map((opt) => (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={mode === opt.value}
            className={`display-mode ${mode === opt.value ? 'is-on' : ''}`}
            onClick={() => setMode(opt.value)}
          >
            <span className={`display-mode-art display-mode-art--${opt.value}`} aria-hidden>
              <i /><i /><i />
            </span>
            <b>{opt.label}</b>
            <span>{opt.desc}</span>
          </button>
        ))}
      </div>
      <label
        className="text-sm"
        style={{
          display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: '1rem', cursor: 'pointer',
          opacity: mode === 'chat' ? 1 : 0.55,
        }}
      >
        <input
          type="checkbox"
          checked={backdrop}
          onChange={(e) => {
            setSceneBackdropEnabled(e.target.checked)
            setBackdrop(e.target.checked)
          }}
        />
        <span>聊天流下渲染场景氛围底</span>
      </label>
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginTop: '0.4rem' }}>
        把当前场景的配图重度模糊后铺在对局界面后面，只留下这个地方的色调，换场景时跟着变；不影响读字。
        没有配图的场景（未配置生图模型，或图还在生成）自动回落到主题底色。
      </p>
    </div>
  )
}
