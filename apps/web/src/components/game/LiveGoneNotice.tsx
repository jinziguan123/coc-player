import { useNavigate } from 'react-router-dom'
import { GiReturnArrow } from 'react-icons/gi'

import { Modal } from '@/components/ui/modal'
import type { LiveGoneError } from '@/lib/liveSession'

/**
 * 实时连接被服务端永久拒绝后的去处提示（见 lib/liveSession 的 LiveGoneError）。
 *
 * 重连循环此时已经停了；不告诉人的话，界面停在最后一刻、输入点了没反应，
 * 看起来像卡死。关掉弹窗或点按钮都回到游戏列表——留在这页已经没有意义。
 */
export function LiveGoneNotice({ error }: { error: LiveGoneError }) {
  const navigate = useNavigate()
  const leave = () => navigate('/game', { replace: true })
  const detail = error.status === 404
    ? '这个房间已经不存在了，可能已被房主删除。'
    : '你已不在这个房间：可能被房主移出席位，或者这台设备的接入已被收回。'
  return (
    <Modal onClose={leave} widthClass="max-w-sm" padded>
      <h3 className="text-base font-semibold mb-2" style={{ color: 'var(--color-text-primary)' }}>
        {error.message}
      </h3>
      <p className="text-sm mb-4" style={{ color: 'var(--color-text-secondary)' }}>{detail}</p>
      <button onClick={leave} className="btn-primary flex items-center gap-1.5">
        <GiReturnArrow size={14} /> 返回游戏列表
      </button>
    </Modal>
  )
}
