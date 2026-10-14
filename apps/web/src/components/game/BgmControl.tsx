/**
 * 顶栏的背景音乐控件：开关 + 音量。偏好是每台设备各自的（见 useBgm）。
 *
 * 曲库为空时不藏起来，而是在提示里说清楚曲子该放哪——否则玩家只会以为这功能坏了。
 */
import { GiSoundOff, GiSoundOn } from 'react-icons/gi'
import { toast } from 'sonner'

import { MOOD_LABELS } from '@/features/game-session/bgm'
import type { BgmState } from '@/features/game-session/useBgm'


const EMPTY_HINT =
  '曲库里还没有曲子：在 server/bgm/<氛围>/（打包版为数据目录下的 bgm/<氛围>/）放入 mp3/ogg 即可，'
  + '氛围目录名为 calm / explore / tense / horror / mythos / sorrow / combat / chase，放好后刷新页面'

export function BgmControl({ bgm }: { bgm: BgmState }) {
  const label = MOOD_LABELS[bgm.mood] || bgm.mood
  const title = bgm.empty
    ? EMPTY_HINT
    : !bgm.enabled
      ? '背景音乐已关闭，点击开启'
      : bgm.blocked
        ? `当前氛围：${label}。浏览器需要先有一次交互——点击页面任意处开始播放`
        : `当前氛围：${label}。点击关闭背景音乐`
  return (
    <span className="inline-flex items-center gap-1" data-tour="bgm">
      <button
        type="button"
        onClick={() => {
          // 曲库为空时开关切了也没声音，悄无声息只会让人以为坏了：直接告诉他曲子放哪
          if (bgm.empty) { toast.info(EMPTY_HINT, { duration: 8000 }); return }
          bgm.setEnabled(!bgm.enabled)
        }}
        className="btn-secondary btn-xs flex items-center gap-1"
        title={title}
        aria-pressed={bgm.enabled}
        aria-label={bgm.enabled ? '关闭背景音乐' : '开启背景音乐'}
        style={bgm.empty || bgm.blocked ? { opacity: 0.6 } : undefined}
      >
        {bgm.enabled ? <GiSoundOn size={13} /> : <GiSoundOff size={13} />}
        {bgm.enabled && !bgm.empty ? label : '配乐'}
      </button>
      {bgm.enabled && !bgm.empty && (
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={bgm.volume}
          onChange={(e) => bgm.setVolume(Number(e.target.value))}
          aria-label="背景音乐音量"
          title={`音量 ${Math.round(bgm.volume * 100)}%`}
          className="w-16 accent-[var(--color-accent)]"
        />
      )}
    </span>
  )
}
