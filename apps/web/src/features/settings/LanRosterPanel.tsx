/**
 * 局域网接入名册：谁能连进这台机器，以及此刻谁还在线。
 *
 * 与旁边的直连面板是同一件事的两种接入方式，所以刻意长得一样——都是「门口有人等着」
 * 加「已经允许的名单」。复用 `.netlink-peer` 那套类名也是这个道理：它描述的是「一位
 * 对端的一行」，不是直连专有的。
 *
 * 名册永远打**本机**后端（localApi）。客人模式下 api 会指向房主的机器，而「谁能连我」
 * 是本机的事——用 api 的话，你会去管别人家的名册，而且那边根本不会让你管（后端按来源
 * 判定，只放行本机）。
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'
import { toast } from 'sonner'
import { localApi } from '@/api/client'

export interface LanPeer {
  token: string
  status: 'pending' | 'approved' | 'rejected'
  label: string
  /** 对方自报的名字。**不可信**，界面必须写成「自称」。 */
  claimed_label: string
  last_addr: string
  last_seen: string
  first_seen: string
  online: boolean
}

/** 门口有人等着时刷得勤一些，否则房主会觉得「点了没反应」。与直连面板同一节奏。 */
const POLL_MS = 2000

/** token 是个 UUID，列表里显示不下，取头尾拼一个能认的短名。 */
function shortToken(token: string): string {
  return token.length <= 12 ? token : `${token.slice(0, 6)}…${token.slice(-4)}`
}

function who(peer: LanPeer): string {
  return peer.label || peer.claimed_label || shortToken(peer.token)
}

export function LanRosterPanel({ lanEnabled }: { lanEnabled: boolean }) {
  const [peers, setPeers] = useState<LanPeer[]>([])
  // 房主给门口那位起的备注，按 token 暂存；批准时提交，之后即丢弃。
  const [labelDrafts, setLabelDrafts] = useState<Record<string, string>>({})

  const refresh = useCallback(async () => {
    try {
      setPeers(await localApi.get<LanPeer[]>('/net/peers'))
    } catch {
      // 轮询失败不打扰用户：下一轮会再试，真出事时按钮操作会报错。
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 只在开着的时候轮询——关着就没人进得来，门口不会自己冒出人。
  useEffect(() => {
    if (!lanEnabled) return
    const timer = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(timer)
  }, [lanEnabled, refresh])

  const decide = async (peer: LanPeer, approved: boolean) => {
    try {
      await localApi.post(`/net/peers/${encodeURIComponent(peer.token)}`, {
        approved,
        label: labelDrafts[peer.token]?.trim() || undefined,
      })
      setLabelDrafts(({ [peer.token]: _dropped, ...rest }) => rest)
      toast.success(approved ? '已同意加入' : '已拒绝，对方的连接已断开')
      await refresh()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败')
    }
  }

  const forget = async (peer: LanPeer) => {
    try {
      await localApi.delete(`/net/peers/${encodeURIComponent(peer.token)}`)
      toast.success('已抹掉，对方再来需要重新同意')
      await refresh()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败')
    }
  }

  const pending = peers.filter((p) => p.status === 'pending')
  const approved = peers.filter((p) => p.status === 'approved')
  const rejected = peers.filter((p) => p.status === 'rejected')

  if (peers.length === 0) {
    return (
      <div className="netlink-block">
        <div className="netlink-block__label">
          还没有别的设备连过。开着局域网加入时，同网段的设备连过来会先排在这里等你同意。
        </div>
      </div>
    )
  }

  return (
    <>
      {pending.length > 0 && (
        <div className="netlink-block">
          <div className="notice" role="alert">
            <span>
              有设备想连进来，同意后才能用。认不出的就拒绝——同一个网段里可能还有别人的设备。
            </span>
          </div>
          {pending.map((peer) => (
            <div key={peer.token} className="netlink-peer netlink-peer--pending">
              <div className="netlink-peer__who">
                {/* 自称不可信，措辞必须让房主意识到这只是对方填的。 */}
                <span className="netlink-peer__name">
                  {peer.claimed_label ? `自称「${peer.claimed_label}」` : '未填名字'}
                </span>
                <span className="netlink-peer__name--id">{peer.last_addr || shortToken(peer.token)}</span>
              </div>
              <input
                className="input netlink-peer__label"
                placeholder="备注（可选）"
                value={labelDrafts[peer.token] ?? ''}
                onChange={(e) =>
                  setLabelDrafts((prev) => ({ ...prev, [peer.token]: e.target.value }))
                }
                maxLength={24}
                aria-label={`给 ${who(peer)} 起备注`}
              />
              <div className="netlink-peer__actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => void decide(peer, true)}
                  title="同意加入"
                  aria-label={`同意 ${who(peer)} 加入`}
                >
                  <Check size={13} />
                </button>
                <button
                  type="button"
                  className="icon-btn icon-btn--danger"
                  onClick={() => void decide(peer, false)}
                  title="拒绝"
                  aria-label={`拒绝 ${who(peer)}`}
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {approved.length > 0 && (
        <div className="netlink-block">
          <div className="netlink-block__label">
            已允许的设备（下次直接进，不用再同意）：
          </div>
          {approved.map((peer) => (
            <div key={peer.token} className="netlink-peer">
              <div className="netlink-peer__who">
                <span className="netlink-peer__name">{who(peer)}</span>
                <span className="netlink-peer__name--id">{peer.last_addr}</span>
              </div>
              {peer.online && <span className="chip chip--success">在线</span>}
              <button
                type="button"
                className="icon-btn icon-btn--danger"
                onClick={() => void decide(peer, false)}
                title="移出名单并断开"
                aria-label={`移出 ${who(peer)}`}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {rejected.length > 0 && (
        <div className="netlink-block">
          <div className="netlink-block__label">
            {/* 拒过的要留着。删了的话对方下次请求又是「陌生设备」，重新排到门口打扰你。 */}
            拒绝过的（还想放进来就点勾，抹掉则当作从没见过）：
          </div>
          {rejected.map((peer) => (
            <div key={peer.token} className="netlink-peer">
              <div className="netlink-peer__who">
                <span className="netlink-peer__name">{who(peer)}</span>
                <span className="netlink-peer__name--id">{peer.last_addr}</span>
              </div>
              <div className="netlink-peer__actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => void decide(peer, true)}
                  title="重新允许"
                  aria-label={`重新允许 ${who(peer)}`}
                >
                  <Check size={13} />
                </button>
                <button
                  type="button"
                  className="icon-btn icon-btn--danger"
                  onClick={() => void forget(peer)}
                  title="从名册里抹掉"
                  aria-label={`抹掉 ${who(peer)}`}
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
