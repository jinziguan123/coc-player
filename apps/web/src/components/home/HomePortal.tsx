import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import { animate, m, useReducedMotion } from 'motion/react'
import { GiCrossedSwords, GiDoorway } from 'react-icons/gi'
import { DecoSunburst } from '@/components/ui/deco'
import { lastActive, sessionHref } from '@/features/home/useHomeInventory'
import type { SessionSummary } from '@/features/game-setup/types'
import { ease, spring } from '@/lib/motion'
import { relativeTime } from '@/lib/relativeTime'
import { absoluteImageUrl } from '@/lib/imageUrl'


/*
 * 拱门几何（viewBox 540×836，与 .portal 的 aspect-ratio 一致，所以 SVG 线框与 HTML 里的
 * 图片窗口能严丝合缝）：
 *   外线 x 8–532，半径 262；内线 x 20–520，半径 250；图片窗 x 30–510，半径 240，拱心 y=270。
 * 图片窗用 border-radius 的百分比画半圆顶（见 .portal-window），不用 clip-path url()——
 * 后者在部分 WebView 里对 HTML 元素支持不稳。
 */
const OUTER = 'M8 836 V270 A262 262 0 0 1 532 270 V836'
const INNER = 'M20 836 V270 A250 250 0 0 1 520 270 V836'
const STEP_L = 'M0 836 V560 H4 V548 H8'
const STEP_R = 'M540 836 V560 H536 V548 H532'

const STATUS_LABEL: Record<string, string> = { active: '进行中', setup: '大厅中', paused: '已暂停' }

const draw = (delay: number) => ({
  initial: { pathLength: 0, opacity: 0 },
  animate: { pathLength: 1, opacity: 1 },
  transition: { pathLength: { duration: 1.1, delay, ease: ease.inOut }, opacity: { duration: 0.2, delay } },
})

/**
 * 首页的焦点：一道黄铜拱门，门里是「上次那一桌」停下时的场景。
 *
 * 拱门是装饰艺术的母题，也是这一页唯一的大胆处——其余入口都退成安静的列表。
 * 点「回到桌上」时门里的画面放大铺满整屏（穿门而入），再落进对局页；
 * 系统开了「减少动态效果」就直接跳转。
 *
 * 没有在跑的桌时，门里是一片日芒纹，门下是「开第一局」的邀请：空状态是邀请，不是播报。
 */
export function HomePortal({ session }: { session: SessionSummary | null }) {
  return (
    <div className="portal-stage">
      <div className="portal">
        <svg className="portal-frame" viewBox="0 0 540 836" preserveAspectRatio="none" aria-hidden>
          <m.path d={OUTER} className="portal-line" {...draw(0)} />
          <m.path d={INNER} className="portal-line portal-line--main" {...draw(0.12)} />
          <m.path d={STEP_L} className="portal-line portal-line--dim" {...draw(0.7)} />
          <m.path d={STEP_R} className="portal-line portal-line--dim" {...draw(0.7)} />
        </svg>
        {/* 拱心石：线框画完时落下来「锁住」整道门 */}
        <m.span
          className="portal-keystone"
          aria-hidden
          initial={{ opacity: 0, y: -14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...spring.pop, delay: 0.95 }}
        />
        {session ? <SessionWindow session={session} /> : <EmptyWindow />}
      </div>
    </div>
  )
}

function SessionWindow({ session }: { session: SessionSummary }) {
  const navigate = useNavigate()
  const reduce = useReducedMotion()
  const windowRef = useRef<HTMLDivElement>(null)
  const [entering, setEntering] = useState<DOMRect | null>(null)
  const href = sessionHref(session)
  const isKp = session.character_name === 'KP'
  const lastSeen = relativeTime(lastActive(session))
  const inCombat = typeof session.combat_round === 'number'

  const enter = (e: React.MouseEvent) => {
    // 新标签页 / 新窗口打开照常走浏览器默认
    if (reduce || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0 || !windowRef.current) return
    e.preventDefault()
    setEntering(windowRef.current.getBoundingClientRect())
  }

  return (
    <>
      <m.div
        ref={windowRef}
        className={`portal-window ${session.scene_image ? '' : 'portal-window--bare'}`}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.9, delay: 0.35, ease: ease.out }}
      >
        {session.scene_image ? (
          <m.img
            src={absoluteImageUrl(session.scene_image)}
            alt=""
            className="portal-image"
            initial={{ scale: 1.12 }}
            animate={{ scale: 1 }}
            transition={{ duration: 1.6, delay: 0.35, ease: ease.out }}
          />
        ) : (
          <DecoSunburst className="portal-sunburst" />
        )}
        <div className="portal-scrim" aria-hidden />

        <m.div
          className="portal-body"
          initial="initial"
          animate="enter"
          variants={{ enter: { transition: { staggerChildren: 0.07, delayChildren: 0.6 } } }}
        >
          <m.p className="portal-eyebrow" variants={rise}>
            <span className="portal-eyebrow-gem" aria-hidden />继续上次的桌
          </m.p>
          <m.h2 className="portal-title" variants={rise}>{session.module_title || '未知模组'}</m.h2>
          <m.p className="portal-meta" variants={rise}>
            <span>{isKp ? '你是 KP' : session.character_name ? `以 ${session.character_name} 入座` : '尚未选角色'}</span>
            <span className="portal-meta-sep" aria-hidden />
            <span>{STATUS_LABEL[session.status] || session.status}</span>
            {inCombat && (
              <>
                <span className="portal-meta-sep" aria-hidden />
                <span className="portal-meta-combat"><GiCrossedSwords aria-hidden /> 第 {session.combat_round} 轮战斗</span>
              </>
            )}
          </m.p>
          {session.scene_title && (
            <m.p className="portal-scene" variants={rise}>停在「{session.scene_title}」</m.p>
          )}
          <m.div variants={rise}>
            <Link to={href} onClick={enter} className="portal-cta no-underline">
              <GiDoorway aria-hidden /> {session.status === 'setup' ? '回到大厅' : '回到桌上'}
            </Link>
          </m.div>
          <m.p className="portal-foot" variants={rise}>
            {[lastSeen && `上次 ${lastSeen}`, session.room_code && `房间码 ${session.room_code}`]
              .filter(Boolean)
              .join(' · ')}
          </m.p>
        </m.div>
      </m.div>

      {entering && createPortal(
        <PortalEnter rect={entering} image={session.scene_image} onDone={() => navigate(href)} />,
        document.body,
      )}
    </>
  )
}

const rise = {
  initial: { opacity: 0, y: 12 },
  enter: { opacity: 1, y: 0, transition: spring.soft },
}

/**
 * 穿门而入：从拱门窗口的位置与形状出发，用 clip-path 展开到整屏，同时压暗成对局页的底色。
 * 只动 clip-path / opacity，不触发布局。
 *
 * 跳转挂在 animate() 的 promise 上，另有一道保险定时器：动画被系统打断、标签页切到后台
 * （rAF 暂停）时也不能把人困在一张全屏遮罩后面。
 */
function PortalEnter({ rect, image, onDone }: { rect: DOMRect; image?: string | null; onDone: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  // 起止两端的值个数与单位必须一一对应，否则无法插值，会直接跳到终点
  const r = rect.width / 2
  const from = `inset(${rect.top}px ${window.innerWidth - rect.right}px ${window.innerHeight - rect.bottom}px ${rect.left}px round ${r}px ${r}px 0px 0px)`

  useEffect(() => {
    const el = ref.current
    if (!el) return
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      onDone()
    }
    const controls = animate(
      el,
      { clipPath: 'inset(0px 0px 0px 0px round 0px 0px 0px 0px)' },
      { duration: 0.62, ease: ease.inOut },
    )
    void controls.then(finish)
    const guard = window.setTimeout(finish, 1200)
    return () => {
      controls.stop()
      window.clearTimeout(guard)
    }
    // 只在挂载时跑一次：这是一段一次性的转场
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      ref={ref}
      className="portal-enter"
      // 首帧就是拱门窗口的形状，不会先闪一帧全屏
      style={{ clipPath: from, ...(image ? { backgroundImage: `url(${absoluteImageUrl(image)})` } : {}) }}
    >
      <m.div
        className="portal-enter-dim"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.25, ease: ease.out }}
      />
    </div>
  )
}

function EmptyWindow() {
  return (
    <m.div
      className="portal-window portal-window--bare"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.9, delay: 0.35, ease: ease.out }}
    >
      <DecoSunburst className="portal-sunburst" />
      <div className="portal-scrim" aria-hidden />
      <div className="portal-body">
        <p className="portal-eyebrow"><span className="portal-eyebrow-gem" aria-hidden />还没有在跑的桌</p>
        <h2 className="portal-title portal-title--sm">门后还是空的</h2>
        <p className="portal-scene">选一个模组，带调查员走进第一个故事。</p>
        <Link to="/game" className="portal-cta no-underline">
          <GiDoorway aria-hidden /> 开一局
        </Link>
      </div>
    </m.div>
  )
}
