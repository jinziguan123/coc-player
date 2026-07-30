import { useEffect } from 'react'
import { toast } from 'sonner'

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

    // 客人侧：房主退出应用或关掉直连时，隧道就死了。不提示的话页面只会莫名
    // 卡住——每个请求都在一条死连接上超时，而用户不知道发生了什么。
    void listenNetlink<string>(EVENT_DISCONNECTED, () => {
      toast.error('与房主的连接已断开', {
        description: '对方可能退出了应用或关闭了内置直连。重新用邀请码加入即可。',
        duration: 10000,
      })
    }).then(register)

    return () => {
      disposed = true
      unlisteners.forEach((off) => off())
      toasts.forEach((id) => toast.dismiss(id))
    }
  }, [])
}
