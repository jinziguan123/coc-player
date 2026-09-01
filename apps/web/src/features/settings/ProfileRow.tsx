/**
 * 配置列表里的一行。对话模型与生图模型共用。
 *
 * 此前两边各写各的，每行并排四到七个一模一样的 chip：对话侧是激活、设为快模型、设为
 * 视觉模型、编辑、复制、测试、删除；生图侧是使用、编辑、测试生图、删除。八条配置就是
 * 五十六个按钮糊成一片，而它们性质根本不同——派岗位的那几个是全局唯一的位置，其余是
 * 对这一条本身的操作。
 *
 * 现在每行只留一个主动作加一个「更多」。岗位徽章、主动作、菜单项都由调用方给：这个组件
 * 只负责「一行长什么样」，不知道也不该知道有哪几种岗位。
 */
import type { ReactNode } from 'react'
import { MoreMenu, type MoreMenuItem } from '@/components/ui/more-menu'

export interface RowAction {
  label: string
  onClick: () => void
  disabled?: boolean
  /** 按不动时说清为什么 */
  title?: string
  ariaLabel: string
}

export function ProfileRow({
  name, meta, badges, primary, menuItems, highlighted = false, menuLabel,
}: {
  name: string
  /** 模型名、地址这类次要信息，等宽显示 */
  meta: ReactNode
  /** 岗位徽章、协议标记 */
  badges?: ReactNode
  /** 留在外面的那一个动作。已经在岗的那条不给——它已经是了，再摆一个只会让人犹豫。 */
  primary?: RowAction
  menuItems: MoreMenuItem[]
  /** 正担着主岗位：描边点出来 */
  highlighted?: boolean
  menuLabel: string
}) {
  return (
    <div className={`profile-row${highlighted ? ' profile-row--narrator' : ''}`}>
      <div className="profile-row__main">
        <div className="profile-row__head">
          <strong className="profile-row__name">{name}</strong>
          {badges}
        </div>
        <div className="profile-row__meta">{meta}</div>
      </div>

      <div className="profile-row__actions">
        {primary && (
          <button
            className="btn-secondary btn-xs"
            onClick={primary.onClick}
            disabled={primary.disabled}
            aria-label={primary.ariaLabel}
            title={primary.title}
          >
            {primary.label}
          </button>
        )}
        <MoreMenu items={menuItems} label={menuLabel} />
      </div>
    </div>
  )
}
