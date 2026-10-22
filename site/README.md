# 公开介绍页

CoC Player 的对外介绍与下载页。**纯静态，没有构建步骤** —— 目录里是什么，部署上去就是什么。

```
site/
├── index.html                介绍页（CSS、JS 全内联）
├── arch.html                 可交互架构图（独立自包含）
├── assets/
│   ├── favicon.svg
│   ├── fonts/                ZCOOL XiaoWei 拉丁子集（14 KB）+ 其 OFL 许可证
│   └── shots/                界面截图，两套主题各 11 张（WebP，共约 2 MB）
└── README.md                 本文件
```

## 主题

两套皮肤与应用同构：默认**密大装饰**（装饰艺术风，键名沿用 `gothic`）走 `:root` 基线，**羊皮纸**写 `data-theme="parchment"`，
色值直接取自 `apps/web/src/index.css` 的同名 token，改应用主题时记得对一下这边。

- 首选项存在 `localStorage['coc_site_theme']`，没存过则跟随系统 `prefers-color-scheme`；
- 防白闪的内联脚本在 `<head>`，必须留在 `<body>` 之前；
- **截图跟着主题换**：`<img data-shot="home">` 的 `src` 在切换时被改成
  `assets/shots/<主题>-home.webp`。加新截图时两套主题都要有，否则切过去会 404。

页面里不要再写死深色值。顶栏底、分隔线、代码块底、截图投影、警示边框都已抽成
`--topbar-bg` / `--rule-color` / `--code-bg` / `--shot-shadow` / `--warn-border`，
新增样式请沿用，否则羊皮纸下会破面。

## 动效

- **进场**：带 `.reveal` 的元素随滚动「从上方落下一点点 + 淡入」，由 `IntersectionObserver` 加 `.is-in` 解除，
  只触发一次，不会来回闪。
- **首屏**：日芒描线、标题依次落下，截图像两扇门从中间推开（`.hero.is-open`，脚本在第二帧加上）。
- **背景**：`.bg-layer` 是固定层（日芒射线 + 两团光），脚本把滚动进度写进 `--sy`（0→1），
  两层按不同系数反向位移，形成视差；hero 另有 `--hero` 控制淡出上移。
- **页签**：`[data-tabs]` 的黄铜底在按钮间滑动，同名 `[data-stack]` 里的截图与 `[data-caps]` 里的说明交叉淡入。
  叠放的截图都带 `data-shot`，切主题时一起换。
- 初始隐藏态挂在 `<html class="js-anim">` 下，**没有 JS 时内容照常显示**；
  `prefers-reduced-motion: reduce` 下位移、淡入、视差全部关闭。

改这块时务必自测这三种情况（内容被永久藏住是这里最容易犯的错）：正常滚动全部显现、
关掉 JS 内容可见、开启「减少动态效果」内容可见。

## 换下载链接

只改 `index.html` 顶部 `window.COC_DOWNLOADS` 那一段：

```js
window.COC_DOWNLOADS = {
  version: 'v0.1.0',
  released: '2026-08-28',                 // 留空则不显示
  mac: { url: 'https://…/CoC-Player_0.1.0_aarch64.dmg', size: '128 MB', note: 'macOS 11 及以上 · Apple Silicon / Intel' },
  win: { url: 'https://…/CoC-Player_0.1.0_x64-setup.exe', size: '132 MB', note: 'Windows 10 / 11 · 64 位' },
}
```

`url` 留空时按钮变虚线边框的「链接待填」并禁止点击，页面不会出现死链。

## 部署

### Vercel

1. 新建 Project，导入 `jinziguan123/coc-player`；
2. **Root Directory** 填 `site`，Framework Preset 选 **Other**；
3. Build Command 和 Output Directory 都留空（纯静态，不需要构建）。

### Netlify

1. Add new site → Import an existing project，选本仓库；
2. **Base directory** 填 `site`，**Publish directory** 也填 `site`，Build command 留空。

或者直接把 `site/` 整个文件夹拖进 Netlify Drop（`app.netlify.com/drop`）。

### 上线后要补的两件事

- `index.html` 里两处 `og:image` 改成绝对地址（`https://你的域名/assets/shots/home.webp`），
  否则部分平台抓不到分享预览图（注意文件名带主题前缀：`gothic-home.webp`）；
- 若绑定了自定义域名，把域名填进 GitHub 仓库的 About → Website。

## 重新生成截图

截图必须来自**内容干净的库**，不能用日常开发库 —— 后者含商业模组与规则书原文，
放到公网上等于对外分发受版权保护的内容。整个流程三步，都在仓库根目录跑：

```bash
# ① 从零建演示库 tmp/site-demo/（会先清空重建），并摆好四局演示对局
server/.venv/bin/python scripts/stage_site_demo.py

# ② 前端打包，再用演示库起沙箱后端（由后端同源托管前端产物）
pnpm --filter web build
cd server && D=$PWD/../tmp/site-demo && \
  DB_PATH=$D/trpg.db LOG_DIR=$D/logs PLUGINS_DIR=$D/plugins \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8011

# ③ 另开一个终端，两套主题各拍一遍（需要系统装了 Chrome 与 cwebp）
pnpm site:shots                       # 全部
pnpm site:shots --only=game,game-vn   # 只重拍几张
```

演示库里只有项目原创的《雾港失灯事件》（`server/app/content/onboarding.py`）、预设调查员
许闻舟，以及从 `server/images/` 拷来的三张为这份模组生成的配图（港务所、雾中栈桥、林恩立绘）。
`ai_settings.json` 跟随 `DB_PATH` 落在演示库目录，里面没有任何模型配置，截图不会带出密钥或接口地址。
`shoot-site-shots.mjs` 开拍前会核对后端背后的库里只有这一份模组，开错库直接停。

`stage_site_demo.py` 摆的四局（叙事、台词、玩家行动都是为这份模组手写的，不调用模型；
建局、入座、真人 KP 动作、战斗结算都走真实服务层，战斗随机性用固定种子钉住）：

| 会话 | 用在 | 内容 |
|---|---|---|
| `game` | `home`、`game`、`game-vn` | AI KP 局，港务所 → 雾中栈桥，最后一句是林恩的台词 |
| `combat` | `combat` | AI KP 局，灯室里对深潜者幼体的战斗轮，停在玩家选反击 / 闪避 |
| `kp_desk` | `kp-desk` | 真人 KP 局的 KP 席，港务所，待办里挂着一张等玩家投的聆听 |
| `kp_combat` | `kp-combat` | 另一局真人 KP 局，灯室开战后的 KP 观局台 |

会话 id 与各席 token 写在 `tmp/site-demo/demo.json`，截图脚本从那里读；想换个目录，
两个脚本分别用 `--out=` / `--demo=` 指过去。

截出来的图（`<主题>-<名字>.webp`，`gothic` 与 `parchment` 各一套，一律 16:10）：

| 名字 | 尺寸 | 画面 |
|---|---|---|
| `home` | 2000×1250 | 首页卷宗，「继续上次的桌」 |
| `game` | 2000×1250（1.5×） | 玩家视角聊天流，右侧角色卡展开 |
| `game-vn` | 2000×1250（1.5×） | 同一局的对话框模式，场景图铺满 |
| `combat` | 2000×1250（1.5×） | 玩家视角的战斗轮：战场、先攻轨、反应选择 |
| `kp-desk` | 2000×1250（1.5×） | 真人 KP 值班台 |
| `kp-combat` | 2000×1250（1.5×） | 真人 KP 观局台 |
| `charcard` | 2000×1250（1.5×） | 角色名录里许闻舟的完整角色卡 |
| `sandbox` / `rulebooks` / `char-wizard` / `settings` | 1440×900 | 模组沙盘、规则书页、建卡向导、AI 配置空状态 |

改名或加图后记得两套都在，切主题时缺哪张就会 404。拍完关掉第 ② 步的后端；
`tmp/site-demo/` 已在 `.gitignore` 里，可随时删掉重建。

## 页面文案的硬约束

改文案前先读 `docs/coc-rule-coverage.md` 和 `docs/release-gates.md`，其中几条是仓库里写死的对外口径：

- **不要写「完整实现 CoC 七版」**。孤注一掷、花费幸运目前只有 AI 裁量，不定性疯狂与魔法未实现；
- 未完成签名与公证前，**不得表述为「正式可信发行版」**；
- 发布页必须同时给出 `LICENSE` 与 `CONTENT_NOTICE.md` 的入口（页脚已有，别删）。
