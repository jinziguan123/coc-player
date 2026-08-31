/**
 * 内置直连面板：开关隧道、发邀请码、审批与管理接入名册。
 *
 * 从 SettingsPage 里搬出来的，不是为了「文件太长」这个理由本身——是因为联机这一 tab
 * 现在管两套准入（直连一套、局域网一套），两个面板并排放在 features/settings/ 下，
 * 比塞在页面组件里各占两百行更好找。
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, Copy, UserPlus, X } from 'lucide-react'
import { toast } from 'sonner'
import { Switch } from '@/components/ui/switch'
import {
  netlinkApprove,
  netlinkAvailable,
  netlinkInvite,
  netlinkReject,
  netlinkRevoke,
  netlinkStart,
  netlinkStatus,
  netlinkStop,
  peerDisplayName,
  shortPeerId,
  type NetlinkStatus,
} from '@/api/netlink'

/** 门口有人等着时刷得勤一些，否则房主会觉得「点了没反应」。 */
const NETLINK_POLL_MS = 2000

export function NetlinkPanel({ backendPort }: { backendPort: number | null }) {
  const available = netlinkAvailable()
  const [status, setStatus] = useState<NetlinkStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [roomCode, setRoomCode] = useState('')
  const [invite, setInvite] = useState('')
  // 房主给门口那位起的备注，按公钥暂存；批准时提交，之后即丢弃。
  const [labelDrafts, setLabelDrafts] = useState<Record<string, string>>({})

  const refresh = useCallback(async () => {
    if (!available) return
    try {
      setStatus(await netlinkStatus())
    } catch {
      // 轮询失败不打扰用户：下一轮会再试，真出事时开关操作会报错。
    }
  }, [available])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 自动恢复不在这里——它挂在 AppShell 的 useNetlinkAutoStart。放在本组件里的
  // 后果是隧道要等房主恰好翻到「设置 → 联机」才启动，客人在那之前怎么都进不来。

  // 只在开着的时候轮询——待批准请求是唯一会「自己冒出来」的状态。
  useEffect(() => {
    if (!available || !status?.hosting) return
    const timer = setInterval(() => void refresh(), NETLINK_POLL_MS)
    return () => clearInterval(timer)
  }, [available, status?.hosting, refresh])

  const hosting = status?.hosting ?? false

  const toggle = async (next: boolean) => {
    if (next && !backendPort) {
      toast.error('还不知道后端端口，稍后再试')
      return
    }
    setBusy(true)
    try {
      if (next) {
        await netlinkStart(backendPort as number)
        toast.success('内置直连已开启')
      } else {
        await netlinkStop()
        setInvite('')
        toast.success('内置直连已关闭')
      }
      await refresh()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  /** 生成即复制——多数情况下下一步就是粘给朋友。 */
  const copyInvite = async (code: string) => {
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(code)
      toast.success('邀请码已复制')
    } catch {
      toast.error('复制失败，请手动选中邀请码')
    }
  }

  const makeInvite = async () => {
    try {
      const code = await netlinkInvite(roomCode.trim().toUpperCase())
      setInvite(code)
      void copyInvite(code)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '生成邀请码失败')
    }
  }

  const decide = async (peerId: string, approve: boolean) => {
    try {
      // 房主填了备注就用他的；没填则 Rust 侧回落到对方自称、再回落到公钥短名。
      if (approve) await netlinkApprove(peerId, labelDrafts[peerId]?.trim() || undefined)
      else await netlinkReject(peerId)
      setLabelDrafts(({ [peerId]: _dropped, ...rest }) => rest)
      toast.success(approve ? '已同意加入' : '已拒绝')
      await refresh()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败')
    }
  }

  const revoke = async (peerId: string) => {
    try {
      await netlinkRevoke(peerId)
      toast.success('已移出名单；对方断线重连后将被挡住')
      await refresh()
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '操作失败')
    }
  }

  if (!available) {
    return (
      <div className="card">
        <h3 className="card-title" style={{ margin: 0 }}>
          内置直连
        </h3>
        <p className="setting-description">
          让不在同一网络的朋友直接连进来，双方都不需要安装 Tailscale 之类的工具。
          此功能只在桌面版可用，浏览器里打开的开发页面用不了。
        </p>
      </div>
    )
  }

  return (
    <div className={`card ${hosting ? 'active-rail' : ''}`}>
      <div className="setting-head">
        <h3 className="card-title" style={{ margin: 0 }}>
          内置直连
        </h3>
        <Switch
          label="内置直连"
          checked={hosting}
          disabled={busy || status === null}
          onChange={toggle}
          onText={hosting ? '已开启' : '已关闭'}
          offText={hosting ? '已开启' : '已关闭'}
        />
      </div>
      <p className="setting-description">
        让不在同一网络的朋友直接连进来，双方都不需要额外安装联网工具。打开即可用，
        不用重启应用。朋友第一次连入时你要在这里点同意。
      </p>

      {hosting && (
        <>
          <div className="netlink-block">
            <div className="netlink-block__label">
              把邀请码发给朋友（填上房间码，对方就不用再问一次）：
            </div>
            <div className="netlink-invite">
              <input
                className="input"
                placeholder="房间码"
                value={roomCode}
                onChange={(e) => setRoomCode(e.target.value)}
                maxLength={8}
                aria-label="房间码"
              />
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void makeInvite()}
              >
                生成邀请码
              </button>
            </div>
            {invite && (
              <button
                type="button"
                onClick={() => void copyInvite(invite)}
                className="copy-line netlink-code"
                title="点击复制"
                aria-label={`复制邀请码 ${invite}`}
              >
                <span>{invite}</span>
                <Copy size={11} style={{ opacity: 0.7 }} aria-hidden="true" />
              </button>
            )}
          </div>

          {(status?.pending.length ?? 0) > 0 && (
            <div className="netlink-block">
              <div className="notice" role="alert">
                <UserPlus size={12} style={{ flexShrink: 0 }} aria-hidden="true" />
                <span>有人想加入，同意后对方才能进来。认不出的标识就拒绝掉。</span>
              </div>
              {status?.pending.map((peer) => {
                const who = peerDisplayName(peer)
                return (
                  <div key={peer.id} className="netlink-peer netlink-peer--pending">
                    <div className="netlink-peer__who">
                      {/* 自称不可信，措辞必须让房主意识到这只是对方填的。 */}
                      <span className="netlink-peer__name">
                        {peer.claimed_label ? `自称「${peer.claimed_label}」` : '未填名字'}
                      </span>
                      <span className="netlink-peer__name--id">{shortPeerId(peer.id)}</span>
                    </div>
                    <input
                      className="input netlink-peer__label"
                      placeholder="备注（可选）"
                      value={labelDrafts[peer.id] ?? ''}
                      onChange={(e) =>
                        setLabelDrafts((prev) => ({ ...prev, [peer.id]: e.target.value }))
                      }
                      maxLength={24}
                      aria-label={`给 ${who} 起备注`}
                    />
                    <div className="netlink-peer__actions">
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={() => void decide(peer.id, true)}
                        title="同意加入"
                        aria-label={`同意 ${who} 加入`}
                      >
                        <Check size={13} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn--danger"
                        onClick={() => void decide(peer.id, false)}
                        title="拒绝"
                        aria-label={`拒绝 ${who}`}
                      >
                        <X size={13} />
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {(status?.approved.length ?? 0) > 0 && (
        <div className="netlink-block">
          <div className="netlink-block__label">
            已允许的朋友（下次直接进，不用再同意）：
          </div>
          {status?.approved.map((peer) => (
            <div key={peer.id} className="netlink-peer">
              <span className="netlink-peer__name">{peer.label}</span>
              <button
                type="button"
                className="icon-btn icon-btn--danger"
                onClick={() => void revoke(peer.id)}
                title="移出名单"
                aria-label={`移出 ${peer.label}`}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
