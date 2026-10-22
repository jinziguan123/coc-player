import { create } from 'zustand'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import { useModuleStore } from './moduleStore'

/** 后端模组上传解析任务（/modules/upload/jobs）。任务表在后端内存里：刷新页面仍在，重启后端即清空。 */
export interface UploadJob {
  id: string
  status: 'running' | 'done' | 'failed'
  stage: string
  percent: number
  detail: string
  result: { id?: string; title?: string } | null
  files: string[]
  created_at: number
}

interface UploadJobsStore {
  jobs: UploadJob[]
  open: boolean
  /** 拉一次任务列表；已在本页见过「进行中」的任务这次结束了，就弹完成/失败提示并刷新模组列表。 */
  refresh: () => Promise<void>
  /** 新提交了一个任务：立刻拉一次，并确保轮询在跑。 */
  track: (jobId: string) => Promise<void>
  dismiss: (jobId: string) => Promise<void>
  clearFinished: () => Promise<void>
  setOpen: (open: boolean) => void
}

// 本页生命周期内见过的「进行中」任务：只对它们弹结束提示——刷新后才看到的旧结果不再重复打扰
const seenRunning = new Set<string>()

export const useUploadJobsStore = create<UploadJobsStore>((set, get) => ({
  jobs: [],
  open: false,

  refresh: async () => {
    let jobs: UploadJob[]
    try {
      jobs = await localApi.get<UploadJob[]>('/modules/upload/jobs')
    } catch {
      return   // 连不上本机后端（如作为客人连到别人的主机）：保持现状，不打扰
    }
    let finished = false
    for (const job of jobs) {
      if (job.status === 'running') {
        seenRunning.add(job.id)
        continue
      }
      if (!seenRunning.delete(job.id)) continue
      finished = true
      if (job.status === 'done') toast.success(`模组「${job.result?.title ?? ''}」解析完成`)
      else toast.error(job.detail || '模组解析失败')
    }
    set({ jobs })
    if (finished) void useModuleStore.getState().fetchModules()
  },

  track: async (jobId) => {
    seenRunning.add(jobId)
    await get().refresh()
  },

  dismiss: async (jobId) => {
    try {
      await localApi.delete(`/modules/upload/jobs/${jobId}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '移除失败')
      return
    }
    set((s) => ({ jobs: s.jobs.filter((j) => j.id !== jobId) }))
  },

  clearFinished: async () => {
    const finished = get().jobs.filter((j) => j.status !== 'running')
    await Promise.allSettled(finished.map((j) => localApi.delete(`/modules/upload/jobs/${j.id}`)))
    set((s) => ({ jobs: s.jobs.filter((j) => j.status === 'running') }))
  },

  setOpen: (open) => set({ open }),
}))

export const runningJobCount = (jobs: UploadJob[]) => jobs.filter((j) => j.status === 'running').length
