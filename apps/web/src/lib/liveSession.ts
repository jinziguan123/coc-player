/**
 * `/live` 常驻连接的重连循环。
 *
 * 从 GameSessionPage 抽出来，一是这段时序逻辑埋在两千行组件里没法测，
 * 二是它的正确性全在「顺序」上，靠人肉看容易看漏：
 *
 * 1. **先订阅、再对齐**。反过来（先拉历史/快照再订阅）会丢掉两者之间产生的事件，
 *    这个窗口在跨网重连时并不窄。所以订阅先开着，期间到达的事件进缓冲区。
 * 2. **对齐完成后回放缓冲**。log 类事件按 id 去重、sync/stream 类后到者覆盖，
 *    所以重复应用是安全的——这正是三分类的意义所在。
 * 3. **断开后退避重连，并且每次重连都重新对齐**。此前只有进页时对齐一次，
 *    断线期间战斗开打/结束、别人确认了回合，HUD 会一直停在旧状态。退避按连续失败
 *    次数指数增长并封顶，房主机器睡眠或隧道断开时不至于每 1.5 秒敲一次门。
 * 4. **被永久拒绝就停**。403（被踢、被吊销）与 404（房间已删除）重连多少次都一样，
 *    交给 onGone 提示去处，不再无声地转圈。
 */

/**
 * 服务端明确表示「这条连接再也连不上了」：403（被踢、接入被吊销、不再是成员）、
 * 404（房间已被删除）。与网络抖动不同，重连只会无限重复同一个错误，所以要停下来告诉人。
 */
export class LiveGoneError extends Error {
  readonly status: 403 | 404
  constructor(status: 403 | 404) {
    super(status === 404 ? '房间已不存在' : '你已不在这个房间')
    this.name = 'LiveGoneError'
    this.status = status
  }
}

export function isLiveGone(e: unknown): e is LiveGoneError {
  return e instanceof LiveGoneError
}

/** 第 attempt 次（从 0 起）连续失败后的等待：指数退避并封顶。 */
export function backoffDelay(attempt: number, baseMs: number, maxMs: number): number {
  return Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt))
}

export interface LiveSessionDeps<T> {
  /** 打开一条事件流。实现方负责在 signal abort 时中止。 */
  connect: (signal: AbortSignal) => AsyncIterable<T>
  /** 与服务端对齐（历史 + sync 类状态快照）。 */
  resync: () => Promise<void>
  /** 投递一条事件。 */
  onEvent: (event: T) => void
  /** 连接断开时调用（用于把界面切到「连接中…」）。 */
  onDisconnect: () => void
  /** 连接被服务端永久拒绝（见 LiveGoneError）：循环随即退出，不再重连。 */
  onGone?: (error: LiveGoneError) => void
  /** 外部取消信号：为 true 时循环退出。 */
  isCancelled: () => boolean
  /** 退避等待，注入以便测试。 */
  wait: (ms: number) => Promise<void>
  signal: AbortSignal
  /** 首次重连的等待；之后每次连续失败翻倍，直到 maxBackoffMs。 */
  backoffMs?: number
  maxBackoffMs?: number
}

export async function runLiveSession<T>(deps: LiveSessionDeps<T>): Promise<void> {
  const { connect, resync, onEvent, onDisconnect, onGone, isCancelled, wait, signal } = deps
  const backoffMs = deps.backoffMs ?? 1500
  const maxBackoffMs = deps.maxBackoffMs ?? 30000
  // 连续失败次数：一次完整对齐成功（连接确实建立起来了）就清零。
  let failures = 0

  while (!isCancelled()) {
    // 每轮一条独立的连接：对齐失败时要把这一轮的连接关掉再重连，否则旧连接在后台
    // 一直挂着（缓冲区只进不出），每失败一次就多泄漏一条。
    const round = new AbortController()
    const forward = () => round.abort()
    signal.addEventListener('abort', forward)
    // 「永久拒绝」可能来自连接那一侧，而此时 resync 多半也失败了——catch 收到的未必是
    // 连接的错误，所以单独记一份。用对象承载，免得闭包里的赋值被类型收窄吃掉。
    const outcome: { gone: LiveGoneError | null } = { gone: null }
    // 只在连接被永久拒绝时兑现，其余情况永不兑现——供退避等待时竞速用
    const never = new Promise<void>(() => {})
    let goneLater: Promise<void> = never
    try {
      let buffering = true
      const buffered: T[] = []
      const pump = (async () => {
        for await (const event of connect(round.signal)) {
          if (isCancelled()) break
          if (buffering) buffered.push(event)
          else onEvent(event)
        }
      })()
      // 缓冲期内连接就断的话，pump 会先于下面的 await 拒绝；先挂个处理器，免得它在
      // 这段窗口里冒泡成 unhandledrejection（下面的 await pump 仍会照常抛给 catch）。
      goneLater = pump.then(
        () => never,
        (e) => {
          if (!isLiveGone(e)) return never
          outcome.gone = e
        },
      )

      await resync()
      failures = 0
      buffering = false
      if (!isCancelled()) for (const event of buffered) onEvent(event)

      await pump
    } catch (e) {
      if (isLiveGone(e)) outcome.gone = e
      /* 其余情况：连接断开或被取消，走下面的退避重连 */
    }
    const closeRound = () => {
      round.abort()
      signal.removeEventListener('abort', forward)
    }
    if (isCancelled()) {
      closeRound()
      break
    }
    // pump 的拒绝处理器在微任务里执行；让出一拍，确保它已经记下结果
    await Promise.resolve()
    if (!outcome.gone) {
      onDisconnect()
      // 等待期间连接那一侧才返回 403/404（对齐先失败、连接请求还在路上）也要能认出来，
      // 所以这一轮的连接等退避结束再关。
      await Promise.race([wait(backoffDelay(failures, backoffMs, maxBackoffMs)), goneLater])
    }
    closeRound()
    if (outcome.gone) {
      onGone?.(outcome.gone)
      return
    }
    failures += 1
  }
}
