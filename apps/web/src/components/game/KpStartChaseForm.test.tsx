import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { KpStartChaseForm } from './KpStartChaseForm'

const CHARACTERS = [{ id: 'c1', name: '山田健太' }, { id: 'c2', name: '亨利·卡特' }]
const NPCS = [{ id: 'n1', name: '京山人吉' }, { id: 'n2', name: '循声者' }]

function Harness({ initial = {} }: { initial?: Record<string, string> }) {
  const [fields, setFields] = useState<Record<string, string>>(initial)
  return (
    <KpStartChaseForm
      fields={fields}
      characters={CHARACTERS}
      npcs={NPCS}
      setField={(key, value) => setFields((current) => ({ ...current, [key]: value }))}
    />
  )
}

describe('发起追逐表单', () => {
  it('逃方与追方都走同一套 MultiSelect，且可多选', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    const quarry = screen.getByRole('button', { name: '逃跑的调查员' })
    await user.click(quarry)
    await user.click(screen.getByRole('option', { name: '山田健太' }))
    expect(quarry).toHaveTextContent('山田健太')

    const pursuers = screen.getByRole('button', { name: '追逐的追兵' })
    await user.click(pursuers)
    await user.click(screen.getByRole('option', { name: '京山人吉' }))
    await user.click(screen.getByRole('option', { name: '循声者' }))
    expect(pursuers).toHaveTextContent('京山人吉、循声者')
  })

  it('旧的 character: 前缀会迁移成裸角色 id，不会让选择看起来是空的', async () => {
    render(<Harness initial={{ quarry: 'character:c1' }} />)
    const quarry = screen.getByRole('button', { name: '逃跑的调查员' })
    await screen.findByText('山田健太')
    expect(quarry).toHaveTextContent('山田健太')
  })
})
