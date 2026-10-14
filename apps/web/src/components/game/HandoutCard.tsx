/**
 * 手书卡（Handout）：KP 发放的信件/报纸/日记/便条原文，渲染成信笺样式卡片。
 * 从 GameSessionPage 的 system 消息分支里拆出。
 */
import { useState } from 'react'
import { GiEnvelope, GiNewspaper, GiNotebook, GiPapers, GiScrollUnfurled } from 'react-icons/gi'
import { getServerUrl } from '@/api/client'
import { fmtTime } from '@/features/game-session/derive'
import type { ChatMessage } from '@/stores/sessionStore'

// 手书（Handout）卡片：按 metadata.handout_kind 选矢量图标与中文标签（缺省 GiScrollUnfurled/文书）
const HANDOUT_ICONS: Record<string, typeof GiScrollUnfurled> = {
  letter: GiEnvelope,
  news: GiNewspaper,
  diary: GiNotebook,
  note: GiPapers,
}
const HANDOUT_KIND_LABELS: Record<string, string> = {
  letter: '信件',
  news: '报纸',
  diary: '日记',
  note: '便条',
}
// 信笺正文用衬线体（配合泛黄纸质感），本地打包 Noto Serif SC，中文回退宋体
const HANDOUT_SERIF = '"Noto Serif SC", Georgia, "Songti SC", "SimSun", serif'

// 手书配图（metadata.image）：加载完成淡入（尊重 prefers-reduced-motion），加载失败整体隐藏不留占位
function HandoutImage({ src }: { src: string }) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const reducedMotion = typeof window !== 'undefined'
    && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (failed) return null
  return (
    <img
      src={src}
      alt=""
      className="block w-full rounded mb-3"
      style={{
        border: '1px solid var(--color-border)',
        opacity: loaded ? 1 : 0,
        transition: reducedMotion ? undefined : 'opacity 0.6s ease',
      }}
      onLoad={() => setLoaded(true)}
      onError={() => setFailed(true)}
    />
  )
}

export function HandoutCard({ msg }: { msg: ChatMessage }) {
  const hk = String(msg.metadata?.handout_kind || '')
  const title = String(msg.metadata?.title || '手书')
  const KindIcon = HANDOUT_ICONS[hk] || GiScrollUnfurled
  const kindLabel = HANDOUT_KIND_LABELS[hk] || '文书'
  // 配图相对 URL（如 /api/images/xxx.jpg）：本机走同源（vite 代理），客人模式拼房主地址
  const handoutImg = String(msg.metadata?.image || '')
  return (
    <div className="chat-msg py-2 flex justify-center">
      <div className="rounded-sm px-5 py-4 max-w-2xl w-full"
        style={{
          background: 'linear-gradient(180deg, color-mix(in srgb, var(--color-bg-tertiary) 84%, #b08d57), var(--color-bg-tertiary))',
          border: '1px solid color-mix(in srgb, var(--color-border) 50%, #b08d57)',
          boxShadow: 'inset 0 0 0 4px color-mix(in srgb, transparent 88%, #b08d57)',
        }}>
        <div className="flex items-center gap-2 pb-2 mb-3"
          style={{ borderBottom: '1px dashed color-mix(in srgb, var(--color-border) 45%, #b08d57)', color: 'var(--color-text-accent)' }}>
          <KindIcon style={{ fontSize: '1.2rem', flexShrink: 0 }} />
          <span className="font-semibold" style={{ fontFamily: HANDOUT_SERIF }}>{title}</span>
          <span className="text-xs ml-auto flex-shrink-0" style={{ color: 'var(--color-text-secondary)' }}>{kindLabel}</span>
        </div>
        {handoutImg && <HandoutImage src={`${getServerUrl()}${handoutImg}`} />}
        <div className="text-sm whitespace-pre-wrap leading-relaxed"
          style={{ color: 'var(--color-text-primary)', fontFamily: HANDOUT_SERIF }}>
          {msg.content}
        </div>
        {fmtTime(msg.ts) && (
          <div className="msg-ts text-right mt-2" style={{ color: 'var(--color-text-secondary)' }}>{fmtTime(msg.ts)}</div>
        )}
      </div>
    </div>
  )
}
