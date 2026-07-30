import { useEffect } from 'react'

import { getServerUrl } from '@/api/client'
import { syncCharactersBackFromHost } from './syncBack'

/**
 * 在合适的时机把参战结果拉回本机角色卡。
 *
 * 时机取「进入时 + 离开时」两头各拉一次：
 * - **离开时**是主要时机（本局刚打完，副本上是最新状态）；
 * - **进入时**补上一次遗漏——上回可能是异常退出（关窗口、掉线、房主先退），
 *   那时来不及同步。拉取是幂等全量覆盖，多拉一次没有副作用。
 *
 * 不做定时轮询：跑团中途的中间状态没有同步价值，而且每次都写盘。
 */
export function useSyncBackOnVisit() {
  useEffect(() => {
    // 本机模式不存在副本，直接跳过。
    if (!getServerUrl()) return
    void syncCharactersBackFromHost()

    return () => {
      // 卸载时再拉一次：这是「一局打完离开房间」的主要路径。
      // 此处拿不到结果也不提示——组件已经在卸载了。
      void syncCharactersBackFromHost()
    }
  }, [])
}
