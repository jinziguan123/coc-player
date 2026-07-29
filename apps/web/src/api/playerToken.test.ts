import { beforeEach, describe, expect, it } from 'vitest'

import { getPlayerToken, setServerIdentity, setServerUrl } from './client'

/**
 * token 按主机隔离（ADR-007 未决项之一）。
 *
 * 此前只有一个全局 token，发给你连过的每一台主机——而它就存在对方库里的
 * `session_participants.owner_token`，等于把「你在别处的身份」交给了每一位房主。
 */
describe('玩家 token 按主机隔离', () => {
  beforeEach(() => {
    localStorage.clear()
    setServerUrl('')
  })

  it('本机与远程主机拿到的是不同的 token', () => {
    const local = getPlayerToken('')
    const hostA = getPlayerToken('http://192.168.1.5:8756')
    expect(hostA).not.toBe(local)
  })

  it('不同主机之间互不相干', () => {
    const a = getPlayerToken('http://192.168.1.5:8756')
    const b = getPlayerToken('http://100.101.102.103:8756')
    expect(a).not.toBe(b)
  })

  it('同一主机稳定复用同一个 token', () => {
    const first = getPlayerToken('http://192.168.1.5:8756')
    expect(getPlayerToken('http://192.168.1.5:8756')).toBe(first)
  })

  it('本机 token 沿用原有的 key，老存档的身份不变', () => {
    localStorage.setItem('trpg_player_token', 'legacy-local')
    expect(getPlayerToken('')).toBe('legacy-local')
  })

  it('升级迁移：当前正连着的主机沿用旧全局 token，不弄丢已入座的席位', () => {
    localStorage.setItem('trpg_player_token', 'legacy-global')
    setServerUrl('http://192.168.1.5:8756')

    // 正连着的这台：沿用（它本来就已拿到过这个 token，沿用不新增泄露）
    expect(getPlayerToken('http://192.168.1.5:8756')).toBe('legacy-global')
    // 其它主机：一律新发，不再共用
    expect(getPlayerToken('http://10.0.0.9:8756')).not.toBe('legacy-global')
  })

  it('缺省参数跟随当前主机设置', () => {
    setServerUrl('http://192.168.1.5:8756')
    expect(getPlayerToken()).toBe(getPlayerToken('http://192.168.1.5:8756'))
    setServerUrl('')
    expect(getPlayerToken()).toBe(getPlayerToken(''))
  })
})

/**
 * 内置直连的本地端口每次连接都重新分配，但房主是同一个。token 若按地址归属，
 * 朋友每次重连都会换一个新 token，表现就是**每次进来都掉席位**。
 */
describe('玩家 token 按稳定身份归属', () => {
  beforeEach(() => {
    localStorage.clear()
    setServerUrl('')
  })

  it('同一房主换了本地端口仍是同一个 token', () => {
    const host = 'netlink:xu4vpubkey'
    setServerIdentity('http://127.0.0.1:54321', host)
    const first = getPlayerToken('http://127.0.0.1:54321')

    // 重开应用，隧道分到了另一个端口
    setServerIdentity('http://127.0.0.1:61000', host)
    expect(getPlayerToken('http://127.0.0.1:61000')).toBe(first)
  })

  it('不同房主即便复用同一个本地端口也不共用 token', () => {
    setServerIdentity('http://127.0.0.1:54321', 'netlink:host-a')
    const a = getPlayerToken('http://127.0.0.1:54321')

    setServerIdentity('http://127.0.0.1:54321', 'netlink:host-b')
    expect(getPlayerToken('http://127.0.0.1:54321')).not.toBe(a)
  })

  it('没登记身份的地址沿用原有行为', () => {
    // 局域网直连不受影响：键仍是地址本身，老存档的席位不动。
    localStorage.setItem('trpg_player_token::http://192.168.1.5:8756', 'existing')
    expect(getPlayerToken('http://192.168.1.5:8756')).toBe('existing')
  })
})
