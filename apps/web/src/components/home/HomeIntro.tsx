import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown } from 'lucide-react'
import {
  GiCharacter,
  GiRobotGolem,
  GiRollingDices,
  GiHeartBottle,
  GiBrain,
  GiMeeple,
  GiWifiRouter,
  GiWorld,
} from 'react-icons/gi'

/** 跑团的三个要素：谁在讲、谁在演、谁来裁决。先把「桌上没有画面」这件事说清楚。 */
const ROLES = [
  {
    Icon: GiRobotGolem,
    title: '守秘人（KP）',
    desc: '描述场景、扮演所有 NPC、决定什么时候该掷骰。在本项目里，这个位子由 AI 来坐。',
  },
  {
    Icon: GiCharacter,
    title: '调查员（你）',
    desc: '一张角色卡：属性、技能、生命与理智。剩下的就是说出「我想做什么」。',
  },
  {
    Icon: GiRollingDices,
    title: '骰子',
    desc: '只在结果不确定时才掷。想试什么都行，骰子管的是试没试成。',
  },
] as const

/** CoC 七版成功等级。阈值与右列示例都对齐后端 resolve_skill_check 的判定顺序，别在这里自造一套。
 *  示例取技能 60（≥50），所以大失败只有 100；技能值 < 50 时 96–100 都算，写在表下脚注里。 */
const CHECK_TIERS = [
  { roll: '01', tier: '大成功', color: 'var(--color-dice-gold)', example: '01' },
  { roll: '≤ 技能 ÷ 5', tier: '极难成功', color: 'var(--color-text-primary)', example: '02–12' },
  { roll: '≤ 技能 ÷ 2', tier: '困难成功', color: 'var(--color-text-primary)', example: '13–30' },
  { roll: '≤ 技能值', tier: '普通成功', color: 'var(--color-text-primary)', example: '31–60' },
  { roll: '> 技能值', tier: '失败', color: 'var(--color-text-secondary)', example: '61–99' },
  { roll: '100', tier: '大失败', color: 'var(--color-dice-fumble)', example: '100' },
] as const

/** 生命与理智：CoC 的两条命脉，各一行，不占卡片的高度。 */
const VITALS = [
  { Icon: GiHeartBottle, color: 'var(--color-danger)', name: '生命（HP）', desc: '挨打就掉，归零濒死。在 CoC 里正面硬刚很少有好下场。' },
  { Icon: GiBrain, color: 'var(--color-text-accent)', name: '理智（SAN）', desc: '目睹超自然与惨状会掉，一次掉太狠会当场陷入临时疯狂。' },
] as const

/** 上手四步，每步挂一个能立刻点进去的入口——只讲流程不给门把手，等于还得让人自己找。 */
const STEPS = [
  { title: '接一个 AI 模型', desc: '守秘人由 AI 扮演。先去设置里填好接口，测通再走。除了模型，其它都跑在你自己机器上。', to: '/settings', linkText: '去设置' },
  { title: '挑一个本子', desc: '走新手团最快。也可以传自己的模组，AI 会把它拆成场景和线索。', to: '/modules', linkText: '模组库' },
  { title: '备一位调查员', desc: '车卡向导带你掷属性、选技能；懒得动手就让 AI 生成，手头有现成的可以用 Excel 导入。', to: '/characters', linkText: '角色名录' },
  { title: '开局', desc: '建房拿房间码叫上朋友，空着的席位可以交给 AI 队友。', to: '/game', linkText: '开始游戏' },
] as const

/** 联机的三种玩法。写清楚各自的前置条件——「不同网络」那条要双方都用桌面版，
 *  隧道跑在 Tauri 外壳的 Rust 进程里（api/netlink.ts），浏览器里根本没有它。
 *  漏掉这句，朋友照着做到一半才发现用不了。 */
const COOP = [
  {
    Icon: GiMeeple,
    title: '一个人也能开',
    desc: '空着的席位交给 AI 队友，不必等人凑齐。',
  },
  {
    Icon: GiWifiRouter,
    title: '同一个网络',
    desc: '房主去设置里打开「允许局域网加入」，这个开关默认是关的。其他人填主机地址和房间码就能进。别在公共网络上开。',
  },
  {
    Icon: GiWorld,
    title: '隔着网络',
    desc: '房主开「内置直连」，发一条邀请码，对方粘进「加入房间」就行，不用另外装联网工具。双方都得用桌面版。',
  },
] as const

/** 首页介绍：跑团是什么、规则怎么转、在本项目里怎么玩。
 *
 *  **默认收起，只对还没上过桌的人自动摊开**。首页在导航里叫「卷宗」，是你的工作台——
 *  已经车了七张卡、跑过几局的人每次进来都撞见一遍「什么是跑团」，那是噪音。可它对
 *  真正的新人又是刚需，所以不能删，只能让它按人出现（见 CaseFileHeader 的 onLoaded）。
 *
 *  摊开后三段并排而不是竖着堆——一旦要滚屏，下面两段对新人就等于不存在。 */
export function HomeIntro({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  // 库存是异步点完的，defaultOpen 会迟一步到；只认「从收起变成该展开」这一次，
  // 不能反过来把用户手动展开的又合上。
  useEffect(() => { if (defaultOpen) setOpen(true) }, [defaultOpen])

  return (
    <section className="home-intro" aria-labelledby="home-intro-heading">
      <button
        type="button"
        id="home-intro-heading"
        className="home-intro-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="home-intro-toggle-title">第一次跑团？</span>
        <span className="home-intro-toggle-hint">跑团是什么、骰子怎么转、在这儿怎么开局</span>
        <ChevronDown size={15} aria-hidden="true" className={open ? 'rotate-180' : ''}
          style={{ transition: 'transform 160ms ease', flexShrink: 0 }} />
      </button>

      {open && (
      <>
      <div className="home-intro-grid mt-4">
        <div>
          <h3 className="section-head">什么是跑团</h3>
          <p className="home-intro-lede">
            跑团（TRPG，桌上角色扮演游戏）没有画面，也没有关卡。场景靠主持人讲出来，
            你说角色要做什么，故事就顺着往下走。同一个本子，两桌人能玩出完全不同的结局。
          </p>
          <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
            {ROLES.map(({ Icon, title, desc }) => (
              <li key={title} className="home-intro-role">
                <Icon size={17} color="var(--color-text-accent)" aria-hidden="true" />
                <div>
                  <div className="home-intro-role-name">{title}</div>
                  <div className="home-intro-note">{desc}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="section-head">基本规则（以 CoC 七版为例）</h3>
          <p className="home-intro-lede">
            检定只掷一颗 d100：<strong style={{ color: 'var(--color-text-primary)' }}>骰点小于等于技能值就是成功，越低越好</strong>。
          </p>
          <div className="home-intro-panel mt-2">
            <table className="home-check-table">
              <thead>
                <tr>
                  <th scope="col">骰出</th>
                  <th scope="col">结果</th>
                  <th scope="col">技能 60 时</th>
                </tr>
              </thead>
              <tbody>
                {CHECK_TIERS.map(({ roll, tier, color, example }) => (
                  <tr key={tier}>
                    <td>{roll}</td>
                    <td style={{ color }}>{tier}</td>
                    <td>{example}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="home-intro-note mt-1.5">
            右列按「侦查 60」算。技能不到 50 时，96–100 都是大失败。处境有利或不利时多掷一颗十位骰，取好的或取差的那个，这就是奖骰和惩骰。
          </p>
          {/* 这两行不套卡片外框：中列本就是三栏里最高的一栏，边框和内边距的高度省下来给一屏 */}
          <ul className="m-0 mt-2 flex list-none flex-col gap-1 p-0">
            {VITALS.map(({ Icon, color, name, desc }) => (
              <li key={name} className="home-intro-vital">
                <Icon size={16} color={color} aria-hidden="true" />
                <div className="home-intro-note">
                  <span className="home-intro-role-name">{name}</span>
                  {'　'}{desc}
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="section-head">如何游玩本项目</h3>
          <ol className="m-0 list-none p-0">
            {STEPS.map(({ title, desc, to, linkText }, i) => (
              <li key={title} className="home-step">
                <span className="home-step-index" aria-hidden="true">{i + 1}</span>
                <div>
                  <div className="home-intro-role-name">{title}</div>
                  <div className="home-intro-note">
                    {desc}
                    <Link to={to} className="home-intro-link no-underline">{linkText}</Link>
                  </div>
                </div>
              </li>
            ))}
          </ol>
          <p className="home-intro-note mt-2">
            进桌之后不用学操作：像聊天一样描述你的行动，KP 喊检定时投骰就行。
          </p>
        </div>
      </div>

      <div className="home-coop">
        {/* 说明并进标题行而不是另起一段：首页的预算是「一屏」，一段独立脚注就要多占 55px */}
        <div className="home-coop-head">
          <h3 className="section-head" style={{ margin: 0 }}>和朋友一起玩</h3>
          <span className="home-intro-note">
            <strong style={{ color: 'var(--color-text-primary)' }}>房主的电脑就是主机</strong>
            。没有账号，也没有服务器，存档就在那台机器上。AI 也只从房主那端调用，所以只有房主需要配模型。
          </span>
        </div>
        <ul className="home-coop-grid m-0 list-none p-0">
          {COOP.map(({ Icon, title, desc }) => (
            <li key={title} className="home-coop-item">
              <Icon size={17} color="var(--color-text-accent)" aria-hidden="true" />
              <div>
                <div className="home-intro-role-name">{title}</div>
                <div className="home-intro-note">{desc}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
      </>
      )}
    </section>
  )
}
