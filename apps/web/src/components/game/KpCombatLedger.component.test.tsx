import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatMessage } from '@/stores/sessionStore'

import { KpCombatLedger } from './KpCombatLedger'
import type { CombatState } from './combat/types'

const post = vi.fn().mockResolvedValue(undefined)
vi.mock('@/api/client', () => ({ api: { post: (...args: unknown[]) => post(...args) } }))
vi.mock('./useKpDesk', () => ({
  useKpDesk: () => ({
    pending_checks: [{
      id: 'check-1', kind: 'skill', skill: '力量', difficulty: 'normal',
      actor_name: '陈守一', reason: '把盐撒出去', group: false,
    }],
    check_requests: [],
    ledger: [],
  }),
}))

const COMBAT: CombatState = {
  round: 4,
  turn: 'chen',
  order: [
    { id: 'chen', name: '陈守一', side: 'player', is_human: true, hp: 9, max_hp: 11, status: 'ok' },
    { id: 'enemy', name: '幼体甲', side: 'enemy', is_human: false, hp: 7, max_hp: 12, status: 'ok' },
  ],
}

const MESSAGES: ChatMessage[] = [
  {
    id: 'check', type: 'system', content: '请陈守一投力量', actor_name: '系统',
    sequence_num: 30,
    metadata: { check_request: true, id: 'check-1' },
  },
  {
    id: 'say', type: 'action', content: '我把盐撒向它的眼睛。', actor_name: '陈守一',
    sequence_num: 31, metadata: { is_player: true },
  },
  {
    id: 'ooc', type: 'ooc', content: '（等我一下）', actor_name: '陈守一',
    sequence_num: 32, metadata: { is_player: true },
  },
]

function renderLedger(over: Partial<Parameters<typeof KpCombatLedger>[0]> = {}) {
  return render(
    <KpCombatLedger
      sessionId="s1"
      messages={MESSAGES}
      combat={COMBAT}
      combatLogSince={null}
      party={[{ id: 'chen', name: '陈守一', isAi: false, occupation: '外科医生', status: 'active' }]}
      onCheck={vi.fn()}
      onReply={vi.fn()}
      onOpenWriter={vi.fn()}
      onChanged={vi.fn()}
      {...over}
    />,
  )
}

describe('KpCombatLedger', () => {
  beforeEach(() => { post.mockClear() })

  it('把待办区、时间轴和行内动作同时摆出来', () => {
    renderLedger()
    expect(screen.getByText('待你处理')).toBeInTheDocument()
    expect(screen.getByText(/请陈守一投力量/)).toBeInTheDocument()
    expect(screen.getByText('我把盐撒向它的眼睛。')).toBeInTheDocument()
    expect(screen.getByText('（等我一下）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发检定' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '回复' }).length).toBeGreaterThan(0)
  })

  it('代投走 KP 专用端点，并通知页面刷新', async () => {
    const onChanged = vi.fn()
    renderLedger({ onChanged })
    fireEvent.click(screen.getAllByRole('button', { name: '代投' })[0])
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith('/sessions/s1/kp/checks/check-1/roll')
      expect(onChanged).toHaveBeenCalled()
    })
  })

  it('点玩家发言的「发检定」把角色名与原文交给写作台', () => {
    const onCheck = vi.fn()
    renderLedger({ onCheck })
    fireEvent.click(screen.getByRole('button', { name: '发检定' }))
    expect(onCheck).toHaveBeenCalledWith('陈守一', '我把盐撒向它的眼睛。')
  })

  it('玩家/场外筛选只留对应条目', () => {
    renderLedger()
    fireEvent.click(screen.getByRole('tab', { name: /玩家/ }))
    expect(screen.queryByText('（等我一下）')).not.toBeInTheDocument()
    expect(screen.getByText('我把盐撒向它的眼睛。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /场外/ }))
    expect(screen.getByText('（等我一下）')).toBeInTheDocument()
    expect(screen.queryByText('我把盐撒向它的眼睛。')).not.toBeInTheDocument()
  })
})
