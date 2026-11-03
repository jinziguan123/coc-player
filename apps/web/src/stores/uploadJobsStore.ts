import { create } from 'zustand'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import { useModuleStore } from './moduleStore'

/**
 * 后台任务（/tasks）：「解析进度」抽屉统一展示所有在后台跑的长任务——模组上传解析、规则书导入、
 * 模组原文索引、检索模型下载、切换检索模型后的索引重建。任务表在后端内存里：刷新页面仍在，重启后端即清空。
 */
export interface UploadJob {
  /** 模组上传解析任务是 `module:<job_id>` */
  id: string
  kind: 'module' | 'rulebook' | 'module_index' | 'embed_download' | 'embed_rebuild' | string
  title: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  stage: string
  /** 有总量时的百分比；null＝阶段性步骤，显示不定进度 */
  percent: number | null
  detail: string
  link: { to: string; label: string } | null
  created_at: number
}

/** 启动了后台任务后，至少盯这么久（毫秒）：有的任务在后台线程起跑时才登记，晚于请求返回 */
export const EXPECT_WINDOW_MS = 15_000

interface UploadJobsStore {
  jobs: UploadJob[]
  open: boolean
  /** 到这个时间点（ms）之前，即使没看到进行中的任务也继续轮询 */
  watchUntil: number
  /** 拉一次任务列表；已在本页见过「进行中」的任务这次结束了，就弹完成/失败提示（模组还会刷新模组列表）。 */
  refresh: () => Promise<void>
  /** 新提交了一个任务：立刻拉一次，并确保轮询在跑。 */
  track: (taskId: string) => Promise<void>
  /**
   * 刚做了会启动后台任务的操作（导入、重建索引、下载、切换模型……）：立刻拉一次，并在接下来
   * 一段时间里持续轮询。不靠「已经看到进行中的任务」才开始轮询——那样晚登记的任务要刷新页面才出现。
   */
  expect: () => Promise<void>
  dismiss: (taskId: string) => Promise<void>
  clearFinished: () => Promise<void>
  setOpen: (open: boolean) => void
}

// 本页生命周期内见过的「进行中」任务：只对它们弹结束提示——刷新后才看到的旧结果不再重复打扰
const seenRunning = new Set<string>()

function announce(job: UploadJob) {
  if (job.status === 'cancelled') return
  if (job.status === 'done') {
    toast.success(job.kind === 'module' ? `模组「${job.title}」解析完成` : `${job.title}：${job.stage || '完成'}`)
  } else {
    toast.error(job.detail || (job.kind === 'module' ? '模组解析失败' : `${job.title}失败`))
  }
}

export const useUploadJobsStore = create<UploadJobsStore>((set, get) => ({
  jobs: [],
  open: false,
  watchUntil: 0,

  refresh: async () => {
    let jobs: UploadJob[]
    try {
      jobs = await localApi.get<UploadJob[]>('/tasks')
    } catch {
      return   // 连不上本机后端（如作为客人连到别人的主机）：保持现状，不打扰
    }
    if (!Array.isArray(jobs)) return   // 响应不是任务列表（如本机后端版本不符）：同上
    let moduleFinished = false
    for (const job of jobs) {
      if (job.status === 'running') {
        seenRunning.add(job.id)
        continue
      }
      if (!seenRunning.delete(job.id)) continue
      announce(job)
      if (job.kind === 'module' || job.kind === 'module_index') moduleFinished = true
    }
    set({ jobs })
    if (moduleFinished) void useModuleStore.getState().fetchModules()
  },

  track: (taskId) => {
    seenRunning.add(taskId)
    return get().expect()
  },

  expect: () => {
    const until = Date.now() + EXPECT_WINDOW_MS
    set({ watchUntil: until })
    // 窗口到期把标记清掉，让同步钩子重新判断要不要继续轮询
    setTimeout(() => { if (get().watchUntil === until) set({ watchUntil: 0 }) }, EXPECT_WINDOW_MS + 50)
    return get().refresh()
  },

  dismiss: async (taskId) => {
    try {
      await localApi.delete(`/tasks/${encodeURIComponent(taskId)}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '移除失败')
      return
    }
    set((s) => ({ jobs: s.jobs.filter((j) => j.id !== taskId) }))
  },

  clearFinished: async () => {
    const finished = get().jobs.filter((j) => j.status !== 'running')
    await Promise.allSettled(finished.map((j) => localApi.delete(`/tasks/${encodeURIComponent(j.id)}`)))
    set((s) => ({ jobs: s.jobs.filter((j) => j.status === 'running') }))
  },

  setOpen: (open) => set({ open }),
}))

export const runningJobCount = (jobs: UploadJob[]) => jobs.filter((j) => j.status === 'running').length
