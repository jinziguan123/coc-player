import { Link } from 'react-router-dom'
import { GiDiceTwentyFacesTwenty } from 'react-icons/gi'
import { sessionHref } from '@/features/home/useHomeInventory'
import type { SessionSummary } from '@/features/game-setup/types'

const STATUS_LABEL: Record<string, string> = {
  active: '进行中',
  setup: '大厅中',
  paused: '已暂停',
}

/** 进行中的用琥珀、大厅中用成功色，与「我的房间」列表同一套配色，别在两处各说各话。 */
function statusClass(status: string): string {
  if (status === 'active') return 'chip chip--accent'
  if (status === 'setup') return 'chip chip--success'
  return 'chip'
}

/** 首页最多摆这么多张，再多就该去「游戏」页看全部了——首页是入口，不是列表页。 */
const MAX = 4

/**
 * 「接着玩」：把还开着的桌摆在首页，一步回到上次那局。
 *
 * 此前要回到进行中的游戏得先点「开始游戏」、再在房间列表里找——而这恰恰是这个工作台上
 * 最高频的一件事。一桌都没开着时整块不渲染：首页不该摆一个空框告诉你这里什么都没有。
 */
export function ResumeSessions({ sessions }: { sessions: SessionSummary[] }) {
  if (sessions.length === 0) return null
  const shown = sessions.slice(0, MAX)

  return (
    <section className="resume" aria-labelledby="resume-head">
      <div className="resume-head">
        <h2 id="resume-head" className="section-head" style={{ margin: 0 }}>接着玩</h2>
        {sessions.length > shown.length && (
          <Link to="/game" className="home-intro-link no-underline">
            还有 {sessions.length - shown.length} 桌
          </Link>
        )}
      </div>

      <div className="resume-grid">
        {shown.map((session) => (
          <Link key={session.id} to={sessionHref(session)} className="resume-card no-underline">
            <span className="resume-icon" aria-hidden="true"><GiDiceTwentyFacesTwenty /></span>
            <span className="resume-body">
              {/* 模组名可能很长，容器要能截断——flex 子项少了 min-w-0 就撑破整行 */}
              <span className="resume-title">{session.module_title || '未知模组'}</span>
              <span className="resume-sub">{session.character_name || '未指定角色'}</span>
            </span>
            <span className={statusClass(session.status)}>
              {STATUS_LABEL[session.status] || session.status}
            </span>
          </Link>
        ))}
      </div>
    </section>
  )
}
