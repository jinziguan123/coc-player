import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { ModuleDetailPage } from './ModuleDetailPage'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  getServerUrl: () => '',
}))

vi.mock('@/components/game/HexSandbox', () => ({
  HexSandbox: ({
    locations,
    selectedIds = [],
    onToggleScene,
  }: {
    locations: { id: string; name: string; map?: { biome?: string } | null }[]
    selectedIds?: readonly string[]
    onToggleScene?: (id: string) => void
  }) => (
    <div data-testid="hex-sandbox">
      {locations.map((location) => (
        <button key={location.id} onClick={() => onToggleScene?.(location.id)}>
          选择{location.name}：{location.map?.biome || 'plain'}
        </button>
      ))}
      <span data-testid="sandbox-selection">{selectedIds.join(',')}</span>
    </div>
  ),
}))

const mockGet = vi.mocked(api.get)
const mockPost = vi.mocked(api.post)
const mockPut = vi.mocked(api.put)

describe('模组详情图片', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({
      id: 'module-1',
      title: '常暗之箱',
      rule_system: 'coc',
      description: '测试模组',
      world_setting: {},
      scenes: [
        { id: 'scene-1', name: '六号车厢', image: '/api/images/scene.jpg', map: { q: 0, r: 0, biome: 'plain' } },
        { id: 'scene-2', name: '餐车', map: { q: 1, r: 0, biome: 'urban' } },
      ],
      npcs: [{ id: 'npc-1', name: '乘务员', portrait: '/api/images/npc.jpg' }],
      clues: [{ id: 'clue-1', name: '染血车票', image: '/api/images/clue.jpg' }],
      triggers: [],
      truth: '',
    })
    mockPut.mockResolvedValue({
      id: 'module-1',
      title: '常暗之箱',
      rule_system: 'coc',
      description: '测试模组',
      world_setting: {},
      scenes: [],
      npcs: [],
      clues: [],
      triggers: [],
      truth: '',
    })
  })

  it('查看模组时展示场景、NPC 和线索图片', async () => {
    render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('img', { name: '六号车厢' })).toHaveAttribute(
      'src', expect.stringContaining('/api/images/scene.jpg?verify='),
    )
    expect(screen.getByRole('img', { name: '乘务员' })).toHaveAttribute(
      'src', expect.stringContaining('/api/images/npc.jpg?verify='),
    )
    expect(screen.getByRole('img', { name: '染血车票' })).toHaveAttribute(
      'src', expect.stringContaining('/api/images/clue.jpg?verify='),
    )
  })

  it('确认 AI 补全后调用接口并重新加载模组', async () => {
    const user = userEvent.setup()
    mockPost.mockResolvedValue({ updated: true })
    render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '沙盘' }))
    // 查看态是只读的：AI 补全会立刻写库，不该在这儿点得动
    expect(screen.queryByRole('button', { name: 'AI 补全地貌与连接' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'AI 生成氛围底图' })).toBeNull()

    await user.click(screen.getByRole('button', { name: '编辑' }))
    await user.click(screen.getByRole('button', { name: 'AI 补全地貌与连接' }))
    expect(screen.getByText('已有连接不会被删除', { exact: false })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '开始补全' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/modules/module-1/map/enrich'))
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2))
  })

  it('沙盘编辑态支持单节点和批量修改地貌', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '沙盘' }))
    await user.click(screen.getByRole('button', { name: '编辑' }))
    await user.click(screen.getByRole('button', { name: /选择六号车厢/ }))
    expect(screen.getByText('已选 1 个地点')).toBeInTheDocument()

    expect(screen.queryByRole('combobox', { name: '设置选中节点地貌' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /拖入沙盘或点击使用地貌：密林/ }))
    expect(screen.getByRole('button', { name: /选择六号车厢：forest/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '全选地图节点' }))
    expect(screen.getByText('已选 2 个地点')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /拖入沙盘或点击使用地貌：水域/ }))
    expect(screen.getByRole('button', { name: /选择六号车厢：water/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /选择餐车：water/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalledOnce())
    const payload = mockPut.mock.calls[0][1] as { scenes: { map?: { biome?: string } }[] }
    expect(payload.scenes.map((scene) => scene.map?.biome)).toEqual(['water', 'water'])
  })

  it('沙盘编辑取消会恢复原地貌且不会保存', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '沙盘' }))
    await user.click(screen.getByRole('button', { name: '编辑' }))
    await user.click(screen.getByRole('button', { name: /选择六号车厢/ }))
    await user.click(screen.getByRole('button', { name: /拖入沙盘或点击使用地貌：密林/ }))
    expect(screen.getByRole('button', { name: /选择六号车厢：forest/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.getByRole('button', { name: /选择六号车厢：plain/ })).toBeInTheDocument()
    expect(mockPut).not.toHaveBeenCalled()
  })

  it('场景卡地貌修改会同步保存到统一地图节点', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '编辑' }))
    screen.getByRole('combobox', { name: '地貌：六号车厢' }).focus()
    await user.keyboard('{Enter}')
    fireEvent.click(await screen.findByRole('option', { name: '密林' }))
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockPut).toHaveBeenCalledOnce())
    const payload = mockPut.mock.calls[0][1] as {
      scenes: { id: string; map?: { biome?: string } }[]
      map_nodes: { scene_id?: string | null; biome?: string }[]
    }
    expect(payload.scenes[0].map?.biome).toBe('forest')
    expect(payload.map_nodes.find((node) => node.scene_id === 'scene-1')?.biome).toBe('forest')
  })

  it('右侧地貌样例可直接替换选中节点并支持道路', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/modules/module-1'] }>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '沙盘' }))
    await user.click(screen.getByRole('button', { name: '编辑' }))
    await user.click(screen.getByRole('button', { name: /选择六号车厢/ }))
    await user.click(screen.getByRole('button', { name: /拖入沙盘或点击使用地貌：道路/ }))

    expect(screen.getByRole('button', { name: /选择六号车厢：road/ })).toBeInTheDocument()
  })

  it('右侧连接编辑器可双向新增和删除连接', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/modules/module-1'] }>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '沙盘' }))
    await user.click(screen.getByRole('button', { name: '编辑' }))
    await user.click(screen.getByRole('button', { name: /选择六号车厢/ }))
    screen.getByRole('combobox', { name: '连接目标' }).focus()
    await user.keyboard('{Enter}')
    fireEvent.click(await screen.findByRole('option', { name: '餐车' }))
    await user.click(screen.getByRole('button', { name: '新增连接' }))
    expect(screen.queryByText('暂无连接')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '删除连接：餐车' }))
    expect(screen.getByText('暂无连接')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalledOnce())
    const payload = mockPut.mock.calls[0][1] as { scenes: { connections?: string[] }[] }
    expect(payload.scenes[0].connections).toEqual([])
    expect(payload.scenes[1].connections).toEqual([])
  })
})

describe('查看模组时页面是只读的', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({
      id: 'module-1', title: '常暗之箱', rule_system: 'coc', description: '',
      world_setting: {}, scenes: [{ id: 'scene-1', name: '六号车厢', image: '/api/images/s.jpg' }],
      npcs: [], clues: [], triggers: [], truth: '',
      character_guidance: { summary: '普通现代人' },
    })
  })

  const renderPage = () => render(
    <MemoryRouter initialEntries={['/modules/module-1']}>
      <Routes><Route path="/modules/:id" element={<ModuleDetailPage />} /></Routes>
    </MemoryRouter>,
  )

  it('配图只看不改：重新生成与上传都会立刻写库，查看态不该有入口', async () => {
    renderPage()
    expect(await screen.findByRole('img', { name: '六号车厢' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /重新生成/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /上传/ })).toBeNull()
  })

  it('车卡建议的 AI 重写也只在编辑态出现（它会直接覆盖已有内容）', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('img', { name: '六号车厢' })
    expect(screen.queryByRole('button', { name: /AI 重写|AI 生成车卡建议/ })).toBeNull()

    await user.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByRole('button', { name: /AI 重写/ })).toBeInTheDocument()
  })

  it('进编辑态后配图入口才出现', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('img', { name: '六号车厢' })
    await user.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByRole('button', { name: /重新生成/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /上传/ })).toBeInTheDocument()
  })
})

describe('视图切换不挪动标签栏', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({
      id: 'module-1', title: 'M', rule_system: 'coc', description: '',
      world_setting: {}, scenes: [], npcs: [], clues: [], triggers: [], truth: '',
    })
  })

  it('标签栏锚在标题右侧，不随右侧按钮增减而横向漂移', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes><Route path="/modules/:id" element={<ModuleDetailPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: '详情' })

    // 「编辑」按钮只在详情/沙盘视图出现；标签栏若与它同处右对齐的一组，切到关系图就会被推走。
    const bar = container.querySelector('.module-toolbar') as HTMLElement
    const tabsParent = () => screen.getByRole('button', { name: '详情' }).parentElement
    const before = tabsParent()
    expect(bar.contains(before!)).toBe(true)
    // 标签栏不能是那个 ml-auto 的容器（右对齐容器里的东西才会被右侧增减推动）
    expect(before!.className).not.toContain('ml-auto')

    await user.click(screen.getByRole('button', { name: '关系图' }))
    expect(screen.queryByRole('button', { name: '编辑' })).toBeNull()   // 右侧确实变了
    expect(tabsParent()!.className).not.toContain('ml-auto')            // 标签栏仍不受其影响
  })
})

describe('NPC 性别', () => {
  // 解析漏填时 KP 只能按名字猜，而外文译名（加布里埃尔、艾希礼）在中文里看不出性别，
  // 猜错会把这个角色整局写成另一个性别。所以它必须能在这里看见、也能改。
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({
      id: 'module-1', title: '鬼屋', rule_system: 'coc', description: '',
      world_setting: {}, scenes: [], clues: [], triggers: [], truth: '',
      npcs: [
        { id: 'npc-1', name: '加布里埃尔·马卡里奥', gender: 'female', description: '维托里奥的妻子' },
        { id: 'npc-2', name: '金·戴伯伦', description: '高等法院的年轻办公室职员' },
      ],
    })
    mockPut.mockResolvedValue({
      id: 'module-1', title: '鬼屋', rule_system: 'coc', description: '',
      world_setting: {}, scenes: [], npcs: [], clues: [], triggers: [], truth: '',
    })
  })

  function open() {
    return render(
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:id" element={<ModuleDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('查看态用中文显示，没填的显示占位而不是空白', async () => {
    open()
    expect(await screen.findByText('维托里奥的妻子')).toBeInTheDocument()
    const labels = screen.getAllByText('性别')
    expect(labels).toHaveLength(2)
    // 第一位有性别、第二位没有——「没填」本身是要让人看见的状态
    expect(labels[0].parentElement).toHaveTextContent('女')
    expect(labels[1].parentElement).toHaveTextContent('—')
  })

  it('编辑态给出选择框，留空是合法选项', async () => {
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: '编辑' }))

    const pickers = screen.getAllByRole('combobox', { name: '性别' })
    expect(pickers).toHaveLength(2)
    expect(pickers[0]).toHaveTextContent('女')
    // 非人怪物、群体条目、原文没交代的都该留空，硬填反而更糟
    expect(pickers[1]).toHaveTextContent('未指定')
  })

  it('保存时不能把性别丢掉', async () => {
    const user = userEvent.setup()
    open()
    await user.click(await screen.findByRole('button', { name: '编辑' }))
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockPut).toHaveBeenCalledOnce())
    const payload = mockPut.mock.calls[0][1] as { npcs: { name?: string; gender?: string }[] }
    expect(payload.npcs[0].gender).toBe('female')
  })
})
