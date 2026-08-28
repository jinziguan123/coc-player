# 公开介绍页

TRPG Player 的对外介绍与下载页。**纯静态，没有构建步骤** —— 目录里是什么，部署上去就是什么。

```
site/
├── index.html                介绍页（CSS、JS 全内联）
├── arch.html                 可交互架构图（独立自包含）
├── assets/
│   ├── favicon.svg
│   ├── fonts/                ZCOOL XiaoWei 拉丁子集（14 KB）+ 其 OFL 许可证
│   └── shots/                界面截图（WebP，共约 240 KB）
└── README.md                 本文件
```

## 换下载链接

只改 `index.html` 顶部 `window.TRPG_DOWNLOADS` 那一段：

```js
window.TRPG_DOWNLOADS = {
  version: 'v0.1.0',
  released: '2026-08-28',                 // 留空则不显示
  mac: { url: 'https://…/TRPG-Player_0.1.0_aarch64.dmg', size: '128 MB', note: 'macOS 11 及以上 · Apple Silicon / Intel' },
  win: { url: 'https://…/TRPG-Player_0.1.0_x64-setup.exe', size: '132 MB', note: 'Windows 10 / 11 · 64 位' },
}
```

`url` 留空时，按钮自动变成灰色的「链接待填」并禁止点击，页面不会出现死链。

## 部署

### Vercel

1. 新建 Project，导入 `jinziguan123/trpg-player`；
2. **Root Directory** 填 `site`，Framework Preset 选 **Other**；
3. Build Command 和 Output Directory 都留空（纯静态，不需要构建）。

### Netlify

1. Add new site → Import an existing project，选本仓库；
2. **Base directory** 填 `site`，**Publish directory** 也填 `site`，Build command 留空。

或者直接把 `site/` 整个文件夹拖进 Netlify Drop（`app.netlify.com/drop`）。

### 上线后要补的两件事

- `index.html` 里两处 `og:image` 改成绝对地址（`https://你的域名/assets/shots/home.webp`），
  否则部分平台抓不到分享预览图；
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

浏览器里把 `localStorage['trpg_player_token::']` 设为 `demo-token-0001`，就能看到预设调查员。
截完图用 `Pillow` 缩到 1600–2000 px 宽再转 WebP（quality 82）放进 `assets/shots/`。

## 页面文案的硬约束

改文案前先读 `docs/coc-rule-coverage.md` 和 `docs/release-gates.md`，其中几条是仓库里写死的对外口径：

- **不要写「完整实现 CoC 七版」**。孤注一掷、花费幸运目前只有 AI 裁量，不定性疯狂与魔法未实现；
- 未完成签名与公证前，**不得表述为「正式可信发行版」**；
- 发布页必须同时给出 `LICENSE` 与 `CONTENT_NOTICE.md` 的入口（页脚已有，别删）。
