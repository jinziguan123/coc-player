/**
 * 配置列表里的一行。
 *
 * 此前每行并排七个一模一样的 chip：激活、设为快模型、设为视觉模型、编辑、复制、测试、
 * 删除。八条配置就是五十六个按钮糊成一片，而它们性质根本不同——前三个是**派岗位**
 * （全局只有三个岗位），后四个是**对这一条本身**的操作。
 *
 * 现在每行只留一个主动作加一个「更多」：叙事岗是最常改的一个，留在外面；其余收进菜单。
 * 已经在叙事岗上的那条不显示主按钮——它已经是了，再摆个「设为叙事」只会让人犹豫。
 */
import { Copy, PlugZap, Trash2, SquarePen } from 'lucide-react'
import { MoreMenu, type MoreMenuItem } from '@/components/ui/more-menu'
import { RoleBadge } from './ModelRoles'

export interface ProfileRowData {
  id: string
  name: string
  protocol: 'openai' | 'anthropic'
  base_url: string
  model_name: string
  is_active: boolean
  is_fast?: boolean
  is_vision?: boolean
}

export function ProfileRow({
  profile, busy, testing, onAssignNarrator, onToggleAide, onToggleReader,
  onEdit, onDuplicate, onTest, onDelete,
}: {
  profile: ProfileRowData
  /** 有弹窗开着时不让改，避免改完又被表单覆盖。 */
  busy: boolean
  testing: boolean
  onAssignNarrator: () => void
  onToggleAide: () => void
  onToggleReader: () => void
  onEdit: () => void
  onDuplicate: () => void
  onTest: () => void
  onDelete: () => void
}) {
  const p = profile
  const items: MoreMenuItem[] = [
    {
      label: p.is_fast ? '不再当副手' : '设为副手模型',
      onClick: onToggleAide,
      title: '规划、AI 队友、滚动摘要这些幕后活改走这条；叙事仍走叙事模型',
    },
    {
      label: p.is_vision ? '不再当读图' : '设为读图模型',
      onClick: onToggleReader,
      title: '解析扫描件与图文模组时走这条；带团仍走叙事模型',
    },
    { label: '编辑', icon: <SquarePen size={12} />, onClick: onEdit, separated: true },
    {
      label: '复制一份',
      icon: <Copy size={12} />,
      onClick: onDuplicate,
      title: '连密钥一起复制，改个模型名就是一条新配置',
    },
    {
      label: testing ? '测试中…' : '测试连接',
      icon: <PlugZap size={12} />,
      onClick: onTest,
    },
    { label: '删除', icon: <Trash2 size={12} />, onClick: onDelete, separated: true },
  ]

  return (
    <div className={`profile-row${p.is_active ? ' profile-row--narrator' : ''}`}>
      <div className="profile-row__main">
        <div className="profile-row__head">
          <strong className="profile-row__name">{p.name}</strong>
          {p.is_active && <RoleBadge role="narrator" />}
          {p.is_fast && <RoleBadge role="aide" />}
          {p.is_vision && <RoleBadge role="reader" />}
          {p.protocol === 'anthropic' && <span className="badge">Anthropic</span>}
        </div>
        <div className="profile-row__meta">
          {p.model_name}
          {p.base_url && <span className="profile-row__url"> · {p.base_url}</span>}
        </div>
      </div>

      <div className="profile-row__actions">
        {!p.is_active && (
          <button
            className="btn-secondary btn-xs"
            onClick={onAssignNarrator}
            disabled={busy}
            aria-label={`让 ${p.name} 来叙事`}
            title={busy ? '先保存或取消正在编辑的配置' : undefined}
          >
            设为叙事
          </button>
        )}
        <MoreMenu items={busy ? items.filter((i) => i.label !== '编辑') : items} label={`${p.name} 的更多操作`} />
      </div>
    </div>
  )
}
