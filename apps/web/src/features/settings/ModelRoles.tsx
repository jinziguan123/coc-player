/**
 * 三个岗位，各自谁在干。
 *
 * 这一页真正的结构是「三个岗位、一批候选」，而不是「一堆配置、每个都能被指派成任何
 * 角色」。此前是后者：每行并排挂着激活 / 设为快模型 / 设为视觉模型三个指派按钮，八条
 * 配置就是二十四个指派按钮——可岗位统共只有三个。
 *
 * 更要命的是三个名字都在说系统怎么实现，不说它管什么：「激活」是个状态，「快模型」说
 * 的是它快，都没回答「设了它之后，游戏里什么会变」。唯一的解释藏在按钮的 title 里，
 * 鼠标停上去才看得见。把职责摊在明面上，才谈得上让人选得明白。
 *
 * 后端字段名照旧（is_active / is_fast / is_vision），这里只改人看的那一面。
 */
import type { ReactNode } from 'react'
import { ROLES, type RoleHolder, type RoleSpec } from './roles'

/**
 * 岗位一览。每张卡回答三件事：这个岗位管什么、现在是谁、空着会怎样。
 */
export function ModelRoles({ holders }: {
  holders: Partial<Record<RoleSpec['key'], RoleHolder | undefined>>
}) {
  return (
    <div className="role-grid">
      {ROLES.map((role) => {
        const who = holders[role.key]
        return (
          <div key={role.key} className={`role-card${who ? '' : ' role-card--vacant'}`}>
            <div className="role-card__head">
              <span className="role-card__label">{role.label}</span>
              {who
                ? <span className="role-card__who">{who.name}</span>
                : <span className="role-card__who role-card__who--vacant">未指定</span>}
            </div>
            <p className="role-card__duty">{role.duty}</p>
            <p className="role-card__foot">
              {who ? who.model_name : role.vacant}
            </p>
          </div>
        )
      })}
    </div>
  )
}

/** 列表行上的岗位徽章。与上面的卡同名同色，一眼能对上。 */
export function RoleBadge({ role }: { role: RoleSpec['key'] }): ReactNode {
  const spec = ROLES.find((r) => r.key === role)
  if (!spec) return null
  return <span className={`role-badge role-badge--${role}`}>{spec.label}</span>
}
