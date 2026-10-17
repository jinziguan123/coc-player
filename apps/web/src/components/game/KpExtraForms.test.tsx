import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { HpChangeForm } from './KpExtraForms'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

/** 受控表单：替它拿着 fields，照实模拟写作台那一侧。 */
function Harness({ onFields }: { onFields: (f: Record<string, string>) => void }) {
  const [fields, setFields] = useState<Record<string, string>>({})
  return (
    <HpChangeForm
      sessionId="s1"
      fields={fields}
      characters={[{ id: 'c1', name: '陈守一' }]}
      setField={(k, v) => setFields((cur) => { const next = { ...cur, [k]: v }; onFields(next); return next })}
    />
  )
}

describe('结算 HP 表单', () => {
  it('伤害默认带负号，骰式原样交给后端掷', async () => {
    vi.mocked(api.get).mockResolvedValue({ active: false })
    let last: Record<string, string> = {}
    render(<Harness onFields={(f) => { last = f }} />)
    await userEvent.type(screen.getByLabelText('变化量'), '1d6+1')
    expect(last.delta).toBe('-1d6+1')
  })

  it('战斗中列出 NPC 参战方，调查员不重复出现', async () => {
    vi.mocked(api.get).mockResolvedValue({
      active: true,
      order: [{ id: 'c1', name: '陈守一', side: 'player' }, { id: 'guard', name: '守门人', side: 'enemy' }],
    })
    render(<Harness onFields={() => {}} />)
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/sessions/s1/combat'))
    await userEvent.click(screen.getByLabelText('结算对象'))
    expect(await screen.findByText('战斗中的 NPC')).toBeInTheDocument()
    expect(screen.getAllByText('陈守一')).toHaveLength(1)
    expect(screen.getByText('守门人')).toBeInTheDocument()
  })
})
