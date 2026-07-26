// 首次进入对局时的一次性操作引导。
//
// 此前「新手团」只是个三态加载页（检查 AI → 建会话 → 跳转），教学内容为零：
// 玩家被直接扔进实时对局，不知道怎么行动、不知道「」和（）的区别、
// 不知道什么时候该投骰、也不知道角色卡在哪。这三件事恰恰全都要在第一分钟内用上。
//
// 只在「本机从未看过」时自动弹一次（localStorage 记住），随时可跳过；
// 之后可从顶栏的「操作说明」重新打开。
import { useState } from 'react'
import { GiRollingDices, GiCharacter, GiScrollUnfurled } from 'react-icons/gi'
import { Modal } from '@/components/ui/modal'

const SEEN_KEY = 'trpg_coach_seen_v1'

export function hasSeenCoach(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === '1'
  } catch {
    return true   // 隐私模式等读不到 localStorage：宁可不弹，也不要每次进来都弹
  }
}

export function markCoachSeen() {
  try {
    localStorage.setItem(SEEN_KEY, '1')
  } catch { /* 存不下就算了，不影响使用 */ }
}

interface Step {
  title: string
  Icon: typeof GiRollingDices
  body: React.ReactNode
}

const STEPS: Step[] = [
  {
    title: '直接用自己的话写行动',
    Icon: GiScrollUnfurled,
    body: (
      <>
        <p>
          底部输入框里写你想做什么就行，不需要记指令。比如
          <code className="coach-code">我翻开桌上的航海日志</code>
          ，守秘人（KP）会据此推进剧情。
        </p>
        <ul className="coach-list">
          <li>
            <code className="coach-code">「……」</code> 或 <code className="coach-code">"……"</code>
            括住的内容 = 你的角色<strong>说出口</strong>的台词
          </li>
          <li>
            <code className="coach-code">（……）</code> 圆括号里 = <strong>场外发言</strong>，
            队友能看到，但不进入剧情
          </li>
        </ul>
      </>
    ),
  },
  {
    title: '该投骰时，聊天里会出现骰子卡',
    Icon: GiRollingDices,
    body: (
      <>
        <p>
          你不用自己决定什么时候投骰。KP 认为需要检定时，聊天流里会出现一张
          <strong>带微光呼吸的待投骰卡片</strong>，点它就投。
        </p>
        <p>
          投完会给出一张读数：<strong>掷出的点数 / 目标值 / 成败</strong>。
          点数越低越好——只要不超过目标值就算成功。
        </p>
      </>
    ),
  },
  {
    title: '角色卡随时可查，也能主动申请检定',
    Icon: GiCharacter,
    body: (
      <>
        <p>
          右侧是你的角色卡（窄屏上点顶栏的展开按钮拉出来），
          能看到 HP、理智值 SAN、全部技能与随身道具。
        </p>
        <p>
          在<strong>技能</strong>页点任意一条技能，可以主动向 KP 申请这项检定——
          难度由 KP 视情境裁定，不是你自己定。
        </p>
        <p className="coach-note">
          多人同桌时，所有真人都点过「推进本回合」，这一轮的发言才会整批交给 KP。
        </p>
      </>
    ),
  },
]

export function OnboardingCoach({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0)
  const current = STEPS[step]
  const last = step === STEPS.length - 1

  const finish = () => {
    markCoachSeen()
    onClose()
  }

  return (
    <Modal onClose={finish} widthClass="max-w-lg" padded>
      <div className="flex items-start gap-3">
        <span className="char-sigil !h-10 !w-10" aria-hidden="true">
          <current.Icon />
        </span>
        <div className="min-w-0 flex-1">
          <div
            className="mb-0.5 tracking-widest"
            style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}
          >
            新手引导 {step + 1} / {STEPS.length}
          </div>
          <h2
            className="font-semibold"
            style={{
              fontFamily: 'var(--font-title)',
              fontSize: 'var(--text-lg)',
              color: 'var(--color-text-accent)',
            }}
          >
            {current.title}
          </h2>
        </div>
      </div>

      <div className="coach-body mt-3">{current.body}</div>

      <div className="mt-5 flex items-center gap-2">
        {/* 进度点 */}
        <div className="flex items-center gap-1.5" aria-hidden="true">
          {STEPS.map((_, index) => (
            <span key={index} className={`coach-dot ${index === step ? 'coach-dot--on' : ''}`} />
          ))}
        </div>
        <button onClick={finish} className="btn-secondary ml-auto !px-3 !py-1 text-sm">
          跳过
        </button>
        {step > 0 && (
          <button onClick={() => setStep((s) => s - 1)} className="btn-secondary !px-3 !py-1 text-sm">
            上一步
          </button>
        )}
        <button
          onClick={() => (last ? finish() : setStep((s) => s + 1))}
          className="btn-primary !px-3 !py-1 text-sm"
        >
          {last ? '开始游戏' : '下一步'}
        </button>
      </div>
    </Modal>
  )
}
