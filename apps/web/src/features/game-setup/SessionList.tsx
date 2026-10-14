import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { GiCrossedSwords, GiDiceTwentyFacesTwenty } from 'react-icons/gi'
import { TiltCard } from '@/components/ui/TiltCard'
import { lastActive } from '@/features/home/useHomeInventory'
import { relativeTime } from '@/lib/relativeTime'
import type { GameSetupState } from './useGameSetup'
import { absoluteImageUrl } from '@/lib/imageUrl'

function formatTime(timestamp?: string) {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

function statusBadge(status: string) {
  return status === 'setup' ? '大厅中' : status === 'active' ? '进行中' : '已暂停'
}

/** 进行中的局用琥珀、大厅中用成功色、暂停保持中性——列表里一眼分出「哪桌在跑」。 */
function statusChipClass(status: string) {
  if (status === 'active') return 'chip chip--accent'
  if (status === 'setup') return 'chip chip--success'
  return 'chip'
}

export function SessionList({ setup }: { setup: GameSetupState }) {
  const {
    activeSessions, openSession, deleteSession,
    remoteRooms, reconnecting, reconnectRemoteRoom, forgetRoom,
  } = setup
  // 最近交互过的桌排在前面（与首页「继续上次的桌」同一口径）
  const sorted = [...activeSessions].sort((a, b) => lastActive(b).localeCompare(lastActive(a)))

  return (
    <section>
      <h3 className="section-head">我的房间</h3>
      {activeSessions.length === 0 && remoteRooms.length === 0 && (
        <div className="empty-state">
          <span className="empty-state-icon"><GiDiceTwentyFacesTwenty /></span>
          <span className="empty-state-title">还没有开着的桌</span>
          <span className="empty-state-hint">
            点右上角「新增游戏」开一新局，或用房间码加入队友的桌。
          </span>
        </div>
      )}
      {/* 拿到整幅宽度后可以多排一列：卡里只有标题、角色和时间三行，两列时每张近 600px
          宽得发空。三列封顶——再多模组名就要截断了。 */}
      <div className="table-grid">
        {sorted.map((session, i) => (
          <TiltCard key={session.id} index={i} max={4} className="table-card-wrap">
            <div
              onClick={() => openSession(session)}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === 'Enter') openSession(session)
              }}
              className={`card entity-card table-card table-card--${session.status}`}
            >
              {session.scene_image && (
                <img src={absoluteImageUrl(session.scene_image)} alt="" className="table-card-scene" loading="lazy" />
              )}
              <span className="table-card-ribbon" aria-hidden />
              <div className="table-card-body">
                <div className="table-card-top">
                  <span className={statusChipClass(session.status)}>{statusBadge(session.status)}</span>
                  {typeof session.combat_round === 'number' && (
                    <span className="table-card-combat"><GiCrossedSwords aria-hidden /> 第 {session.combat_round} 轮</span>
                  )}
                </div>
                <div className="table-card-title">{session.module_title || '未知模组'}</div>
                <div className="table-card-meta">
                  {session.character_name === 'KP' ? '你是 KP' : session.character_name || '未选角色'}
                  {session.scene_title && <> · 停在「{session.scene_title}」</>}
                </div>
                <div className="table-card-foot">
                  {session.room_code && <span className="table-card-code">{session.room_code}</span>}
                  <span>{relativeTime(lastActive(session)) ?? formatTime(session.created_at)}</span>
                </div>
              </div>
              <ConfirmDialog
                title="删除游戏"
                description="确定要删除该游戏存档吗？聊天记录将一并删除，此操作不可恢复。"
                confirmLabel="删除"
                onConfirm={() => deleteSession(session.id)}
              >
                {(open) => (
                  <span className="entity-card-actions table-card-delete">
                    <button
                      onClick={(event) => {
                        event.stopPropagation()
                        open()
                      }}
                      className="chip chip--danger chip-btn chip-btn--danger"
                    >
                      删除
                    </button>
                  </span>
                )}
              </ConfirmDialog>
            </div>
          </TiltCard>
        ))}
      </div>

      {/* 在别人那儿玩的房间：存在**房主的库**里，本机会话列表永远拉不到。
          靠本地记录留在这儿，点一下重建隧道并直接进房——房主掉线过一次之后，
          不该逼玩家再去翻聊天记录找邀请码。 */}
      {remoteRooms.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            在朋友那儿的房间（点击连接房主并进入）
          </div>
          <div className="grid gap-2.5 lg:grid-cols-2 xl:grid-cols-3">
            {remoteRooms.map((room) => {
              const busy = reconnecting === `${room.hostId}::${room.roomCode}`
              return (
                <div
                  key={`${room.hostId}::${room.roomCode}`}
                  onClick={() => { if (!busy) void reconnectRemoteRoom(room) }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !busy) void reconnectRemoteRoom(room)
                  }}
                  className="card entity-card w-full cursor-pointer text-left"
                  style={busy ? { opacity: 0.6 } : undefined}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div
                        className="truncate font-semibold"
                        style={{ color: 'var(--color-text-accent)', fontSize: 'var(--text-base)' }}
                      >
                        {room.title || '朋友的房间'}
                      </div>
                      <div
                        className="mt-0.5 truncate"
                        style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--text-xs)' }}
                      >
                        房间码 {room.roomCode} · {formatTime(room.lastSeenAt)}
                      </div>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-1">
                      <span className="chip">{busy ? '连接中…' : '需连接'}</span>
                      <span className="entity-card-actions inline-flex">
                        <button
                          onClick={(event) => { event.stopPropagation(); forgetRoom(room) }}
                          className="chip chip--danger chip-btn chip-btn--danger"
                          title="只从这个列表里移除，不影响房主那边的房间"
                        >
                          移除
                        </button>
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
