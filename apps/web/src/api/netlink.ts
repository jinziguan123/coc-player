/**
 * 内置直连（netlink）：与 Tauri 外壳里的 iroh 隧道通信。
 *
 * 这是前端唯一一处**不走后端 HTTP 而走 Tauri IPC** 的地方——隧道跑在 Rust 进程里，
 * 后端（Python）根本不知道它的存在。窗口从 loader 跳转到 `http://127.0.0.1:<端口>`
 * 之后属于 remote origin，默认拿不到 IPC，靠 `src-tauri/capabilities/netlink.json`
 * 显式放行。
 *
 * 浏览器里直接开发（`pnpm dev`）时没有 Tauri 外壳，这里的调用都会抛
 * `NetlinkUnavailableError`，界面据此显示「仅桌面版可用」而不是报一个看不懂的错。
 */

export interface ApprovedPeer {
  id: string
  label: string
}

export interface NetlinkStatus {
  hosting: boolean
  endpoint_id: string | null
  invite: string | null
  connected_to: string | null
  local_port: number | null
  pending: string[]
  approved: ApprovedPeer[]
}

export interface GuestLink {
  local_port: number
  room_code: string | null
}

export class NetlinkUnavailableError extends Error {
  constructor() {
    super('内置直连只在桌面版可用')
    this.name = 'NetlinkUnavailableError'
  }
}

type TauriGlobal = {
  core?: { invoke?: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> }
}

function tauri(): TauriGlobal | undefined {
  return (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__
}

/** 当前环境能否使用内置直连。用于决定是显示面板还是显示「仅桌面版可用」。 */
export function netlinkAvailable(): boolean {
  return typeof tauri()?.core?.invoke === 'function'
}

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const fn = tauri()?.core?.invoke
  if (!fn) throw new NetlinkUnavailableError()
  return fn<T>(cmd, args)
}

/** 开启内置直连，返回本机 EndpointId。需要后端端口——隧道要反代到它。 */
export function netlinkStart(backendPort: number): Promise<string> {
  return invoke<string>('netlink_start', { backendPort })
}

export function netlinkStop(): Promise<void> {
  return invoke<void>('netlink_stop')
}

export function netlinkStatus(): Promise<NetlinkStatus> {
  return invoke<NetlinkStatus>('netlink_status')
}

/** 生成邀请码；带上房间码，朋友就不必再单独问一次。 */
export function netlinkInvite(roomCode?: string): Promise<string> {
  return invoke<string>('netlink_invite', { roomCode: roomCode || null })
}

/** 客人侧：用邀请码连上房主。 */
export function netlinkConnect(inviteCode: string): Promise<GuestLink> {
  return invoke<GuestLink>('netlink_connect', { inviteCode })
}

export function netlinkDisconnect(): Promise<void> {
  return invoke<void>('netlink_disconnect')
}

export function netlinkApprove(peerId: string, label?: string): Promise<void> {
  return invoke<void>('netlink_approve', { peerId, label: label || null })
}

export function netlinkReject(peerId: string): Promise<void> {
  return invoke<void>('netlink_reject', { peerId })
}

export function netlinkRevoke(peerId: string): Promise<void> {
  return invoke<void>('netlink_revoke', { peerId })
}

/** 公钥太长，界面上显示头尾即可（与 Rust 侧 `short_id` 同一规则）。 */
export function shortPeerId(id: string): string {
  return id.length <= 12 ? id : `${id.slice(0, 6)}…${id.slice(-4)}`
}
