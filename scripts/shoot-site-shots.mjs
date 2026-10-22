// 重拍介绍页（site/）用的应用截图。
//
//     server/.venv/bin/python scripts/stage_site_demo.py     # ① 建干净演示库 tmp/site-demo/
//     （起沙箱后端，见 site/README.md「重新生成截图」）       # ② 后端同源托管前端产物
//     pnpm site:shots                                         # ③ 两套主题各拍一遍
//     pnpm site:shots --only=game,game-vn                     # 只重拍其中几张
//     pnpm site:shots --base=http://127.0.0.1:8011 --demo=tmp/site-demo
//
// 为什么要有这个脚本：site/assets/shots 里那些图（gothic / parchment 各一套）每次界面
// 一动就过时，而介绍页上摆着过时截图，比文案有 AI 味更容易被人一眼看穿。手动截十几张要
// 切两次主题、跑十来个页面、再逐张转 webp，做一次就不想做第二次——所以固化成脚本。
//
// **只对着演示库拍。** 介绍页要上公网，而日常开发库 server/trpg.db 里有商业模组与规则书
// 原文。会话 id 与各席 token 都从 stage_site_demo.py 写下的 <演示库>/demo.json 读，
// 不再去开发库里挑「最近动过的那局」。
//
// 主题只从 localStorage 的 trpg_theme 读（见 apps/web/index.html 的首帧内联脚本），
// 没有 URL 开关，所以无头浏览器必须在页面加载前把它写进去。Playwright 的 addInitScript
// 正是干这个的。
//
// 依赖用 playwright-core 而不是 playwright：后者的 postinstall 会拉三套浏览器（几百 MB），
// 而这里用 channel: 'chrome' 驱动系统已装的 Chrome，那些一个都用不上，CI 装依赖不必替它买单。
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT = path.join(ROOT, 'site/assets/shots')

function arg(name, fallback) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`))
  return hit ? hit.slice(name.length + 3) : fallback
}
const BASE = arg('base', process.env.SITE_SHOTS_BASE || 'http://127.0.0.1:8011')
const DEMO_DIR = path.resolve(ROOT, arg('demo', process.env.SITE_DEMO_DIR || 'tmp/site-demo'))
const ONLY = arg('only', '').split(',').filter(Boolean)

const demoFile = path.join(DEMO_DIR, 'demo.json')
if (!existsSync(demoFile)) {
  console.error(`找不到 ${demoFile}。先跑 server/.venv/bin/python scripts/stage_site_demo.py 建演示库。`)
  process.exit(1)
}
const demo = JSON.parse(readFileSync(demoFile, 'utf8'))
const { tokens, sessions } = demo

// 后端必须开在演示库上：问一下 /api/modules，库里只该有那一份原创模组。开错库（比如顺手
// 起了开发库）截出来的就是商业模组，宁可直接停下。
async function assertDemoBackend() {
  const list = await fetch(`${BASE}/api/modules`).then((r) => r.json())
  const titles = list.map((m) => m.title)
  if (titles.length !== 1 || titles[0] !== '雾港失灯事件') {
    throw new Error(`${BASE} 后面不是演示库（模组：${titles.join('、') || '无'}）。按 site/README.md 用 DB_PATH 指向演示库再起后端。`)
  }
}

// 游戏页的 SSE 长连接一直开着，networkidle 永远等不到——只等 DOM，再靠 settle 兜住
// 历史消息渲染、角色卡雷达图这些异步的部分。
//
// 出图 2000×1250，但按 1333×833 的逻辑视口排版再 1.5× 放大。介绍页把图缩到千把像素宽
// 显示，1× 的 2000 宽图缩一半，字就只有真实大小的一半。倍率不能再往上抬：2× 意味着
// 1000px 的逻辑宽度，那时侧边栏和角色卡把消息区挤成一条，叙述只剩几行。
const GAME = { w: 2000, h: 1250, dpr: 1.5, waitUntil: 'domcontentloaded', settle: 4500, collapseSidebar: true }

// 演示库里没配模型，AI KP 局会挂一条「尚未配置可用的 AI 模型」横幅——真实用户跑到这一步
// 早就配好了，那条提示不属于「对局长什么样」。只拦这一个状态接口，模型列表一概不碰。
const AI_READY = { '**/api/settings/ai/status': { configured: true } }

/**
 * 介绍页里按 data-shot 引用的每一张。**一律 16:10**，跟真实窗口一个比例。曾经试过「按各页
 * 内容量单独定高」，想把留白裁掉，结果每张图比例都不一样，一张都不像应用窗口——留白多是
 * 内容本身的事，不该靠压扁窗口去掩盖。
 *
 * 有几张要先点进去才到得了：介绍页的 alt 写明了它们是「模组沙盘视图」「创建角色向导」，
 * 不是模组列表和角色名录。截错页面比截图过时更糟，所以这里带上到达路径。
 */
const PAGES = [
  // 首页卷宗。玩家 token 下有两局开着的桌，最近动过的那局（港务所）挂在「继续上次的桌」。
  { shot: 'home', path: '/', w: 2000, h: 1250, token: tokens.player },

  // 游戏页是这东西跑起来的样子，介绍页最该有的一张：聊天流、右侧角色卡展开。
  { shot: 'game', path: `/game/${sessions.game}`, ...GAME, token: tokens.player, mock: AI_READY },

  // 同一局切成对话框模式：场景图清晰铺满，对话框停在林恩那句台词上（它是最后一站，
  // 游标缺省就落在那里）。reducedMotion 让打字机整页出现，不用赌等多久。
  {
    shot: 'game-vn', path: `/game/${sessions.game}`, ...GAME, token: tokens.player, mock: AI_READY,
    display: 'dialogue', ready: '.vn-nameplate',
  },

  // 灯室里的战斗轮：方格战场、先攻轨，轮到玩家行动。
  { shot: 'combat', path: `/game/${sessions.combat}`, ...GAME, token: tokens.player, mock: AI_READY },

  // 真人 KP 的 KP 席：普通场景下的值班台。
  { shot: 'kp-desk', path: `/game/${sessions.kp_desk}`, ...GAME, token: tokens.kp },

  // 另一局真人 KP 局已切进战斗：KP 观局台。
  { shot: 'kp-combat', path: `/game/${sessions.kp_combat}`, ...GAME, token: tokens.kp },

  // 许闻舟的整张角色卡：角色名录里点开详情。
  {
    shot: 'charcard', path: '/characters', ...GAME, waitUntil: 'networkidle', settle: 1500,
    token: tokens.player, ready: '.archive-title',
    after: async (page) => {
      await page.getByText('许闻舟').first().click()
      await page.waitForTimeout(1200)
    },
  },

  {
    shot: 'sandbox', w: 1440, h: 900,
    path: async (page, base) => {
      const list = await page.evaluate((b) => fetch(b + '/api/modules').then((r) => r.json()), base)
      const m = list.find((x) => (x.title || '').includes('雾港失灯')) || list[0]
      if (!m) throw new Error('库里一个模组都没有，截不了沙盘')
      return `/modules/${m.id}`
    },
    after: async (page) => {
      await page.getByRole('button', { name: '沙盘' }).click()
      await page.waitForTimeout(900)      // 六边形网格是 canvas，等它画完
    },
  },

  { shot: 'rulebooks', path: '/rulebooks', w: 1440, h: 900, ready: '.archive-title' },

  {
    shot: 'char-wizard', path: '/characters', w: 1440, h: 900, ready: '.archive-title',
    after: async (page) => {
      await page.getByRole('button', { name: '创建角色' }).click()
      await page.waitForTimeout(600)
    },
  },

  {
    shot: 'settings', path: '/settings', w: 1440, h: 900,
    // 这张配的是介绍页「接一个模型」那一步，读者正是还没配过的人——空状态才对题。
    // 演示库本来就没有模型配置；照样拦一道，万一有人在演示库上手动配过也不会截出接口地址。
    mock: { '**/settings/ai/profiles': [], '**/settings/ai/image-profiles': [] },
  },
].filter((p) => !ONLY.length || ONLY.includes(p.shot))

const THEMES = ['gothic', 'parchment']

function toWebp(png, webp) {
  // 质量 85 与 scripts/generate-terrain.sh 一致
  execFileSync('cwebp', ['-q', '85', png, '-o', webp], { stdio: 'ignore' })
}

await assertDemoBackend()
const browser = await chromium.launch({ channel: 'chrome' })
const tmp = mkdtempSync(path.join(tmpdir(), 'coc-shots-'))
let made = 0

try {
  for (const theme of THEMES) {
    for (const p of PAGES) {
      // 视口＝目标尺寸 / dpr。别用「半尺寸 ＋ 2×」——那样内容是按一半的逻辑宽度排版的，
      // 宽屏才有的三列布局根本不会出现，截出来是窄窗口的样子。
      const dpr = p.dpr ?? 1
      const ctx = await browser.newContext({
        viewport: { width: Math.round(p.w / dpr), height: Math.round(p.h / dpr) },
        deviceScaleFactor: dpr,
        colorScheme: 'dark',
        // 进场动画、打字机一律直接落到终态：截图要的是界面停稳之后的样子
        reducedMotion: 'reduce',
      })
      // 必须在页面脚本跑之前写：首帧内联脚本就是靠它决定 data-theme 的。
      // 没指定 token 的页面也给一个固定的「路人」token，免得每次随机一个、首页内容飘忽。
      await ctx.addInitScript(([t, tok, collapse, display]) => {
        try {
          localStorage.setItem('trpg_theme', t)
          localStorage.setItem('trpg_player_token', tok)
          localStorage.setItem('trpg_game_display', display)
          // 新手引导（features/tour/）会蒙一层挖洞遮罩，整页被压暗，截出来是张灰片。
          // 标成看过——截图要的是界面本身，不是第一次进来的教学态。
          localStorage.setItem('coc_game_tour_seen_v1', '1')
          if (collapse) localStorage.setItem('trpg_sidebar_collapsed', '1')
          for (const k of ['check-request', 'dice-result', 'luck-offer', 'combat', 'split-party']) {
            localStorage.setItem('coc_hint_seen::' + k, '1')
          }
        } catch { /* 隐私模式，忽略 */ }
      }, [theme, p.token || 'site-demo-visitor', p.collapseSidebar || false, p.display || 'chat'])

      const page = await ctx.newPage()
      for (const [pattern, body] of Object.entries(p.mock || {})) {
        await page.route(pattern, (route) =>
          route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }))
      }
      // 动态路径要先有个页面才能发 fetch，所以先落到首页
      await page.goto(BASE + '/', { waitUntil: 'networkidle' })
      const target = typeof p.path === 'function' ? await p.path(page, BASE) : p.path
      await page.goto(BASE + target, { waitUntil: p.waitUntil ?? 'networkidle' })
      if (p.ready) await page.waitForSelector(p.ready, { timeout: 15_000 })
      // 列表进场动画有错峰延迟，等它们落定，否则截到半透明的中间态
      await page.waitForTimeout(p.settle ?? 1200)
      if (p.after) await p.after(page)
      // 截之前把鼠标挪开，免得停在哪个按钮上留下悬停态
      await page.mouse.move(0, 0)

      const png = path.join(tmp, `${theme}-${p.shot}.png`)
      await page.screenshot({ path: png })
      toWebp(png, path.join(OUT, `${theme}-${p.shot}.webp`))
      await ctx.close()
      made += 1
      console.log(`  ${theme}-${p.shot}.webp`)
    }
  }
} finally {
  await browser.close()
  rmSync(tmp, { recursive: true, force: true })
}

console.log(`\n共 ${made} 张，已写入 site/assets/shots/`)
