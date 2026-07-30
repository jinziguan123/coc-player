/** 主机地址：留空 = 本机后端（开发用 vite 代理 /api；打包客户端用本机 sidecar）；
 *  设值（如 http://192.168.1.5:8000）= 作为客人连到房主后端。 */
export function getServerUrl(): string {
  return localStorage.getItem('trpg_server_url') || ''
}

export function setServerUrl(url: string) {
  const clean = url.trim().replace(/\/+$/, '')
  if (clean) localStorage.setItem('trpg_server_url', clean)
  else localStorage.removeItem('trpg_server_url')
}

/** 当前 API 前缀：本机走同源 /api（vite 代理）；连主机时走绝对地址 <host>/api。 */
export function getApiBase(): string {
  const s = getServerUrl()
  return s ? `${s}/api` : '/api'
}

const LOCAL_TOKEN_KEY = 'trpg_player_token'
const IDENTITY_PREFIX = 'trpg_server_identity::'

/**
 * 记录某个主机地址背后的**稳定身份**，token 按它归属而不是按地址归属。
 *
 * 内置直连（netlink）连上房主后，前端打的是 `http://127.0.0.1:<临时端口>`——
 * 端口每次连接都重新分配，按地址存 token 的话，每次重连都会换一个新 token、
 * 于是每次都掉席位。传入 `netlink:<房主公钥>` 这类稳定标识即可跟着房主走。
 *
 * 没有登记过映射的地址（局域网直连）沿用地址本身，行为与此前完全一致。
 */
export function setServerIdentity(serverUrl: string, identity: string) {
  if (!serverUrl) return
  localStorage.setItem(IDENTITY_PREFIX + serverUrl, identity)
  adoptTokenFromLoopbackUrl(identity)
}

/**
 * 迁移：把此前按「回环地址」存的 token 挪到 identity 名下。
 *
 * 隧道的本地端口每次连接都变。identity 机制上线之前，token 是按那个临时地址
 * 存的；升级后第一次重连会换用 identity 键，找不到旧 token 就新发一个——后端
 * 于是把你当成**另一个玩家**，之前的席位与历史全都看不见了。
 *
 * 只在「identity 名下还没有 token」且「恰好只有一个回环地址存过 token」时迁移。
 * 有多个就不猜：连过多位房主时挑错等于顶替了别人的身份，比丢席位更糟。
 */
function adoptTokenFromLoopbackUrl(identity: string) {
  const identityKey = `${LOCAL_TOKEN_KEY}::${identity}`
  if (localStorage.getItem(identityKey)) return

  const loopbackKeys: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key?.startsWith(`${LOCAL_TOKEN_KEY}::http://127.0.0.1:`)) loopbackKeys.push(key)
  }
  if (loopbackKeys.length !== 1) return

  const inherited = localStorage.getItem(loopbackKeys[0])
  if (inherited) localStorage.setItem(identityKey, inherited)
}

function identityFor(serverUrl: string): string {
  if (!serverUrl) return serverUrl
  return localStorage.getItem(IDENTITY_PREFIX + serverUrl) || serverUrl
}

function randomToken(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/**
 * 轻量玩家身份：localStorage 持久化的随机串，作为 X-Player-Token 带上。
 *
 * **按主机隔离。** 此前只有一个全局 token，发给你连过的每一台主机——而 token 就明晃晃
 * 存在对方库里的 `session_participants.owner_token`，等于把「你在别处的身份」交给了
 * 每一位房主。现在每台远程主机各持一个互不相干的 token。
 *
 * 这不能让 token 变成身份认证：它仍是明文 bearer，同一台主机内谁拿到谁就是你。
 * 那需要 TLS 与账号体系，见 ADR-007 的未决项。这里只是把「泄露面」从「所有主机」
 * 收敛成「泄露给谁就只影响谁」。
 *
 * 归属键是主机的**稳定身份**而非地址，见 `setServerIdentity`——内置直连的本地
 * 端口每次都变，按地址存会让人每次重连都掉席位。
 */
export function getPlayerToken(serverUrl: string = getServerUrl()): string {
  if (!serverUrl) {
    let t = localStorage.getItem(LOCAL_TOKEN_KEY)
    if (!t) { t = randomToken(); localStorage.setItem(LOCAL_TOKEN_KEY, t) }
    return t
  }
  const key = `${LOCAL_TOKEN_KEY}::${identityFor(serverUrl)}`
  let t = localStorage.getItem(key)
  if (!t) {
    // 升级迁移：当前正连着的这台主机沿用旧的全局 token，避免把已入座的席位弄丢
    // （它本来就已经拿到过这个 token，沿用不新增泄露）。其余主机一律新发。
    const isCurrentHost = serverUrl === getServerUrl()
    const legacy = isCurrentHost ? localStorage.getItem(LOCAL_TOKEN_KEY) : null
    t = legacy || randomToken()
    localStorage.setItem(key, t)
  }
  return t
}

/** 从 API base 反推主机地址：`/api` = 本机，`<host>/api` = 远程房主。 */
function serverUrlForBase(base: string): string {
  return base.startsWith('/') ? '' : base.replace(/\/api$/, '')
}

function authHeaders(base: string, extra?: HeadersInit): HeadersInit {
  return { 'X-Player-Token': getPlayerToken(serverUrlForBase(base)), ...(extra || {}) }
}

async function requestAt<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: authHeaders(base, { 'Content-Type': 'application/json', ...(init?.headers || {}) }),
  })
  if (!res.ok) {
    const body = await res.text()
    let msg = body
    try {
      const json = JSON.parse(body)
      msg = json.detail || json.message || body
    } catch { /* use raw text */ }
    throw new Error(msg)
  }
  return res.json()
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestAt<T>(getApiBase(), path, init)
}

/** multipart 文件上传：走 getApiBase()（客人模式打到房主 IP）+ 带 X-Player-Token；
 *  刻意不设 Content-Type，让浏览器自动带 multipart boundary。 */
export async function uploadFile<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: authHeaders(getApiBase()),
    body: form,
  })
  if (!res.ok) {
    const body = await res.text()
    let msg = body
    try {
      const json = JSON.parse(body)
      msg = json.detail || json.message || body
    } catch { /* use raw text */ }
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T = void>(path: string, body?: unknown) =>
    request<T>(path, { method: 'DELETE', body: body ? JSON.stringify(body) : undefined }),
}

/**
 * 始终访问当前前端同源的本机后端。
 * 客人连接远程房主后，api 会切到房主地址；localApi 保留读取本机角色库的能力，
 * 用于把本机已有角色导入远程房间。
 */
export const localApi = {
  get: <T>(path: string) => requestAt<T>('/api', path),
  post: <T>(path: string, body?: unknown) =>
    requestAt<T>('/api', path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    requestAt<T>('/api', path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    requestAt<T>('/api', path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T = void>(path: string, body?: unknown) =>
    requestAt<T>('/api', path, { method: 'DELETE', body: body ? JSON.stringify(body) : undefined }),
}

async function* parseSSEStream(res: Response) {
  if (!res.body) return

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const data = JSON.parse(line.slice(6))
      yield data
    }
  }
}

export async function* streamSSE(path: string, body?: unknown) {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: authHeaders(getApiBase(), { 'Content-Type': 'application/json' }),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok || !res.body) throw new Error(`SSE error: ${res.status}`)
  yield* parseSSEStream(res)
}

export async function* connectSSE(path: string, signal?: AbortSignal) {
  const res = await fetch(`${getApiBase()}${path}`, { signal, headers: authHeaders(getApiBase()) })
  if (res.status === 204 || !res.body) return
  if (!res.ok) throw new Error(`SSE error: ${res.status}`)
  yield* parseSSEStream(res)
}
