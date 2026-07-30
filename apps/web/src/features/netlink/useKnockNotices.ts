import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import { getServerUrl, setServerUrl } from '@/api/client'

import {
  EVENT_DISCONNECTED,
  EVENT_PENDING,
  EVENT_SETTLED,
  listenNetlink,
  netlinkApprove,
  netlinkReject,
  peerDisplayName,
  type PendingEvent,
} from '@/api/netlink'

/**
 * 全局「有人敲门」提示。
 *
 * 内置直连的准入要房主点同意，但房主多半正在跑团、不在设置页——不推提示的话，
 * 朋友会在门外干等两分钟直到超时，而房主全程不知道有人来过。所以挂在应用根上，
 * 任何页面都能收到。
 *
 * 提示里直接给「同意 / 拒绝」，省掉「跳到设置页再找」这一趟。改备注、事后吊销
 * 仍在「设置 → 联机」里。
 */
export function useKnockNotices() {
  const navigate = useNavigate()

  useEffect(() => {
    // 记住每位敲门者对应的 toast，房主在别处处理掉之后好收掉它
    // （例如他自己开着设置页点了同意，这条提示就该消失）。
    const toasts = new Map<string, string | number>()
    let disposed = false
    const unlisteners: Array<() => void> = []

    const register = (unlisten: () => void) => {
      // 组件已卸载时立刻退订，避免慢一步返回的订阅泄漏。
      if (disposed) unlisten()
      else unlisteners.push(unlisten)
    }

    void listenNetlink<PendingEvent>(EVENT_PENDING, (event) => {
      const who = peerDisplayName({ id: event.peer_id, claimed_label: event.claimed_label })
      // 自称不可信，措辞上要让房主意识到这只是对方填的名字。
      const detail = event.claimed_label
        ? `对方自称「${event.claimed_label}」`
        : '对方没有填名字'
      const id = toast(`${who} 请求加入`, {
        description: `${detail}。认不出就拒绝掉。`,
        duration: Infinity,
        action: {
          label: '同意',
          onClick: () => {
            void netlinkApprove(event.peer_id).then(
              () => toast.success(`已同意 ${who} 加入`),
              () => toast.error('操作失败'),
            )
          },
        },
        cancel: {
          label: '拒绝',
          onClick: () => {
            void netlinkReject(event.peer_id).then(
              () => toast.success(`已拒绝 ${who}`),
              () => toast.error('操作失败'),
            )
          },
        },
      })
      toasts.set(event.peer_id, id)
    }).then(register)

    void listenNetlink<string>(EVENT_SETTLED, (peerId) => {
      const id = toasts.get(peerId)
      if (id !== undefined) {
        toast.dismiss(id)
        toasts.delete(peerId)
      }
    }).then(register)

    // 客人侧：房主退出应用或关掉直连时，隧道就死了。
    //
    // 光提示不够——必须把主机地址切回本机。隧道一死，那个 127.0.0.1:<临时端口>
    // 就没人监听了，而前端还对着它发请求：会话列表拉不到就显示为空（看着像
    // 「存档没了」，其实数据在房主库里好好的），「加入房间」也是往死地址发。
    //
    // 房主的存档与你的席位都还在：他重开应用后邀请码不变（身份已持久化），
    // 重新粘一次就能回去。
    void listenNetlink<string>(EVENT_DISCONNECTED, () => {
      const wasRemote = !!getServerUrl()
      if (wasRemote) setServerUrl('')
      toast.error('与房主的连接已断开', {
        description: wasRemote
          ? '已切回本机。房主重开应用后，用同一个邀请码即可回到原来的房间与席位。'
          : '对方可能退出了应用或关闭了内置直连。',
        duration: 12000,
      })
      // 当前页面上的数据都来自房主，留在原地只会满屏报错；送回加入房间的入口。
      if (wasRemote) navigate('/game')
    }).then(register)

    return () => {
      disposed = true
      unlisteners.forEach((off) => off())
      toasts.forEach((id) => toast.dismiss(id))
    }
  }, [navigate])
}
