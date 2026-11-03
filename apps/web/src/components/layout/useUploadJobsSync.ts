import { useEffect } from 'react'
import { runningJobCount, useUploadJobsStore } from '@/stores/uploadJobsStore'

const POLL_MS = 1500

/**
 * 全局同步后台任务：挂在 AppShell 上。进入应用先拉一次（刷新页面后据此找回进行中的任务），
 * 有任务在跑、抽屉开着、或刚做了会启动后台任务的操作（``expect()`` 窗口内）时按间隔轮询；
 * 都不满足就停，不白跑请求。
 */
export function useUploadJobsSync() {
  const refresh = useUploadJobsStore((s) => s.refresh)
  const running = useUploadJobsStore((s) => runningJobCount(s.jobs))
  const open = useUploadJobsStore((s) => s.open)
  const expecting = useUploadJobsStore((s) => s.watchUntil > 0)
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!running && !open && !expecting) return
    const t = window.setInterval(() => { void refresh() }, POLL_MS)
    return () => window.clearInterval(t)
  }, [running, open, expecting, refresh])
}
