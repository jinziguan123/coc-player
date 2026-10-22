import { useEffect } from 'react'
import { runningJobCount, useUploadJobsStore } from '@/stores/uploadJobsStore'

const POLL_MS = 1500

/**
 * 全局同步模组解析任务：挂在 AppShell 上。进入应用先拉一次（刷新页面后据此找回进行中的解析），
 * 有任务在跑、或抽屉开着时按间隔轮询；全部结束且抽屉关着就停，不白跑请求。
 */
export function useUploadJobsSync() {
  const refresh = useUploadJobsStore((s) => s.refresh)
  const running = useUploadJobsStore((s) => runningJobCount(s.jobs))
  const open = useUploadJobsStore((s) => s.open)
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!running && !open) return
    const t = window.setInterval(() => { void refresh() }, POLL_MS)
    return () => window.clearInterval(t)
  }, [running, open, refresh])
}
