// 首次进入对局时的一次性操作引导。
//
// 此前「新手团」只是个三态加载页（检查 AI → 建会话 → 跳转），教学内容为零：
// 玩家被直接扔进实时对局，不知道怎么行动、不知道「」和（）的区别、
// 不知道什么时候该投骰、也不知道角色卡在哪。这三件事恰恰全都要在第一分钟内用上。
//
// 只在「本机从未看过」时自动弹一次（localStorage 记住），随时可跳过；
// 之后可从顶栏的「操作说明」重新打开。
import { useState } from 'react'
import { GiRollingDices, GiCharacter, GiScrollUnfurled, GiSpellBook } from 'react-icons/gi'
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
          难度由 KP 视情境裁定，不是你自己定。申请会加入本回合暂存，
          和你这一轮的发言一起交给 KP。
        </p>
        <p className="coach-note">
          发言先进入「本回合暂存」，点过「推进本回合」才整批交给 KP——交出去之前都还能改或删。
          多人同桌时要所有真人都点过才会交。
        </p>
      </>
    ),
  },
  // 常驻速查：前三页是「第一次进来该知道什么」，这一页是「玩到一半忘了随时能翻回来看什么」。
  // 从顶栏「操作说明」打开时直接落在这一页——回头查一个规则不该先点三次「下一步」。
  {
    title: '速查',
    Icon: GiSpellBook,
    body: (
      <>
        <dl className="coach-ref">
          <dt>怎么读骰子</dt>
          <dd>
            读数是<strong>掷出点数 / 目标值</strong>。<strong>点数越低越好</strong>，
            不超过目标值即成功；远低于目标值可达成困难/极难成功。
          </dd>
          <dt>暗投是什么</dt>
          <dd>
            部分检定（心理学、侦查等）由系统<strong>暗投</strong>，点数只有 KP 看得到——
            因为「知道自己失败了」本身就是情报。你只会读到一段可能真也可能假的描述。
          </dd>
          <dt>主动申请检定</dt>
          <dd>
            角色卡 → <strong>技能</strong>页点任意一条。可以补一句想查什么（如「他刚才那句话」）。
            申请会<strong>加入本回合暂存</strong>，和你这一轮的发言一起交给 KP——
            所以可以先点检定、再补一句台词，一起发。<strong>难度由 KP 裁定</strong>，之后再由你投骰。
          </dd>
          <dt>三种输入</dt>
          <dd>
            裸文字 = 行动；<code className="coach-code">「……」</code> = 说出口的台词；
            <code className="coach-code">（……）</code> = 场外发言，不进剧情。
          </dd>
          <dt>身上有什么</dt>
          <dd>
            角色卡 → <strong>道具</strong>页是权威清单。<strong>不在清单上的东西就是没有</strong>，
            写「我掏出某某」也变不出来。
          </dd>
          <dt>卡住了 / 生成很久没反应</dt>
          <dd>
            等待处会显示已等秒数；真卡住可点<strong>「打断并重新生成」</strong>。
            剧情不知道往哪走时，多和 NPC 说话、或申请一次<strong>灵感</strong>检定。
          </dd>
        </dl>
      </>
    ),
  },
]

export function OnboardingCoach(
  { onClose, startAtReference = false }: { onClose: () => void; startAtReference?: boolean },
) {
  // 从顶栏「操作说明」进来的是已经在玩的人，他要查的是速查页；只有首次进对局才从第一页走。
  const [step, setStep] = useState(startAtReference ? STEPS.length - 1 : 0)
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
            {last ? '操作速查' : `新手引导 ${step + 1} / ${STEPS.length}`}
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
        {!last && (
          <button onClick={finish} className="btn-secondary ml-auto !px-3 !py-1 text-sm">
            跳过
          </button>
        )}
        {step > 0 && (
          <button
            onClick={() => setStep((s) => s - 1)}
            className={`btn-secondary !px-3 !py-1 text-sm${last ? ' ml-auto' : ''}`}
          >
            上一步
          </button>
        )}
        <button
          onClick={() => (last ? finish() : setStep((s) => s + 1))}
          className="btn-primary !px-3 !py-1 text-sm"
        >
          {last ? (startAtReference ? '知道了' : '开始游戏') : '下一步'}
        </button>
      </div>
    </Modal>
  )
}
