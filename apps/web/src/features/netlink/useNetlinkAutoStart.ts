import { useEffect, useRef } from 'react'

import { localApi } from '@/api/client'
import { netlinkAvailable, netlinkStart, netlinkStatus } from '@/api/netlink'

/**
 * 开机自动恢复内置直连。
 *
 * iroh endpoint 是运行时对象，进程一重启就没了，但房主「想开着」的意愿存了盘
 * （Rust 侧的 wanted 标记）。这里在应用起来后把它开回来。
 *
 * **必须挂在全局**（AppShell），不能放在设置页的面板里——那个组件只有打开
 * 「设置 → 联机」才挂载，于是隧道要等房主恰好去翻那一页才启动。实际表现是
 * 客人拿着正确的房间码怎么都进不来，而房主一打开设置页对方就突然进来了。
 */
export function useNetlinkAutoStart() {
  const attempted = useRef(false)

  useEffect(() => {
    if (!netlinkAvailable() || attempted.current) return
    let cancelled = false

    void (async () => {
      try {
        const status = await netlinkStatus()
        if (cancelled || attempted.current) return
        // 上次是显式关掉的，或者已经开着了，都不用管。
        if (!status.wanted || status.hosting) return

        const port = await resolveBackendPort()
        if (cancelled || !port) return
        attempted.current = true
        await netlinkStart(port)
      } catch {
        // 静默：房主没有主动操作，不该被一个他没点过的动作弹错误。
        // 设置页里的开关仍然可用，他可以自己开。
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])
}

/** 隧道要反代到本机后端，得知道它监听在哪个端口。 */
async function resolveBackendPort(): Promise<number | null> {
  const net = await localApi.get<{ port: number | null }>('/net')
  if (net.port) return net.port
  // 打包版一定有 port；开发态后端不知道自己被绑在哪，而 `pnpm tauri dev` 下
  // 页面在 vite(5173)、后端在 8000，不能拿页面端口顶替。
  if (import.meta.env.DEV) return 8000
  return Number(window.location.port) || null
}
