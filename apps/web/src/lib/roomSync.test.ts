import { describe, expect, it } from 'vitest'
import { luckSnapshotFrom } from './roomSync'

// 实测事故：侦查掷出 71 > 55，差 16 点、角色幸运 45，后端挂出询价并停下整条结算链，
// 可 luck_offer 事件不落库——玩家刷新一次卡片就没了，人却还等着拍板。
// 这个归一函数是广播与 /sync 快照的共同出口，两条路才不会一边改了另一边忘了。

const RAW = {
  char_id: 'char-1',
  actor: '陈守一',
  skill: '侦查',
  dice_event_id: 'dice-1',
  cost: 16,
  available: 45,
  target: 55,
}

describe('幸运询价的状态归一', () => {
  it('广播的 metadata（没有 pending 字段）照常认', () => {
    const s = luckSnapshotFrom(RAW)
    expect(s?.cost).toBe(16)
    expect(s?.available).toBe(45)
    expect(s?.actor).toBe('陈守一')
    expect(s?.char_id).toBe('char-1')   // 只有骰子的主人能拍板
  })

  it('/sync 快照（带 pending）走同一个出口，结果一致', () => {
    expect(luckSnapshotFrom({ pending: true, ...RAW })).toEqual(luckSnapshotFrom(RAW))
  })

  it('没有待决的就返回 null', () => {
    expect(luckSnapshotFrom({ pending: false })).toBeNull()
    expect(luckSnapshotFrom(undefined)).toBeNull()
    expect(luckSnapshotFrom(null)).toBeNull()
  })

  it('差 0 点时不画卡——与其给一张点不动的，不如不给', () => {
    expect(luckSnapshotFrom({ ...RAW, cost: 0 })).toBeNull()
    expect(luckSnapshotFrom({ ...RAW, cost: undefined })).toBeNull()
  })
})
