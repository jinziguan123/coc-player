import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CharacterPanel } from './CharacterPanel'

const baseCharacter = {
  id: 'character-1',
  name: '山田健太',
  base_attributes: {},
  skills: {},
  system_data: {},
  backstory: '',
  status: 'active',
}

describe('CharacterPanel 状态显示', () => {
  it.each([
    ['ok', '正常'],
    ['dying', '濒死'],
    ['fled', '逃离'],
  ])('将 %s 显示为中文“%s”', (status, label) => {
    render(<CharacterPanel character={{ ...baseCharacter, status }} />)

    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.queryByText(status)).not.toBeInTheDocument()
  })

  it('未知英文状态不直接暴露内部代码值', () => {
    render(<CharacterPanel character={{ ...baseCharacter, status: 'future_status' }} />)

    expect(screen.getByText('未知状态')).toBeInTheDocument()
    expect(screen.queryByText('future_status')).not.toBeInTheDocument()
  })
})

describe('技能页申请检定', () => {
  const withSkills = { ...baseCharacter, skills: { 侦查: 65 } }

  it('说明与确认框都写明「申请进本回合暂存、与发言一起交给 KP」', async () => {
    const user = userEvent.setup()
    render(<CharacterPanel character={withSkills} onSkillCheck={() => {}} />)

    await user.click(screen.getByRole('tab', { name: '技能' }))
    // 列表上方的常驻说明：点下去不是立刻触发一次生成，而是进暂存
    expect(screen.getByText(/把检定申请加入本回合/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /侦查/ }))
    expect(screen.getByText(/申请会加入本回合暂存/)).toBeInTheDocument()
    expect(screen.getByText(/推进本回合/)).toBeInTheDocument()
  })

  it('确认后把技能名与检定目标一并交出去', async () => {
    const user = userEvent.setup()
    const calls: [string, string][] = []
    render(
      <CharacterPanel
        character={withSkills}
        onSkillCheck={(skill, intent) => calls.push([skill, intent])}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '技能' }))
    await user.click(screen.getByRole('button', { name: /侦查/ }))
    await user.type(screen.getByRole('textbox'), '报纸的日期')
    await user.click(screen.getByRole('button', { name: '申请' }))

    expect(calls).toEqual([['侦查', '报纸的日期']])
  })
})
