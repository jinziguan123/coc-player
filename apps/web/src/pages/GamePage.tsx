import { useState } from 'react'
import { ArrowLeft, Plus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
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
  const navigate = useNavigate()
  const setup = useGameSetup()
  const [chooser, setChooser] = useState(false)
  const [mode, setMode] = useState<Mode>(null)

  const pick = (next: Mode) => { setMode(next); setChooser(false) }

  return (
    // 开局表单是线性流程，房间列表是并列卡片——容器放到 4xl，够两列房间卡又不至于把表单拉散
    <div className="mx-auto mt-8 max-w-4xl">
      <div className="page-head">
        {/* 在流程里，左上角的「返回」退回房间列表，而不是退出整个页面——
            这一层才是用户心里的上一步。 */}
        <button
          onClick={() => (mode ? setMode(null) : navigate(-1))}
          className="btn-secondary btn-sm flex items-center gap-1"
        >
          <ArrowLeft size={14} aria-hidden="true" /> 返回
        </button>
        <h2 className="page-title">{mode ? TITLE[mode] : '开始游戏'}</h2>
        {/* 流程中不再提供「新增游戏」：此刻要么把这一步做完，要么返回。 */}
        {!mode && (
          <div className="page-head-actions">
            <button
              onClick={() => setChooser(true)}
              className="btn-primary btn-sm flex items-center gap-1"
            >
              <Plus size={14} aria-hidden="true" /> 新增游戏
            </button>
          </div>
        )}
      </div>

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
  )
}
