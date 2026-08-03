import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ImageSlot } from './ImageSlot'

vi.mock('@/api/client', () => ({
  api: { post: vi.fn() },
  uploadFile: vi.fn(),
  getServerUrl: () => '',
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const { api, uploadFile } = await import('@/api/client')

const props = {
  moduleId: 'm1', kind: 'scene' as const, itemId: 's1',
  field: 'image' as const, alt: '教堂', onChange: vi.fn(),
}

beforeEach(() => { vi.clearAllMocks() })

describe('模组配图槽位', () => {
  it('没有配图时给出占位和「AI 生成」入口，而不是整个不渲染', () => {
    render(<ImageSlot {...props} />)
    expect(screen.getByText('暂无配图')).toBeInTheDocument()
    // 之前 `item.image &&` 短路掉了整块，没图的条目连生成第一张的入口都没有
    expect(screen.getByRole('button', { name: /AI 生成/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /上传/ })).toBeEnabled()
  })

  it('有配图时显示图片，并把按钮换成「重新生成」', () => {
    render(<ImageSlot {...props} src="/api/images/a.jpg" />)
    expect(screen.getByAltText('教堂')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新生成/ })).toBeInTheDocument()
  })

  it('重新生成必须带 force：不带的话后端是自愈语义，会把原图返回来', async () => {
    vi.mocked(api.post).mockResolvedValue({ url: '/api/images/new.jpg' })
    const onChange = vi.fn()
    render(<ImageSlot {...props} src="/api/images/old.jpg" onChange={onChange} />)

    await userEvent.click(screen.getByRole('button', { name: /重新生成/ }))

    expect(api.post).toHaveBeenCalledWith('/modules/m1/images/regenerate',
      expect.objectContaining({ kind: 'scene', item_id: 's1', field: 'image', force: true }))
    expect(onChange).toHaveBeenCalledWith('/api/images/new.jpg')
  })

  it('上传把文件与目标条目一起发出去，成功后回调新地址', async () => {
    vi.mocked(uploadFile).mockResolvedValue({ url: '/api/images/up.jpg' })
    const onChange = vi.fn()
    const { container } = render(<ImageSlot {...props} onChange={onChange} />)

    const input = container.querySelector('input[type=file]') as HTMLInputElement
    await userEvent.upload(input, new File(['x'], 'p.png', { type: 'image/png' }))

    expect(uploadFile).toHaveBeenCalledWith('/modules/m1/images/upload', expect.any(FormData))
    const form = vi.mocked(uploadFile).mock.calls[0][1] as FormData
    expect(form.get('kind')).toBe('scene')
    expect(form.get('item_id')).toBe('s1')
    expect(form.get('field')).toBe('image')
    expect(onChange).toHaveBeenCalledWith('/api/images/up.jpg')
  })

  it('模组或条目尚未保存时置灰，别让用户点了才吃 404', () => {
    const { rerender } = render(<ImageSlot {...props} moduleId={undefined} />)
    expect(screen.getByRole('button', { name: /AI 生成/ })).toBeDisabled()
    rerender(<ImageSlot {...props} itemId="" />)
    expect(screen.getByRole('button', { name: /上传/ })).toBeDisabled()
  })
})
