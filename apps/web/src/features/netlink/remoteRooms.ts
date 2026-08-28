/**
 * 记住「我在别人那儿玩的房间」。
 *
 * 房主一断线，客人就被切回本机（见 useKnockNotices），那些房间存在**房主的库**里，
 * 本机会话列表当然拉不到它们——于是房间从列表上消失，只能重新粘一次邀请码。
 * 而重连需要的东西（邀请码、房间码）本来就在客人手里，没道理让他再去问一遍。
 *
 * 存在本地：这是「我连过谁」的私人记录，不属于任何一方的存档。
 */

const STORAGE_KEY = 'trpg_remote_rooms'
/** 只留最近这些个，免得列表被历史房间淹没。 */
const MAX_ROOMS = 12

export interface RemoteRoom {
  /** 邀请码，重连时原样喂给 netlinkConnect。 */
  invite: string
  /** 房间码，连上之后据它找回房间。 */
  roomCode: string
  /** 房主的 EndpointId，同一房主的多个房间据此归拢，也用于 token 归属。 */
  hostId: string
  /** 模组名，列表上给人看的。进过一次才知道。 */
  title?: string
  /** 上次进入时间，用于排序。 */
  lastSeenAt: string
}

function keyOf(room: Pick<RemoteRoom, 'hostId' | 'roomCode'>): string {
  return `${room.hostId}::${room.roomCode}`
}

export function listRemoteRooms(): RemoteRoom[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // 存坏了就当没有——这是便利功能，不该因为一条脏数据让整页崩掉。
    return parsed.filter(
      (r): r is RemoteRoom =>
        !!r && typeof r === 'object'
        && typeof (r as RemoteRoom).invite === 'string'
        && typeof (r as RemoteRoom).roomCode === 'string'
        && typeof (r as RemoteRoom).hostId === 'string',
    )
  } catch {
    return []
  }
}

/** 记住（或更新）一个远程房间。同一房主的同一房间只留一条。 */
export function rememberRemoteRoom(room: Omit<RemoteRoom, 'lastSeenAt'>): void {
  if (!room.invite || !room.roomCode || !room.hostId) return
  const entry: RemoteRoom = { ...room, lastSeenAt: new Date().toISOString() }
  const rest = listRemoteRooms().filter((r) => keyOf(r) !== keyOf(entry))
  const next = [entry, ...rest].slice(0, MAX_ROOMS)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 配额满等情况：记不住只是少个快捷入口，不影响本次游玩。
  }
}

export function forgetRemoteRoom(room: Pick<RemoteRoom, 'hostId' | 'roomCode'>): void {
  const next = listRemoteRooms().filter((r) => keyOf(r) !== keyOf(room))
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 同上
  }
}

/**
 * 邀请码的前缀。`coc:` 是当前的；`trpg:` 是项目改名前发出去的，仍然认——
 * 码是发给别人的字符串，改名不该让对方手里那张当场作废。只认不发，生成一律用 `coc:`。
 * 与 Rust 侧 `src-tauri/src/netlink/invite.rs` 的 PREFIX / LEGACY_PREFIX 对齐。
 */
const INVITE_PREFIXES = ['coc:', 'trpg:']

/** 这串是邀请码（走内置直连），而不是主机地址（走局域网直连）。 */
export function isInviteCode(raw: string): boolean {
  const cleaned = raw.trim().replace(/^["'「<]+/, '').toLowerCase()
  return INVITE_PREFIXES.some((prefix) => cleaned.startsWith(prefix))
}

/** 邀请码形如 `coc:<公钥>[:<房间码>]`，取出公钥。前缀是哪个都一样按冒号分段。 */
export function hostIdFromInvite(invite: string): string {
  const cleaned = invite.trim().replace(/^["'「<]+|["'」>]+$/g, '')
  const parts = cleaned.split(':')
  return parts.length >= 2 ? parts[1] : ''
}
