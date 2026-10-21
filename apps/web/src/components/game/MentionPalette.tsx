/**
 * @ 补全菜单的本体：只负责把行画出来，判定、两级切换与插入都在 useMentionMenu。
 *
 * 单独成文件的两个理由：组件文件里混着导出的 hook 会让 fast refresh 退化成整页刷新
 * （同 kpCatalogs.ts 的注释），以及 useMentionMenu 的行数本来就该留给状态机。
 *
 * 观感按站内菜单那套来（.more-menu-list / .combo-list）：卡片底 + 细边框 + 同一个投影，
 * 进出场走 lib/motion 的 popoverVariants。**不另起一套视觉**——它是输入框的一部分，
 * 不该长得像个外挂面板。
 *
 * 两级的行同构：图标片 + 名字 + 右侧说明。差别只在尾部：一级是「项数 + 右箭头」，
 * 二级是地点状态 / 技能值；全局过滤（直接打字）时名字后面补一个组名，免得不知道
 * 「艾琳」是队友还是别的东西。
 */
import { AnimatePresence, m } from 'motion/react'
import { ArrowUpDown, ChevronRight, CornerDownLeft } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'

import { popoverVariants } from '@/lib/motion'
import type { MentionCandidate } from './mentionSources'

export interface PaletteItem extends MentionCandidate {
  /** 按 kind 定的图标底色 class（who / where / skill）。 */
  cls: string
  icon: ReactNode
  /** 组名（全局过滤时补在名字后面）。 */
  kindLabel: string
  /** 直接打字过滤出来的结果要标出组名；从一级点进某组时不必重复。 */
  showKind?: boolean
}

export interface PaletteGroup {
  key: string
  label: string
  count: number
  cls: string
  icon: ReactNode
}

type Rows =
  | { mode: 'groups'; groups: PaletteGroup[] }
  | { mode: 'items'; items: PaletteItem[]; scopeLabel?: string }

export function MentionPalette(input: Rows & {
  open: boolean
  cursor: number
  /** 打了字但一条都没命中：面板不消失，改说一句「没有匹配」——正打着字面板突然没了，
   *  会让人以为 @ 坏了。 */
  empty?: boolean
  onHover: (i: number) => void
  onPick: (i: number) => void
}) {
  const { open, cursor, empty, onHover, onPick } = input
  // 长列表（技能动辄五十多项）用方向键走的时候，高亮行必须自己滚进可视区，
  // 否则按十几下也看不见选中了谁。只有高亮行变了才滚，鼠标划过不会带着整页抖。
  const activeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (open) activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [cursor, open])

  const activeProps = (i: number) => (i === cursor ? { ref: activeRef } : {})

  return (
    <AnimatePresence>
      {open && (
        <m.div
          className="kp-mention"
          role="listbox"
          aria-label="可以提到的人与地方"
          variants={popoverVariants}
          initial="initial"
          animate="enter"
          exit="exit"
          // 面板在输入框上方展开，缩放的锚点落在左下角才像"从输入框里长出来"
          style={{ transformOrigin: 'left bottom' }}
          // 别让点选把焦点从输入框抢走：抢走会先触发 textarea 的 blur（菜单当场收起），
          // 这一下 click 就落空了。preventDefault 掉 mousedown 是最省事的守法。
          onMouseDown={(e) => e.preventDefault()}
        >
          {empty ? (
            <div className="kp-mention-empty">
              <span className="kp-mention-empty-title">没有匹配的队友、地点或技能</span>
              <span className="kp-mention-empty-note">接着写下去就好，@ 只是补全</span>
            </div>
          ) : input.mode === 'groups' ? (
            <>
              {input.groups.map((g, i) => (
                <button
                  key={g.key}
                  type="button"
                  role="option"
                  aria-selected={i === cursor}
                  className={`kp-mention-row ${i === cursor ? 'kp-mention-row--on' : ''}`}
                  onMouseEnter={() => onHover(i)}
                  onClick={() => onPick(i)}
                  {...activeProps(i)}
                >
                  <span className={`kp-mention-ic ${g.cls}`}>{g.icon}</span>
                  <span className="kp-mention-name">{g.label}</span>
                  <span className="kp-mention-count">{g.count}</span>
                  <ChevronRight size={13} className="kp-mention-go" aria-hidden="true" />
                </button>
              ))}
              {/* 一级才给键位提示：两级这件事不写出来，第一次用的人不会去按 Enter。 */}
              <div className="kp-mention-foot">
                <ArrowUpDown size={12} aria-hidden="true" />
                选择
                <CornerDownLeft size={12} aria-hidden="true" />
                进入
                <span className="kp-mention-foot-note">或直接打字</span>
              </div>
            </>
          ) : (
            <>
              {input.scopeLabel && (
                // 二级的头部：不是候选行，所以不进 role=option，也不占键盘高亮的序号。
                <div className="kp-mention-head">
                  <span className="kp-mention-head-title">{input.scopeLabel}</span>
                  <span className="kp-mention-kbd">Esc</span>
                  <span className="kp-mention-head-note">返回</span>
                </div>
              )}
              {input.items.map((m, i) => (
                <button
                  key={`${m.kind}:${m.label}`}
                  type="button"
                  role="option"
                  aria-selected={i === cursor}
                  className={`kp-mention-row ${i === cursor ? 'kp-mention-row--on' : ''}`}
                  onMouseEnter={() => onHover(i)}
                  onClick={() => onPick(i)}
                  {...activeProps(i)}
                >
                  <span className={`kp-mention-ic ${m.cls}`}>{m.icon}</span>
                  <span className="kp-mention-name">{m.label}</span>
                  {m.showKind && <span className="kp-mention-kind">{m.kindLabel}</span>}
                  {m.hint && (
                    m.hint === '当前'
                      ? <span className="kp-mention-tag">当前</span>
                      : (
                        <span className={m.kind === 'skill' ? 'kp-mention-hint kp-mention-hint--num' : 'kp-mention-hint'}>
                          {m.hint}
                        </span>
                      )
                  )}
                </button>
              ))}
            </>
          )}
        </m.div>
      )}
    </AnimatePresence>
  )
}
