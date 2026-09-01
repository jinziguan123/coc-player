// 重拍介绍页（site/）用的应用截图。
//
//     pnpm site:shots            # 需要 dev server 已在跑（pnpm dev）
//     pnpm site:shots --base=http://127.0.0.1:5173
//
// 为什么要有这个脚本：site/assets/shots 里那些图（gothic / parchment 各一套）每次界面
// 一动就过时，而介绍页上摆着过时截图，比文案有 AI 味更容易被人一眼看穿。手动截十几张要
// 切两次主题、跑六个页面、再逐张转 webp，做一次就不想做第二次——所以固化成脚本。
//
// 主题只从 localStorage 的 trpg_theme 读（见 apps/web/index.html 的首帧内联脚本），
// 没有 URL 开关，所以无头浏览器必须在页面加载前把它写进去。Playwright 的 addInitScript
// 正是干这个的。
//
// 依赖用 playwright-core 而不是 playwright：后者的 postinstall 会拉三套浏览器（几百 MB），
// 而这里用 channel: 'chrome' 驱动系统已装的 Chrome，那些一个都用不上，CI 装依赖不必替它买单。
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT = path.join(ROOT, 'site/assets/shots')

const argBase = process.argv.find((a) => a.startsWith('--base='))
const BASE = argBase ? argBase.slice('--base='.length) : 'http://127.0.0.1:5177'

/**
 * 游戏页要 token 才进得去：会话按 `X-Player-Token` 授权（server/app/api/deps.py），
 * 没有它 `/api/sessions` 只会返回空。前端把这串 UUID 存在 localStorage 的
 * `trpg_player_token` 下，值就落在房主席位的 `owner_token` 列里——从本机库里取出来
 * 注入，等价于「你自己在自己机器上打开了这一局」，不伪造任何东西。
 *
 * 不写死某个会话 id：那是这台机器上的存档，随时会被删。挑最近动过的一局，谁跑都能
 * 截到自己的桌。
 */
function hostSeat() {
  const sql = `SELECT s.id || '|' || p.owner_token
    FROM game_sessions s JOIN session_participants p ON p.session_id = s.id
    WHERE p.role = 'human' AND p.owner_token IS NOT NULL AND s.status = 'active'
    ORDER BY s.updated_at DESC LIMIT 1;`
  try {
    const out = execFileSync('sqlite3', [path.join(ROOT, 'server/trpg.db'), sql], {
      encoding: 'utf8',
    }).trim()
    if (!out) return null
    const [sessionId, token] = out.split('|')
    return { sessionId, token }
  } catch {
    return null                       // 没装 sqlite3 或库还没建，跳过游戏页
  }
}

/** 真人 KP 那一局的 KP 席。介绍页要展示「AI 退到副手位」是什么样子，而 KP 席的
 *  role 是 'kp' 不是 'human'，跟上面那条挑不到一块儿去。 */
function kpSeat() {
  const sql = `SELECT s.id || '|' || p.owner_token
    FROM game_sessions s JOIN session_participants p ON p.session_id = s.id
    WHERE p.role = 'kp' AND p.owner_token IS NOT NULL
      AND s.kp_mode = 'human' AND s.status = 'active'
    ORDER BY s.updated_at DESC LIMIT 1;`
  try {
    const out = execFileSync('sqlite3', [path.join(ROOT, 'server/trpg.db'), sql], {
      encoding: 'utf8',
    }).trim()
    if (!out) return null
    const [sessionId, token] = out.split('|')
    return { sessionId, token }
  } catch {
    return null
  }
}

const seat = hostSeat()
const kp = kpSeat()

/**
 * 介绍页里按 data-shot 引用的每一张。宽高与页面上写的 width/height 一一对应，
 * 改这里要同步改 index.html。
 *
 * **一律 16:10**，跟真实窗口一个比例。曾经试过「按各页内容量单独定高」，想把留白裁掉，
 * 结果每张图比例都不一样（3.8:1、2.9:1、2.1:1），一张都不像应用窗口——留白多是内容
 * 本身的事，不该靠压扁窗口去掩盖。
 *
 * 有几张要先点进去才到得了：介绍页的 alt 写明了它们是「模组沙盘视图」「创建角色向导」，
 * 不是模组列表和角色名录。截错页面比截图过时更糟，所以这里带上到达路径。
 */
const PAGES = [
  { shot: 'home', path: '/', w: 2000, h: 1250 },

  // 游戏页是这东西跑起来的样子，介绍页最该有的一张。
  seat && {
    shot: 'game', path: `/game/${seat.sessionId}`, w: 2000, h: 1250,
    token: seat.token,
    // 游戏页的 SSE 长连接一直开着，networkidle 永远等不到——只等 DOM，再靠 settle 兜住
    // 历史消息渲染、角色卡雷达图这些异步的部分。
    waitUntil: 'domcontentloaded',
    settle: 4000,
    // 出图 2000×1250，但按 1333×833 的逻辑视口排版再 1.5× 放大。介绍页把图缩到千把
    // 像素宽显示，1× 的 2000 宽图缩一半，字就只有真实大小的一半。倍率不能再往上抬：
    // 2× 意味着 1000px 的逻辑宽度，那时侧边栏和角色卡把消息区挤成一条，叙述只剩几行。
    dpr: 1.5,
    // 侧边栏收起。展开时它占掉 1/5 的宽，而截图要展示的是对局本身，不是导航。
    collapseSidebar: true,
  },

  // 真人 KP 视角：同一个游戏页，右边多一条工作台，AI 退到副手位。
  // 挑最近动过的那局——它多半就是你正在带的，消息流也才有内容可看。
  kp && {
    // 只截工作台本身，不截整页：这张图要说的是「KP 的操作台长什么样」，而消息流有没有
    // 内容全看这局跑到哪儿了——刚开的局截出来就是大半张空背景。
    shot: 'kp-console', path: `/game/${kp.sessionId}`, w: 1400, h: 900,
    token: kp.token,
    waitUntil: 'domcontentloaded',
    settle: 4000,
    collapseSidebar: true,
    element: '.kp-console-pane',
    // 只要上半截到「发布叙事」为止。整条八百多像素高，配在文字旁边会瘦得像根柱子。
    clipHeight: 400,
    dpr: 1.5,
  },

  {
    shot: 'sandbox', w: 1440, h: 900,
    // 优先挑 alt 里点名的那个本子，找不到就用第一个
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
    // 而且本机那份配置列表里是真实的接口地址（含第三方中转站），介绍页是要公开发布的，
    // 不该把它截进去。拦掉这个接口拿到的就是新装那天的样子，既贴题又不外泄。
    mock: { '**/settings/ai/profiles': [], '**/settings/ai/image-profiles': [] },
  },
].filter(Boolean)

const THEMES = ['gothic', 'parchment']

function toWebp(png, webp) {
  // 质量 85 与 scripts/generate-terrain.sh 一致；现有几张也在 100KB 上下
  execFileSync('cwebp', ['-q', '85', png, '-o', webp], { stdio: 'ignore' })
}

const browser = await chromium.launch({ channel: 'chrome' })
const tmp = mkdtempSync(path.join(tmpdir(), 'coc-shots-'))
let made = 0

try {
  for (const theme of THEMES) {
    for (const p of PAGES) {
      // 视口＝目标尺寸，1× 出图。别用「半尺寸 ＋ 2×」——那样内容是按一半的逻辑宽度
      // 排版的，宽屏才有的三列布局根本不会出现，截出来是窄窗口的样子。
      // 页面上这些图按 1440/2000 宽声明、实际显示到千把像素，本身就相当于 2× 了。
      const dpr = p.dpr ?? 1
      const ctx = await browser.newContext({
        viewport: { width: Math.round(p.w / dpr), height: Math.round(p.h / dpr) },
        deviceScaleFactor: dpr,
        colorScheme: 'dark',
      })
      // 必须在页面脚本跑之前写：首帧内联脚本就是靠它决定 data-theme 的
      await ctx.addInitScript(([t, tok, collapse]) => {
        try {
          localStorage.setItem('trpg_theme', t)
          if (tok) localStorage.setItem('trpg_player_token', tok)
          // 新手引导（features/tour/）会蒙一层挖洞遮罩，整页被压暗，截出来是张灰片。
          // 标成看过——截图要的是界面本身，不是第一次进来的教学态。
          localStorage.setItem('coc_game_tour_seen_v1', '1')
          if (collapse) localStorage.setItem('trpg_sidebar_collapsed', '1')
          for (const k of ['check-request', 'dice-result', 'luck-offer', 'combat', 'split-party']) {
            localStorage.setItem('coc_hint_seen::' + k, '1')
          }
        } catch { /* 隐私模式，忽略 */ }
      }, [theme, p.token || null, p.collapseSidebar || false])

      const page = await ctx.newPage()
      for (const [pattern, body] of Object.entries(p.mock || {})) {
        await page.route(pattern, (route) =>
          route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }))
      }
      // 动态路径要先有个页面才能发 fetch，所以先落到首页
      await page.goto(BASE + '/', { waitUntil: 'networkidle' })
      const target = typeof p.path === 'function' ? await p.path(page, BASE) : p.path
      await page.goto(BASE + target, { waitUntil: p.waitUntil ?? 'networkidle' })
      if (p.ready) await page.waitForSelector(p.ready, { timeout: 10_000 })
      // 列表进场动画有错峰延迟，等它们落定，否则截到半透明的中间态
      await page.waitForTimeout(p.settle ?? 1200)
      if (p.after) await p.after(page)

      const png = path.join(tmp, `${theme}-${p.shot}.png`)
      if (p.element) {
        const box = await page.locator(p.element).boundingBox()
        if (!box) throw new Error(`截不到 ${p.element}：页面上没有这个元素`)
        await page.screenshot({
          path: png,
          clip: { ...box, height: Math.min(box.height, p.clipHeight ?? box.height) },
        })
      } else {
        await page.screenshot({ path: png })
      }
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
console.log('注意：截到的是这台机器上的真实数据。首页的「接着玩」只有本机存档里有开着的桌时才会出现。')
if (!seat) console.log('跳过了游戏页：本机没有进行中的对局，或读不到 server/trpg.db。')
if (!kp) console.log('跳过了真人 KP 页：本机没有进行中的真人 KP 对局。')
