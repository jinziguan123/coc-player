import { useState } from 'react'
import { ArrowLeft, Plus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { GameEntryChooser } from '@/features/game-setup/GameEntryChooser'
import { JoinRoomPanel } from '@/features/game-setup/JoinRoomPanel'
import { NewGamePanel } from '@/features/game-setup/NewGamePanel'
import { SessionList } from '@/features/game-setup/SessionList'
import { useGameSetup } from '@/features/game-setup/useGameSetup'

/** 入口选完之后展开哪一半；null=只列房间。 */
type Mode = 'create' | 'join' | null

export function GamePage() {
  const navigate = useNavigate()
  const setup = useGameSetup()
  const [chooser, setChooser] = useState(false)
  const [mode, setMode] = useState<Mode>(null)

  const pick = (next: Mode) => { setMode(next); setChooser(false) }

  return (
    // 开局表单是线性流程，房间列表是并列卡片——容器放到 4xl，够两列房间卡又不至于把表单拉散
    <div className="mx-auto mt-8 max-w-4xl">
      <div className="mb-6 flex items-center gap-3">
        <button
          onClick={() => navigate(-1)}
          className="btn-secondary flex items-center gap-1 !px-2 !py-1 text-sm"
        >
          <ArrowLeft size={14} aria-hidden="true" /> 返回
        </button>
        <h2 className="page-title !mb-0">开始游戏</h2>
        <button
          onClick={() => (mode ? setMode(null) : setChooser(true))}
          className="btn-primary ml-auto flex items-center gap-1 text-sm"
        >
          <Plus size={14} aria-hidden="true" /> {mode ? '收起' : '新增游戏'}
        </button>
      </div>

      {/* 先问「开一局，还是去别人那局」，再只展开该出现的那一半——
          从前两个表单同屏堆着，页面一打开就是一长条，而用户此刻只想回答这一个问题。 */}
      <GameEntryChooser
        open={chooser}
        onCreate={() => pick('create')}
        onJoin={() => pick('join')}
        onClose={() => setChooser(false)}
      />

      {mode === 'create' && <NewGamePanel setup={setup} />}
      {mode === 'join' && <JoinRoomPanel setup={setup} />}

      <SessionList setup={setup} />
    </div>
  )
}
