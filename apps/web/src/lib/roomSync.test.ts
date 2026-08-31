import { describe, expect, it } from 'vitest'
import { luckOfferMessage } from './roomSync'

// 实测事故：侦查掷出 71 > 55，差 16 点、角色幸运 45，后端挂出询价并停下整条结算链，
// 可 luck_offer 事件不落库——玩家刷新一次卡片就没了，人却还等着拍板。这份快照是补它的。

const SNAP = {
  pending: true,
  id: 'luck:dice-1',
  char_id: 'char-1',
  actor: '陈守一',
  skill: '侦查',
  dice_event_id: 'dice-1',
  cost: 16,
  available: 45,
  target: 55,
}

describe('幸运询价的重连补卡', () => {
  it('待决时还原成消息流里的那条，字段形状与广播一致', () => {
    const msg = luckOfferMessage(SNAP)
    // 页面靠 cost + dice_event_id 认出这是幸运卡（见 GameSessionPage 的渲染分支）
    expect(msg?.metadata?.cost).toBe(16)
    expect(msg?.metadata?.dice_event_id).toBe('dice-1')
    expect(msg?.metadata?.available).toBe(45)
    expect(msg?.metadata?.actor).toBe('陈守一')
    expect(msg?.metadata?.char_id).toBe('char-1')   // 只有骰子的主人能拍板
  })

  it('id 原样用后端给的——store 按 id 幂等，改了就会出两张卡', () => {
    expect(luckOfferMessage(SNAP)?.id).toBe('luck:dice-1')
  })

  it('没有待决的就不补', () => {
    expect(luckOfferMessage({ pending: false })).toBeNull()
    expect(luckOfferMessage(undefined)).toBeNull()
  })

  it('数据不全时宁可不补，也不要渲染一张点不动的卡', () => {
    expect(luckOfferMessage({ ...SNAP, id: undefined })).toBeNull()
    expect(luckOfferMessage({ ...SNAP, cost: undefined })).toBeNull()
  })
})
