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
  id: 'module:j1', kind: 'module', title: '陵墓.pdf', status: 'running',
  stage: '读取文件内容（大 PDF 抽图需要一会儿）', percent: 3, detail: '', link: null, created_at: 1_700_000_000,
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
    const oldDone: UploadJob = { ...running, id: 'module:old', status: 'done', title: '旧模组', link: { to: '/modules/m0', label: '查看模组' } }
    get.mockResolvedValueOnce([running, oldDone])
    await act(() => useUploadJobsStore.getState().refresh())
    expect(useUploadJobsStore.getState().jobs).toHaveLength(2)
    expect(toastSuccess).not.toHaveBeenCalled()       // 旧结果不重复打扰

    get.mockResolvedValueOnce([{ ...running, status: 'done', title: '陵墓', link: { to: '/modules/m1', label: '查看模组' } }, oldDone])
    await act(() => useUploadJobsStore.getState().refresh())
    expect(toastSuccess).toHaveBeenCalledWith('模组「陵墓」解析完成')
    expect(get).toHaveBeenCalledWith('/tasks')
  })

  it('规则书导入、检索模型等其他后台任务也在这里：无总量时不显示百分比，完成后给跳转', async () => {
    const rulebook: UploadJob = {
      id: 'rulebook:1', kind: 'rulebook', title: '规则书《守秘人规则书》导入', status: 'running',
      stage: '解析 PDF', percent: null, detail: '', link: { to: '/rules?tab=books', label: '查看规则书' }, created_at: 1,
    }
    const download: UploadJob = {
      id: 'embed_download:2', kind: 'embed_download', title: '下载检索模型 bge-large', status: 'running',
      stage: '下载中 120/327 MB', percent: 36, detail: '', link: null, created_at: 2,
    }
    useUploadJobsStore.setState({ jobs: [download, rulebook], open: true })
    render(<MemoryRouter><Sidebar /><UploadJobsDrawer /></MemoryRouter>)
    expect(screen.getByLabelText('2 个进行中')).toHaveTextContent('2')
    expect(screen.getByText('解析 PDF')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: /守秘人规则书/ })).not.toHaveAttribute('aria-valuenow')
    expect(screen.getByText('36%')).toBeInTheDocument()

    get.mockResolvedValueOnce([download, rulebook])
    await act(() => useUploadJobsStore.getState().refresh())
    get.mockResolvedValueOnce([download, { ...rulebook, status: 'done', stage: '完成：400 页 / 3273 片段' }])
    await act(() => useUploadJobsStore.getState().refresh())
    expect(toastSuccess).toHaveBeenCalledWith('规则书《守秘人规则书》导入：完成：400 页 / 3273 片段')
    expect(screen.getByRole('link', { name: /查看规则书/ })).toHaveAttribute('href', '/rules?tab=books')
  })
})
