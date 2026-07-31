import { beforeEach, describe, expect, it } from 'vitest'

import {
  forgetRemoteRoom,
  hostIdFromInvite,
  listRemoteRooms,
  rememberRemoteRoom,
} from './remoteRooms'

/**
 * 别人的房间存在**房主的库**里，本机会话列表永远拉不到。房主一掉线，客人就被切回
 * 本机，房间从「我的房间」凭空消失——只能重新翻聊天记录找邀请码。这份本地记录就是
 * 为了让它留在列表上。
 */
describe('记住在别人那儿玩的房间', () => {
  beforeEach(() => localStorage.clear())

  const room = { invite: 'trpg:hostkey:K7M9PQ2R', roomCode: 'K7M9PQ2R', hostId: 'hostkey' }

  it('记住之后能读回来', () => {
    rememberRemoteRoom({ ...room, title: '陵墓' })
    const [saved] = listRemoteRooms()
    expect(saved.roomCode).toBe('K7M9PQ2R')
    expect(saved.title).toBe('陵墓')
    expect(saved.invite).toBe('trpg:hostkey:K7M9PQ2R')
  })

  it('同一房主的同一房间只留一条，重进只更新不堆积', () => {
    rememberRemoteRoom({ ...room, title: '旧标题' })
    rememberRemoteRoom({ ...room, title: '新标题' })
    expect(listRemoteRooms()).toHaveLength(1)
    expect(listRemoteRooms()[0].title).toBe('新标题')
  })

  it('同一房主的不同房间各留一条', () => {
    rememberRemoteRoom(room)
    rememberRemoteRoom({ ...room, roomCode: 'OTHER123' })
    expect(listRemoteRooms()).toHaveLength(2)
  })

  it('最近进入的排在前面', () => {
    rememberRemoteRoom({ ...room, roomCode: 'FIRST' })
    rememberRemoteRoom({ ...room, roomCode: 'SECOND' })
    expect(listRemoteRooms().map((r) => r.roomCode)).toEqual(['SECOND', 'FIRST'])
  })

  it('只留最近若干个，不让列表被历史房间淹没', () => {
    for (let i = 0; i < 20; i++) rememberRemoteRoom({ ...room, roomCode: `CODE${i}` })
    expect(listRemoteRooms().length).toBeLessThanOrEqual(12)
    // 最新的那个一定在
    expect(listRemoteRooms()[0].roomCode).toBe('CODE19')
  })

  it('信息不全的不记——重连需要邀请码和房间码，缺一个都连不回去', () => {
    rememberRemoteRoom({ invite: '', roomCode: 'K7M9PQ2R', hostId: 'hostkey' })
    rememberRemoteRoom({ invite: 'trpg:hostkey', roomCode: '', hostId: 'hostkey' })
    expect(listRemoteRooms()).toEqual([])
  })

  it('可以从列表移除', () => {
    rememberRemoteRoom(room)
    forgetRemoteRoom(room)
    expect(listRemoteRooms()).toEqual([])
  })

  it('存储被写坏时当作没有，而不是让整页崩掉', () => {
    localStorage.setItem('trpg_remote_rooms', '{ 这不是数组')
    expect(listRemoteRooms()).toEqual([])
    localStorage.setItem('trpg_remote_rooms', '{"a":1}')
    expect(listRemoteRooms()).toEqual([])
  })

  it('丢掉结构不对的条目，保留好的', () => {
    localStorage.setItem('trpg_remote_rooms', JSON.stringify([
      { invite: 'trpg:h:C', roomCode: 'C', hostId: 'h', lastSeenAt: 'x' },
      { 缺字段: true },
      null,
    ]))
    expect(listRemoteRooms()).toHaveLength(1)
  })
})

describe('从邀请码取房主标识', () => {
  it('取出公钥那一段', () => {
    expect(hostIdFromInvite('trpg:xu4vabc:K7M9PQ2R')).toBe('xu4vabc')
    expect(hostIdFromInvite('trpg:xu4vabc')).toBe('xu4vabc')
  })

  it('容忍聊天软件带来的引号', () => {
    expect(hostIdFromInvite('「trpg:xu4vabc:CODE」')).toBe('xu4vabc')
  })

  it('不是邀请码时给空串', () => {
    expect(hostIdFromInvite('192.168.1.5')).toBe('')
    expect(hostIdFromInvite('')).toBe('')
  })
})
