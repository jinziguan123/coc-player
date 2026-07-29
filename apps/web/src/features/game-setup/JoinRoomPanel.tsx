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
          disabled={!joinCode.trim() && !isInvite}
          className="btn-primary"
        >
          加入
        </button>
      </div>
      {isInvite && (
        <p className="mt-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          将通过内置直连连接房主。首次加入需要对方在「设置 → 联机」里点同意。
        </p>
      )}
    </div>
  )
}
