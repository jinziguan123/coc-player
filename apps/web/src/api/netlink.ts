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

/** 门口等着的一位。`claimed_label` 是对方**自称**的名字，谁都能这么叫自己。 */
export interface PendingPeer {
  id: string
  claimed_label: string
}

export interface NetlinkStatus {
  hosting: boolean
  endpoint_id: string | null
  invite: string | null
  connected_to: string | null
  local_port: number | null
  pending: PendingPeer[]
  approved: ApprovedPeer[]
  /** 上次退出时直连是开着的——据此自动恢复，见 SettingsPage 的 NetlinkPanel。 */
  wanted: boolean
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
  event?: {
    listen?: <T>(
      event: string,
      handler: (message: { payload: T }) => void,
    ) => Promise<() => void>
  }
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

/**
 * 客人侧：用邀请码连上房主。
 *
 * `label` 是自报给房主看的名字，让他知道敲门的是谁；可空。首次加入需房主手动
 * 同意，**这个 Promise 会一直挂着直到对方表态或超时（约两分钟）**，调用方要在
 * 此期间显示等待提示。被拒绝会以 reject 返回明确原因。
 */
export function netlinkConnect(inviteCode: string, label?: string): Promise<GuestLink> {
  return invoke<GuestLink>('netlink_connect', { inviteCode, label: label || null })
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

/** 门口有人等着时房主该看到的称呼：有自称就用它，否则退回公钥短名。 */
export function peerDisplayName(peer: PendingPeer): string {
  return peer.claimed_label || shortPeerId(peer.id)
}

// --- 事件 ---------------------------------------------------------------
//
// 房主多半不在设置页，靠轮询他根本不知道有人在敲门，所以 Rust 侧会主动推事件。

/** 有陌生人在门口等着。 */
export const EVENT_PENDING = 'netlink://pending'
/** 门口那位已被处理（同意/拒绝/超时），据此收掉提示。 */
export const EVENT_SETTLED = 'netlink://settled'
/** 与房主的连接断了（对方退出应用、关掉直连或网络中断）。 */
export const EVENT_DISCONNECTED = 'netlink://disconnected'

export interface PendingEvent {
  peer_id: string
  claimed_label: string
}

/**
 * 订阅一个 netlink 事件，返回取消订阅的函数。
 *
 * 非桌面环境下静默返回空操作——不该让浏览器里打开的页面因为没有 Tauri 就报错。
 */
export async function listenNetlink<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  const listen = tauri()?.event?.listen
  if (!listen) return () => {}
  try {
    return await listen<T>(event, (message) => handler(message.payload))
  } catch {
    return () => {}
  }
}
