import { useState } from 'react'
import { UserRound } from 'lucide-react'

/**
 * 角色头像：有图显示图，没图回落姓名首字纹章。
 *
 * 「没有头像」是**正常状态**而不是缺陷态——绝大多数卡不会去生成头像，纹章得自己站得住，
 * 不能做成一个等着被填的空框。所以两种形态共用同一套边框、圆角与琥珀描边，尺寸也一致，
 * 混排在列表里不会一眼看出谁「缺」了东西。
 *
 * 图片 404 时（数据目录被清过、跨机同步过来的卡）自动回落纹章，不留破图。
 */
export function CharacterPortrait({
  name,
  avatarUrl,
  size = 'md',
  className = '',
}: {
  name: string
  avatarUrl?: string | null
  /** sm=列表密排 / md=卡片抬头 / lg=编辑页主视觉 */
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const [broken, setBroken] = useState(false)
  const initial = name.trim().charAt(0)
  const sizeClass = { sm: 'char-portrait--sm', md: '', lg: 'char-portrait--lg' }[size]
  const cls = `char-sigil char-portrait ${sizeClass} ${className}`.trim()

  if (avatarUrl && !broken) {
    return (
      <span className={cls} aria-hidden="true">
        <img
          src={avatarUrl}
          alt=""
          className="char-portrait-img"
          onError={() => setBroken(true)}
        />
      </span>
    )
  }
  return (
    <span className={cls} aria-hidden="true">
      {initial || <UserRound className="h-4 w-4" />}
    </span>
  )
}
