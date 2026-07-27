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
 *    断线期间战斗开打/结束、别人确认了回合，HUD 会一直停在旧状态。
 */

export interface LiveSessionDeps<T> {
  /** 打开一条事件流。实现方负责在 signal abort 时中止。 */
  connect: (signal: AbortSignal) => AsyncIterable<T>
  /** 与服务端对齐（历史 + sync 类状态快照）。 */
  resync: () => Promise<void>
  /** 投递一条事件。 */
  onEvent: (event: T) => void
  /** 连接断开时调用（用于把界面切到「连接中…」）。 */
  onDisconnect: () => void
  /** 外部取消信号：为 true 时循环退出。 */
  isCancelled: () => boolean
  /** 退避等待，注入以便测试。 */
  wait: (ms: number) => Promise<void>
  signal: AbortSignal
  backoffMs?: number
}

export async function runLiveSession<T>(deps: LiveSessionDeps<T>): Promise<void> {
  const { connect, resync, onEvent, onDisconnect, isCancelled, wait, signal } = deps
  const backoffMs = deps.backoffMs ?? 1500

  while (!isCancelled()) {
    try {
      let buffering = true
      const buffered: T[] = []
      const pump = (async () => {
        for await (const event of connect(signal)) {
          if (isCancelled()) break
          if (buffering) buffered.push(event)
          else onEvent(event)
        }
      })()
      // 缓冲期内连接就断的话，pump 会先于下面的 await 拒绝；先挂个处理器，免得它在
      // 这段窗口里冒泡成 unhandledrejection（下面的 await pump 仍会照常抛给 catch）。
      pump.catch(() => {})

      await resync()
      buffering = false
      if (!isCancelled()) for (const event of buffered) onEvent(event)

      await pump
    } catch {
      /* 连接断开或被取消：走下面的退避重连 */
    }
    if (isCancelled()) break
    onDisconnect()
    await wait(backoffMs)
  }
}
