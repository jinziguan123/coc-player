/**
 * 创建前 AI 对「这张卡与所选模组是否契合」的顾虑：浮在页面中央，可收起成底部的小条。
 *
 * 不做成模态：玩家要一边看顾虑一边改卡。收起后人物卡照常滚动、编辑，小条随时点开。
 * 浮窗与小条各自进退场（缩放淡出 / 从底部升起），不共用 layoutId——两者都带退场动画时
 * 共享布局的交接会让二者都卸不掉。
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m } from 'motion/react'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { GiScrollUnfurled } from 'react-icons/gi'
import { ease, spring } from '@/lib/motion'

/** 退场用短补间而不是弹簧：弹簧要等缩放「停稳」才卸载，期间透明的浮窗还会挡住页面中央的点击。 */
const EXIT = { duration: 0.18, ease: ease.out }

export interface EvalResult { warnings: string[]; suggestions: string[] }

export function EvalConcerns({ result, collapsed, creating, onCollapse, onExpand, onDismiss, onCreateAnyway }: {
  result: EvalResult | null
  collapsed: boolean
  creating: boolean
  onCollapse: () => void
  onExpand: () => void
  onDismiss: () => void
  onCreateAnyway: () => void
}) {
  // Esc 只收起不关掉：顾虑还在，玩家随时能再看
  useEffect(() => {
    if (!result || collapsed) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCollapse() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [result, collapsed, onCollapse])

  const count = result ? result.warnings.length + result.suggestions.length : 0
  return createPortal(
    <AnimatePresence>
      {result && !collapsed && (
        <m.section key="panel" className="cs-eval" role="dialog" aria-label="AI 的契合度顾虑"
          initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.94, transition: EXIT }}
          transition={spring.pop}>
          <header className="cs-eval-h">
            <GiScrollUnfurled aria-hidden />
            <h3>这张卡与所选模组的契合度有些顾虑</h3>
            <span className="cs-grow" />
            <button className="cs-eval-ic" onClick={onCollapse} aria-label="收起" title="收起（Esc）"><ChevronDown size={16} /></button>
            <button className="cs-eval-ic" onClick={onDismiss} aria-label="关闭" title="关闭"><X size={16} /></button>
          </header>
          <div className="cs-eval-b">
            {result.warnings.length > 0 && (
              <>
                <div className="cs-eval-label">顾虑</div>
                <ul>{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              </>
            )}
            {result.suggestions.length > 0 && (
              <>
                <div className="cs-eval-label">建议</div>
                <ul>{result.suggestions.map((w) => <li key={w}>{w}</li>)}</ul>
              </>
            )}
          </div>
          <footer className="cs-eval-f">
            <span className="cs-hint">只是提醒，不影响能否创建</span>
            <span className="cs-grow" />
            <button className="btn-secondary btn-sm" onClick={onCollapse}>收起，去修改</button>
            <button className="btn-primary btn-sm" disabled={creating} onClick={onCreateAnyway}>
              {creating ? '创建中…' : '仍然创建'}
            </button>
          </footer>
        </m.section>
      )}
      {result && collapsed && (
        <m.button key="pill" className="cs-eval-pill" onClick={onExpand}
          initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 16, transition: EXIT }} transition={spring.pop}
          aria-label={`展开 AI 的 ${count} 条顾虑`}>
          <GiScrollUnfurled aria-hidden />
          <span>AI 顾虑 {count} 条</span>
          <ChevronUp size={14} aria-hidden />
        </m.button>
      )}
    </AnimatePresence>,
    document.body,
  )
}
