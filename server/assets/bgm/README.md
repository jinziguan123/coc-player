# 内置背景音乐曲库

按氛围分目录存放可循环的曲目（`.mp3` / `.ogg` / `.m4a` / `.wav`），后端启动后按目录扫描，
无需维护清单：

| 目录 | 氛围 | 何时播放 |
| --- | --- | --- |
| `calm/` | 安宁 | 场景标为 calm，或 KP 指定 |
| `explore/` | 调查 | 默认；危险度 calm 的场景 |
| `tense/` | 不安 | 危险度 uneasy 的场景 |
| `horror/` | 恐怖 | 危险度 dangerous / deadly 的场景 |
| `mythos/` | 神话 | 场景标为 mythos，或 KP 指定 |
| `sorrow/` | 悲伤 | 场景标为 sorrow，或 KP 指定 |
| `combat/` | 战斗 | 战斗轮进行中（自动） |
| `chase/` | 追逐 | 追逐进行中（自动） |

某个氛围没有曲目时，前端回落到 `explore/`；仍没有就静音。

**授权**：这里只放 CC0 / CC-BY 等允许随软件再分发的曲目，每加一首都要在 `credits.json`
登记来源、作者、许可证与署名文本（应用内「音乐致谢」读它），并同步 `CREDITS.md`。
`tests/test_bgm.py` 会校验每个内置音频文件都已登记。Tabletop Audio、Pixabay 等「仅限个人使用 / 禁止作为独立素材再分发」
的来源不能放进来。

**用户自定义曲库**：在数据目录下建 `bgm/<氛围>/` 放入曲目即可（源码运行时是 `server/bgm/`，
打包版在 app-data 目录，如 macOS 的 `~/Library/Application Support/TRPGPlayer/bgm/`）。
某个氛围下放了自己的曲子，就整组替换这里的内置曲目。
