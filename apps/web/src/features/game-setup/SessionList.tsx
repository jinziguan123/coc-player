import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import type { GameSetupState } from './useGameSetup'

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
  const { activeSessions, openSession, deleteSession } = setup

  return (
    <section>
      <h3 className="section-head">我的房间</h3>
      {activeSessions.length === 0 && (
        <p className="mb-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
          暂无进行中的房间。点右上角「新增游戏」开新局或加入房间。
        </p>
      )}
      <div className="grid gap-2.5 lg:grid-cols-2">
        {activeSessions.map((session) => (
          <div
            key={session.id}
            onClick={() => openSession(session)}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === 'Enter') openSession(session)
            }}
            className={`card entity-card w-full cursor-pointer text-left ${session.status === 'active' ? 'active-rail' : ''}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div
                  className="truncate font-semibold"
                  style={{ color: 'var(--color-text-accent)', fontSize: 'var(--text-base)' }}
                >
                  {session.module_title || '未知模组'}
                </div>
                <div
                  className="mt-0.5 truncate"
                  style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--text-xs)' }}
                >
                  {session.character_name || '未知角色'} · {formatTime(session.created_at)}
                </div>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                <span className={statusChipClass(session.status)}>{statusBadge(session.status)}</span>
                <ConfirmDialog
                  title="删除游戏"
                  description="确定要删除该游戏存档吗？聊天记录将一并删除，此操作不可恢复。"
                  confirmLabel="删除"
                  onConfirm={() => deleteSession(session.id)}
                >
                  {(open) => (
                    <span className="entity-card-actions inline-flex">
                      <button
                        onClick={(event) => {
                          event.stopPropagation()
                          open()
                        }}
                        className="chip chip--danger hover:!bg-[var(--color-danger-deep)] hover:!text-[var(--color-on-danger)] transition-colors"
                      >
                        删除
                      </button>
                    </span>
                  )}
                </ConfirmDialog>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
