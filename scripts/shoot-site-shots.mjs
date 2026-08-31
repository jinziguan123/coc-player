// 重拍介绍页（site/）用的应用截图。
//
//     pnpm site:shots            # 需要 dev server 已在跑（pnpm dev）
//     pnpm site:shots --base=http://127.0.0.1:5177
//
// 为什么要有这个脚本：site/assets/shots 里那十张图（gothic / parchment 各五张）每次界面
// 一动就过时，而介绍页上摆着过时截图，比文案有 AI 味更容易被人一眼看穿。手动截十张要
// 切两次主题、跑五个页面、再逐张转 webp，做一次就不想做第二次——所以固化成脚本。
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

/** 介绍页里按 data-shot 引用的五张。宽高与页面上写的 width/height 一一对应，改这里要同步改 index.html。
 *  高度按各页内容量单独给——统一高度会让内容少的页（规则书、建卡）截出大半张空背景。
 *
 *  两张要先点进去才到得了——介绍页的 alt 写明了它们是「模组沙盘视图」和「创建角色向导」，
 *  不是模组列表和角色名录。截错页面比截图过时更糟，所以这里带上到达路径。 */
const PAGES = [
  // 首页只在有开着的桌时才多出「接着玩」一块。截高度按「没有开着的桌」这个下限给，
  // 有桌时那块会把中间填上，图会更满而不是被截断。
  { shot: 'home', path: '/', w: 2000, h: 520 },
  {
    shot: 'sandbox', w: 1600, h: 760,
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
  { shot: 'rulebooks', path: '/rulebooks', w: 1600, h: 560, ready: '.archive-title' },
  {
    shot: 'char-wizard', path: '/characters', w: 1600, h: 640, ready: '.archive-title',
    after: async (page) => {
      await page.getByRole('button', { name: '创建角色' }).click()
      await page.waitForTimeout(600)
    },
  },
  {
    shot: 'settings', path: '/settings', w: 1600, h: 560,
    // 这张配的是介绍页「接一个模型」那一步，读者正是还没配过的人——空状态才对题。
    // 而且本机那份配置列表里是真实的接口地址（含第三方中转站），介绍页是要公开发布的，
    // 不该把它截进去。拦掉这个接口拿到的就是新装那天的样子，既贴题又不外泄。
    mock: { '**/settings/ai/profiles': [], '**/settings/ai/image-profiles': [] },
  },
]

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
      // 视口＝目标尺寸，1× 出图。别用「半尺寸 ＋ 2×」——那样内容是按 800px 逻辑宽度
      // 排版的，宽屏才有的三列布局根本不会出现，截出来是窄窗口的样子。
      // 页面上这些图按 1600/2000 宽声明、实际显示到千把像素，本身就相当于 2× 了。
      const ctx = await browser.newContext({
        viewport: { width: p.w, height: p.h },
        deviceScaleFactor: 1,
        colorScheme: 'dark',
      })
      // 必须在页面脚本跑之前写：首帧内联脚本就是靠它决定 data-theme 的
      await ctx.addInitScript((t) => {
        try { localStorage.setItem('trpg_theme', t) } catch { /* 隐私模式，忽略 */ }
      }, theme)

      const page = await ctx.newPage()
      for (const [pattern, body] of Object.entries(p.mock || {})) {
        await page.route(pattern, (route) =>
          route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }))
      }
      // 动态路径要先有个页面才能发 fetch，所以先落到首页
      await page.goto(BASE + '/', { waitUntil: 'networkidle' })
      const target = typeof p.path === 'function' ? await p.path(page, BASE) : p.path
      await page.goto(BASE + target, { waitUntil: 'networkidle' })
      if (p.ready) await page.waitForSelector(p.ready, { timeout: 10_000 })
      // 列表进场动画有错峰延迟，等它们落定，否则截到半透明的中间态
      await page.waitForTimeout(1200)
      if (p.after) await p.after(page)

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
console.log('注意：截到的是这台机器上的真实数据。首页的「接着玩」只有本机存档里有开着的桌时才会出现。')
