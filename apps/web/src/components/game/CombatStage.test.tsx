// 沉浸战场右侧那块「已选中」面板是「点谁看谁」的落点，可「看谁」和「选谁当目标」原先
// 各写各的状态：点棋子只写 targetId，面板便停在上一张脸上——点第二个敌人，棋盘准星和
// 目标下拉都换了，右边显示的仍是第一个。这里把三条会改变「选中」的路径都钉住。
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CombatStage, type Combatant, type CombatState } from './CombatStage'

function unit(
  id: string, name: string, side: Combatant['side'], hp: number, max: number,
  pos?: { x: number; y: number },
): Combatant {
  return { id, name, side, is_human: side !== 'enemy', hp, max_hp: max, status: 'ok', pos: pos ?? null }
}

const ORDER: Combatant[] = [
  unit('pc', '沃什帕杉德', 'player', 12, 12, { x: 7, y: 3 }),
  unit('mate', '队友甲', 'ally', 5, 10),
  unit('stranger', '陌生男性', 'enemy', 17, 17, { x: 5, y: 2 }),
  unit('unknown', '不明存在', 'enemy', 9, 9, { x: 8, y: 2 }),
]

const COMBAT: CombatState = {
  round: 3,
  turn: 'pc',
  order: ORDER,
  grid: { cols: 12, rows: 8, cell_m: 1.5 },
}

function renderStage() {
  const { container } = render(
    <CombatStage combat={COMBAT} myCharId="pc" sessionId="s1" log={[]} layout="immersive" />,
  )
  return {
    /** 焦点面板显示的是谁。 */
    focus: () => container.querySelector('.combat-focus-name')?.textContent,
    /** 面板顶上的标签：正在行动 / 已选中。 */
    focusLabel: () => container.querySelector('.combat-focus .combat-label')?.textContent,
    /** 目标下拉当前显示的目标。 */
    target: () => screen.getByRole('combobox', { name: '选择目标' }).textContent,
  }
}

describe('沉浸战场：焦点面板跟着「选中」走', () => {
  it('缺省显示当前行动者', () => {
    const s = renderStage()
    expect(s.focus()).toBe('沃什帕杉德')
    expect(s.focusLabel()).toBe('正在行动')
  })

  it('点棋盘上的另一个敌人：面板与目标下拉一起换过去', () => {
    const s = renderStage()
    expect(s.target()).toContain('陌生男性')   // 攻击的缺省目标＝第一个存活敌人

    fireEvent.click(screen.getByTitle('不明存在 9/9'))

    expect(s.focus()).toBe('不明存在')
    expect(s.focusLabel()).toBe('已选中')
    expect(s.target()).toContain('不明存在')
  })

  it('改目标下拉，面板也跟着换——它俩说的必须是同一个单位', async () => {
    const user = userEvent.setup()
    const s = renderStage()

    await user.click(screen.getByRole('combobox', { name: '选择目标' }))
    await user.click(screen.getByRole('option', { name: /不明存在/ }))

    expect(s.focus()).toBe('不明存在')
  })

  it('点先攻序只是「看谁」，不会顺手把目标也改掉', () => {
    const s = renderStage()
    expect(s.target()).toContain('陌生男性')

    fireEvent.click(screen.getByTitle('不明存在 · 9/9'))

    expect(s.focus()).toBe('不明存在')
    expect(s.target()).toContain('陌生男性')   // 目标仍在原处
  })

  it('换动作换了一套候选（攻击→急救→攻击），面板跟着新目标走', async () => {
    const user = userEvent.setup()
    const s = renderStage()

    fireEvent.click(screen.getByTitle('不明存在 9/9'))
    expect(s.focus()).toBe('不明存在')

    await user.click(screen.getByRole('button', { name: '急救' }))
    expect(s.focus()).toBe('队友甲')

    await user.click(screen.getByRole('button', { name: '攻击' }))
    expect(s.focus()).toBe('不明存在')
  })
})
