/**
 * 顶栏的背景音乐控件：一个图标按钮（开 / 关），音量滑杆悬停或聚焦时才浮出。
 *
 * 不显示当前氛围名：配乐是氛围，不是信息——把「恐怖」两个字挂在顶栏上，等于提前告诉
 * 玩家 KP 觉得接下来要出事。曲库为空时点击直接说清曲子放哪，否则玩家只会以为坏了。
 */
import { GiSoundOff, GiSoundOn } from 'react-icons/gi'
import { toast } from 'sonner'

import type { BgmState } from '@/features/game-session/useBgm'

const EMPTY_HINT =
  '曲库里还没有曲子：在 server/bgm/<氛围>/（打包版为数据目录下的 bgm/<氛围>/）放入 mp3/ogg 即可，'
  + '氛围目录名为 calm / explore / tense / horror / mythos / sorrow / combat / chase，放好后刷新页面'

export function BgmControl({ bgm }: { bgm: BgmState }) {
  const title = bgm.empty
    ? EMPTY_HINT
    : !bgm.enabled
      ? '背景音乐已关闭，点击开启'
      : bgm.blocked
        ? '浏览器需要先有一次交互——点击页面任意处开始播放'
        : '点击关闭背景音乐'
  const showSlider = bgm.enabled && !bgm.empty
  return (
    // group：悬停在按钮或浮层上、或键盘聚焦到其中任一处时，音量浮层显示
    <span className="group relative inline-flex" data-tour="bgm">
      <button
        type="button"
        onClick={() => {
          if (bgm.empty) { toast.info(EMPTY_HINT, { duration: 8000 }); return }
          bgm.setEnabled(!bgm.enabled)
        }}
        className="btn-secondary btn-xs flex items-center"
        title={title}
        aria-pressed={bgm.enabled}
        aria-label={bgm.enabled ? '关闭背景音乐' : '开启背景音乐'}
        style={bgm.empty || bgm.blocked ? { opacity: 0.6 } : undefined}
      >
        {bgm.enabled ? <GiSoundOn size={14} /> : <GiSoundOff size={14} />}
      </button>
      {showSlider && (
        // pt-1 而不是 mt-1：浮层与按钮之间不留空隙，鼠标往下移的途中不会掉出 hover
        <span className="invisible absolute left-1/2 top-full z-50 -translate-x-1/2 pt-1 opacity-0 transition-opacity duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
          <span
            className="flex items-center gap-2 rounded border px-2.5 py-1.5 text-xs shadow-lg"
            style={{ background: 'var(--color-bg-secondary)', borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
          >
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={bgm.volume}
              onChange={(e) => bgm.setVolume(Number(e.target.value))}
              aria-label="背景音乐音量"
              className="w-24 accent-[var(--color-accent)]"
            />
            <span className="w-8 text-right tabular-nums">{Math.round(bgm.volume * 100)}%</span>
          </span>
        </span>
      )}
    </span>
  )
}
