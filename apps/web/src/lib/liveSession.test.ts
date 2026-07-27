import { describe, expect, it } from 'vitest'

import { runLiveSession } from './liveSession'

/** 可控的事件流：手动推送、手动结束/报错，用来把时序钉死。 */
function makeStream<T>() {
  const queue: T[] = []
  let resolveNext: (() => void) | null = null
  let finished: 'end' | 'error' | null = null

  const push = (item: T) => {
    queue.push(item)
    resolveNext?.()
    resolveNext = null
  }
  const finish = (how: 'end' | 'error' = 'end') => {
    finished = how
    resolveNext?.()
    resolveNext = null
  }

  async function* iterate() {
    for (;;) {
      while (queue.length) yield queue.shift() as T
      if (finished === 'error') throw new Error('连接断开')
      if (finished === 'end') return
      await new Promise<void>((r) => { resolveNext = r })
    }
  }

  return { push, finish, iterate }
}

const noopSignal = new AbortController().signal

describe('runLiveSession', () => {
  it('先订阅、再对齐：resync 必须发生在连接建立之后', async () => {
    const order: string[] = []
    const stream = makeStream<string>()

    const run = runLiveSession<string>({
      connect: () => { order.push('connect'); return stream.iterate() },
      resync: async () => { order.push('resync') },
      onEvent: () => {},
      onDisconnect: () => {},
      isCancelled: () => order.includes('done'),
      wait: async () => {},
      signal: noopSignal,
    })
    await Promise.resolve()
    stream.finish()
    order.push('done')
    await run

    expect(order.slice(0, 2)).toEqual(['connect', 'resync'])
  })

  it('对齐期间到达的事件先缓冲，对齐完成后按序补投', async () => {
    const delivered: string[] = []
    const stream = makeStream<string>()
    let releaseResync: () => void = () => {}
    const resyncStarted = new Promise<void>((r) => { releaseResync = r })
    let finishResync: () => void = () => {}
    const resyncDone = new Promise<void>((r) => { finishResync = r })

    let stop = false
    const run = runLiveSession<string>({
      connect: () => stream.iterate(),
      resync: async () => { releaseResync(); await resyncDone },
      onEvent: (e) => delivered.push(e),
      onDisconnect: () => {},
      isCancelled: () => stop,
      wait: async () => {},
      signal: noopSignal,
    })

    await resyncStarted
    // 对齐尚未完成：这两条必须被缓冲，不能提前投递
    stream.push('a')
    stream.push('b')
    await new Promise((r) => setTimeout(r, 0))
    expect(delivered).toEqual([])

    finishResync()
    await new Promise((r) => setTimeout(r, 0))
    expect(delivered).toEqual(['a', 'b'])   // 顺序保持

    stream.push('c')                         // 缓冲期结束后直投
    await new Promise((r) => setTimeout(r, 0))
    expect(delivered).toEqual(['a', 'b', 'c'])

    stop = true
    stream.finish()
    await run
  })

  it('断线后重连，并且每次重连都重新对齐', async () => {
    const events: string[] = []
    let resyncCount = 0
    let attempt = 0
    const streams = [makeStream<string>(), makeStream<string>()]

    let stop = false
    const run = runLiveSession<string>({
      connect: () => streams[Math.min(attempt++, 1)].iterate(),
      resync: async () => { resyncCount += 1 },
      onEvent: (e) => events.push(e),
      onDisconnect: () => events.push('[断开]'),
      isCancelled: () => stop,
      wait: async () => {},
      signal: noopSignal,
    })

    await new Promise((r) => setTimeout(r, 0))
    streams[0].push('第一次连接')
    await new Promise((r) => setTimeout(r, 0))
    streams[0].finish('error')                // 模拟服务端进程被杀
    await new Promise((r) => setTimeout(r, 0))

    streams[1].push('重连之后')
    await new Promise((r) => setTimeout(r, 0))
    expect(events).toEqual(['第一次连接', '[断开]', '重连之后'])
    expect(resyncCount).toBe(2)               // 关键：重连也对齐，不只进页时对齐

    stop = true
    streams[1].finish()
    await run
  })

  it('流干净结束（服务端关闭而非报错）同样触发重连', async () => {
    let resyncCount = 0
    let attempt = 0
    const streams = [makeStream<string>(), makeStream<string>()]

    let stop = false
    const run = runLiveSession<string>({
      connect: () => streams[Math.min(attempt++, 1)].iterate(),
      resync: async () => { resyncCount += 1 },
      onEvent: () => {},
      onDisconnect: () => {},
      isCancelled: () => stop,
      wait: async () => {},
      signal: noopSignal,
    })

    await new Promise((r) => setTimeout(r, 0))
    streams[0].finish('end')                  // EOF，不是异常
    await new Promise((r) => setTimeout(r, 0))
    expect(resyncCount).toBe(2)

    stop = true
    streams[1].finish()
    await run
  })

  it('取消后不再重连', async () => {
    let resyncCount = 0
    const stream = makeStream<string>()
    let stop = false

    const run = runLiveSession<string>({
      connect: () => stream.iterate(),
      resync: async () => { resyncCount += 1 },
      onEvent: () => {},
      onDisconnect: () => {},
      isCancelled: () => stop,
      wait: async () => {},
      signal: noopSignal,
    })

    await new Promise((r) => setTimeout(r, 0))
    stop = true
    stream.finish('error')
    await run

    expect(resyncCount).toBe(1)
  })
})
