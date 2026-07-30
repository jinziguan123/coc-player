import type { GameSetupState } from './useGameSetup'

export function JoinRoomPanel({ setup }: { setup: GameSetupState }) {
  const {
    connectedHost,
    disconnectHost,
    hostAddr,
    setHostAddr,
    joinCode,
    setJoinCode,
    joinRoom,
    guestLabel,
    setGuestLabel,
    joinWaiting,
  } = setup

  // 邀请码走内置直连，房间码可由它带来，界面提示与按钮禁用条件都要跟着变。
  const isInvite = hostAddr.trim().toLowerCase().startsWith('trpg:')

  return (
    <div className="card mb-6">
      <h3 className="card-title">加入房间</h3>
      {connectedHost && (
        <div
          className="mb-2 flex items-center gap-2 rounded px-2 py-1 text-xs"
          style={{
            background: 'var(--color-bg-tertiary)',
            color: 'var(--color-text-secondary)',
          }}
        >
          <span>
            已连接到主机 <b style={{ color: 'var(--color-text-accent)' }}>{connectedHost}</b>
          </span>
          <button
            onClick={disconnectHost}
            className="btn-secondary ml-auto !px-2 !py-0.5"
          >
            断开（回本机）
          </button>
        </div>
      )}
      <input
        value={hostAddr}
        onChange={(event) => setHostAddr(event.target.value)}
        placeholder="邀请码（trpg:…）或主机地址（如 192.168.1.5）；留空 = 本机房间"
        className="input mb-2 w-full"
      />
      {/* 房主那边看到的是一串公钥，有个名字他才认得出敲门的是谁。 */}
      {isInvite && (
        <input
          value={guestLabel}
          onChange={(event) => setGuestLabel(event.target.value)}
          placeholder="你的名字（给房主看，便于他认出你）"
          className="input mb-2 w-full"
          maxLength={24}
          aria-label="你的名字"
        />
      )}
      <div className="flex gap-2">
        <input
          value={joinCode}
          onChange={(event) => setJoinCode(event.target.value.toUpperCase())}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void joinRoom()
          }}
          placeholder={isInvite ? '房间码（邀请码已带则可留空）' : '输入房间码（向房主索取）'}
          className="input flex-1"
          maxLength={8}
        />
        <button
          onClick={() => void joinRoom()}
          // 邀请码里可能已经带了房间码，此时不该因为这一栏空着就拦住。
          disabled={joinWaiting || (!joinCode.trim() && !isInvite)}
          className="btn-primary"
        >
          {joinWaiting ? '等待中…' : '加入'}
        </button>
      </div>
      {joinWaiting ? (
        <p className="mt-2 text-xs" style={{ color: 'var(--color-text-accent)' }}>
          已敲门，正在等房主同意（他同意后会自动进入，最多等两分钟）…
        </p>
      ) : (
        isInvite && (
          <p className="mt-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            将通过内置直连连接房主。首次加入需要对方点同意，之后就不用了。
          </p>
        )
      )}
    </div>
  )
}
