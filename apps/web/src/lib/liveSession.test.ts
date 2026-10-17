import { describe, expect, it } from 'vitest'

import { backoffDelay, LiveGoneError, runLiveSession } from './liveSession'

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

/** 一条一读就失败的流（next 返回 rejected promise）：模拟连接被拒或断开。 */
function failingStream<T>(failure: () => Promise<never>): AsyncIterable<T> {
  return { [Symbol.asyncIterator]: () => ({ next: failure }) }
}

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

  it('连接被永久拒绝（403/404）就停止重连并交给 onGone', async () => {
    let connects = 0
    const gone: number[] = []
    let disconnects = 0
    await runLiveSession<string>({
      connect: () => {
        connects += 1
        return failingStream<string>(() => Promise.reject(new LiveGoneError(404)))
      },
      // 房间被删时历史接口也会失败：catch 收到的是它，而不是连接那一侧的 404
      resync: async () => { throw new Error('历史拉取失败') },
      onEvent: () => {},
      onDisconnect: () => { disconnects += 1 },
      onGone: (e) => gone.push(e.status),
      isCancelled: () => false,
      wait: async () => {},
      signal: noopSignal,
    })
    expect(connects).toBe(1)
    expect(gone).toEqual([404])
    expect(disconnects).toBe(0)
  })

  it('对齐先失败、连接稍后才被拒：在退避等待期间认出来', async () => {
    let rejectConnect: (e: unknown) => void = () => {}
    const pending = new Promise<never>((_, reject) => { rejectConnect = reject })
    const gone: number[] = []
    let waited = 0
    await runLiveSession<string>({
      connect: () => failingStream<string>(() => pending),
      resync: async () => { throw new Error('先失败') },
      onEvent: () => {},
      onDisconnect: () => {},
      onGone: (e) => gone.push(e.status),
      isCancelled: () => false,
      wait: () => {
        waited += 1
        rejectConnect(new LiveGoneError(403))
        return new Promise(() => {})   // 退避尚未结束
      },
      signal: noopSignal,
    })
    expect(waited).toBe(1)
    expect(gone).toEqual([403])
  })

  it('普通断线按连续失败次数指数退避，连上之后清零', async () => {
    const waits: number[] = []
    let attempt = 0
    let stop = false
    await runLiveSession<string>({
      connect: () => failingStream<string>(() => Promise.reject(new Error('网络抖动'))),
      resync: async () => {
        attempt += 1
        if (attempt === 4) return            // 第 4 次连上并对齐成功
        throw new Error('对齐失败')
      },
      onEvent: () => {},
      onDisconnect: () => {},
      isCancelled: () => stop,
      wait: async (ms) => {
        waits.push(ms)
        if (waits.length >= 5) stop = true
      },
      signal: noopSignal,
      backoffMs: 1000,
      maxBackoffMs: 3000,
    })
    expect(waits).toEqual([1000, 2000, 3000, 1000, 2000])
  })

  it('backoffDelay 封顶', () => {
    expect(backoffDelay(0, 1500, 30000)).toBe(1500)
    expect(backoffDelay(3, 1500, 30000)).toBe(12000)
    expect(backoffDelay(10, 1500, 30000)).toBe(30000)
  })
})
