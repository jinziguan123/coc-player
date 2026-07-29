# Tauri 打包资源

`trpg-server/` 是 PyInstaller 产出的后端 onedir，由打包流程填充，**不进版本库**
（见 [`docs/packaging.md`](../../docs/packaging.md)：先 `rm -rf resources/trpg-server`
再从 `server/dist/trpg-server` 整个拷过来）。

本文件本身是有用途的，别删：`tauri.conf.json` 的 `bundle.resources` 用
`resources/**/*` 匹配这个目录，而 Tauri 的 glob **匹配不到任何文件就直接报错**。
没有它，开发态（尚未打包过后端时）连 `cargo check` / `cargo test` 都跑不起来，
Rust 侧的隧道测试就没人跑得动了。
