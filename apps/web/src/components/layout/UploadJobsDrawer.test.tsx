import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('@/api/client', () => ({
  api: { get: vi.fn() },
  localApi: { get: (url: string) => get(url), delete: vi.fn() },
}))
const toastSuccess = vi.fn()
const toastError = vi.fn()
vi.mock('sonner', () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }))

import { useUploadJobsStore, type UploadJob } from '@/stores/uploadJobsStore'
import { Sidebar } from './Sidebar'
import { UploadJobsDrawer } from './UploadJobsDrawer'

const running: UploadJob = {
  id: 'j1', status: 'running', stage: '读取文件内容（大 PDF 抽图需要一会儿）', percent: 3,
  detail: '', result: null, files: ['陵墓.pdf'], created_at: 1_700_000_000,
}

beforeEach(() => {
  get.mockReset()
  toastSuccess.mockReset()
  toastError.mockReset()
  useUploadJobsStore.setState({ jobs: [], open: false })
})

describe('解析进度', () => {
  it('侧边栏角标显示进行中的任务数，抽屉列出阶段与百分比', () => {
    useUploadJobsStore.setState({ jobs: [running], open: true })
    render(
      <MemoryRouter>
        <Sidebar />
        <UploadJobsDrawer />
      </MemoryRouter>,
    )
    expect(screen.getByLabelText('1 个进行中')).toHaveTextContent('1')
    expect(screen.getByRole('dialog', { name: '解析进度' })).toBeInTheDocument()
    expect(screen.getByText('陵墓.pdf')).toBeInTheDocument()
    expect(screen.getByText(running.stage)).toBeInTheDocument()
    expect(screen.getByText('3%')).toBeInTheDocument()
    // 进行中的任务不给「移除」——删了它仍在后台跑，只是看不见了
    expect(screen.queryByLabelText('从列表移除')).toBeNull()
  })

  it('刷新后找回任务；只对本页见过「进行中」的任务弹结束提示', async () => {
    // 刷新页面后第一次拉取：一个已结束的旧任务 + 一个进行中的
    const oldDone: UploadJob = { ...running, id: 'old', status: 'done', result: { id: 'm0', title: '旧模组' } }
    get.mockResolvedValueOnce([running, oldDone])
    await act(() => useUploadJobsStore.getState().refresh())
    expect(useUploadJobsStore.getState().jobs).toHaveLength(2)
    expect(toastSuccess).not.toHaveBeenCalled()       // 旧结果不重复打扰

    get.mockResolvedValueOnce([{ ...running, status: 'done', result: { id: 'm1', title: '陵墓' } }, oldDone])
    await act(() => useUploadJobsStore.getState().refresh())
    expect(toastSuccess).toHaveBeenCalledWith('模组「陵墓」解析完成')
  })
})
