# 公开介绍页

CoC Player 的对外介绍与下载页。**纯静态，没有构建步骤** —— 目录里是什么，部署上去就是什么。

```
site/
├── index.html                介绍页（CSS、JS 全内联）
├── arch.html                 可交互架构图（独立自包含）
├── assets/
│   ├── favicon.svg
│   ├── fonts/                ZCOOL XiaoWei 拉丁子集（14 KB）+ 其 OFL 许可证
│   └── shots/                界面截图，两套主题各 5 张（WebP，共约 485 KB）
└── README.md                 本文件
```

## 主题

两套皮肤与应用同构：默认**暗夜哥特**走 `:root` 基线，**羊皮纸**写 `data-theme="parchment"`，
色值直接取自 `apps/web/src/index.css` 的同名 token，改应用主题时记得对一下这边。

- 首选项存在 `localStorage['coc_site_theme']`，没存过则跟随系统 `prefers-color-scheme`；
- 防白闪的内联脚本在 `<head>`，必须留在 `<body>` 之前；
- **截图跟着主题换**：`<img data-shot="home">` 的 `src` 在切换时被改成
  `assets/shots/<主题>-home.webp`。加新截图时两套主题都要有，否则切过去会 404。

页面里不要再写死深色值。顶栏底、分隔线、代码块底、截图投影、警示边框都已抽成
`--topbar-bg` / `--rule-color` / `--code-bg` / `--shot-shadow` / `--warn-border`，
新增样式请沿用，否则羊皮纸下会破面。

## 动效

- **进场**：区块随滚动「从上方落下一点点 + 淡入」，由 `IntersectionObserver` 加 `.is-in` 解除，
  只触发一次，不会来回闪。
- **背景**：`.bg-layer` 是固定层，脚本把滚动进度写进 `--sy`（0→1），层内两片渐变按不同系数
  反向位移，形成视差；hero 另有 `--hero` 控制淡出上移。
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
放到公网上等于对外分发受版权保护的内容。干净库的建法：

```bash
mkdir -p tmp/shotdata
DB_PATH=$PWD/tmp/shotdata/trpg.db server/.venv/bin/python - <<'PY'
import sys, os; sys.path.insert(0, "server"); os.chdir("server")
from app.database import run_migrations, SessionLocal
run_migrations()
from app.services.onboarding_service import _ensure_module, _ensure_character
db = SessionLocal(); m = _ensure_module(db); _ensure_character(db, m, "demo-token-0001"); db.commit()
PY
```

这样建出来的库只有项目原创的《雾港失灯事件》和预设调查员，且 `ai_settings.json`
跟随 `DB_PATH` 落在同一目录，截图里不会带出真实密钥。

再用它起后端（前端产物由后端同源托管，先跑一次 `pnpm --filter web build`）：

```bash
cd server && DB_PATH=$PWD/../tmp/shotdata/trpg.db .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011
```

浏览器里把 `localStorage['trpg_player_token::']` 设为 `demo-token-0001`，就能看到预设调查员；
再把 `localStorage['trpg_theme']` 设成 `gothic` 或 `parchment`，**两套主题各抓一遍**。

截完图用 `Pillow` 缩到 1600–2000 px 宽再转 WebP（quality 82），按
`<主题>-<名字>.webp` 命名放进 `assets/shots/`。改名或加图后记得两套都在，
切主题时缺哪张就会 404。

## 页面文案的硬约束

改文案前先读 `docs/coc-rule-coverage.md` 和 `docs/release-gates.md`，其中几条是仓库里写死的对外口径：

- **不要写「完整实现 CoC 七版」**。孤注一掷、花费幸运目前只有 AI 裁量，不定性疯狂与魔法未实现；
- 未完成签名与公证前，**不得表述为「正式可信发行版」**；
- 发布页必须同时给出 `LICENSE` 与 `CONTENT_NOTICE.md` 的入口（页脚已有，别删）。
