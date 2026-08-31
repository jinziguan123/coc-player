import { useState } from 'react'
import { Plus } from 'lucide-react'
import { ArchiveHead } from '@/components/layout/ArchiveHead'
import { GameEntryChooser } from '@/features/game-setup/GameEntryChooser'
import { JoinRoomPanel } from '@/features/game-setup/JoinRoomPanel'
import { NewGamePanel } from '@/features/game-setup/NewGamePanel'
import { SessionList } from '@/features/game-setup/SessionList'
import { useGameSetup } from '@/features/game-setup/useGameSetup'

/** 当前所处的步骤；null=房间列表（本页的落地态）。 */
type Mode = 'create' | 'join' | null

const TITLE: Record<'create' | 'join', string> = {
  create: '创建房间',
  join: '加入房间',
}

export function GamePage() {
  const setup = useGameSetup()
  const [chooser, setChooser] = useState(false)
  const [mode, setMode] = useState<Mode>(null)

  const pick = (next: Mode) => { setMode(next); setChooser(false) }

  return (
    // 抬头占满整幅宽度，标题在左、操作贴右；表单与房间列表另外限宽——开局表单是线性流程，
    // 拉到满屏反而难用。不居中：各页标题的左边缘要落在同一条线上（见 HomePage）。
    <div>
      {/* 在流程里，左上角的「返回」退回房间列表，而不是退出整个页面——这一层才是用户
          心里的上一步。流程中也不再提供「新增游戏」：此刻要么把这一步做完，要么返回。 */}
      <ArchiveHead
        title={mode ? TITLE[mode] : '开始游戏'}
        stats={mode ? undefined : [{ label: '开着的桌', value: setup.activeSessions.length }]}
        back={!!mode}
        onBack={() => setMode(null)}
        actions={mode ? undefined : (
          <button onClick={() => setChooser(true)} className="btn-primary btn-sm flex items-center gap-1">
            <Plus size={14} aria-hidden="true" /> 新增游戏
          </button>
        )}
      />

      <div className="max-w-4xl">
      {/* 先问「开一局，还是去别人那局」，再只展开该出现的那一半——
          从前两个表单同屏堆着，页面一打开就是一长条，而用户此刻只想回答这一个问题。 */}
      <GameEntryChooser
        open={chooser}
        onCreate={() => pick('create')}
        onJoin={() => pick('join')}
        onClose={() => setChooser(false)}
      />

      {/* 三种状态互斥：要么在建房、要么在加入、要么在看自己的房间。
          流程进行中还把「我的房间」挂在下面，等于让用户一边填表一边被另一条路岔开。 */}
      {mode === 'create' ? <NewGamePanel setup={setup} />
        : mode === 'join' ? <JoinRoomPanel setup={setup} />
          : <SessionList setup={setup} />}
      </div>
    </div>
  )
}
