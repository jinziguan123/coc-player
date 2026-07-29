mod netlink;

use std::sync::Mutex;

use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// 后端 sidecar 的运行时状态：它自选的端口，以及子进程句柄（退出时杀掉）。
#[derive(Default)]
struct Backend {
    port: Mutex<Option<u16>>,
    child: Mutex<Option<CommandChild>>,
}

/// 供加载页轮询：拿到后端端口后返回，未就绪返回 null。
#[tauri::command]
fn backend_port(state: State<Backend>) -> Option<u16> {
    *state.port.lock().unwrap()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![
            backend_port,
            netlink::netlink_start,
            netlink::netlink_stop,
            netlink::netlink_connect,
            netlink::netlink_disconnect,
            netlink::netlink_status,
            netlink::netlink_approve,
            netlink::netlink_reject,
            netlink::netlink_revoke,
            netlink::netlink_invite,
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // 准入名册要落在用户可写目录，所以 Netlink 得等 setup 拿到路径才能建。
            let handle = app.handle().clone();
            let data_dir = handle.path().app_data_dir()?;
            handle.manage(netlink::Netlink::new(data_dir));

            // 拉起打包进来的后端（PyInstaller onedir，放在 resources/trpg-server/）。
            let resource_dir = handle.path().resource_dir()?;
            let exe = resource_dir
                .join("resources")
                .join("trpg-server")
                .join("trpg-server");
            // 资源拷贝后可能丢掉可执行位，补上，否则 spawn 会失败。
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(meta) = std::fs::metadata(&exe) {
                    let mut perm = meta.permissions();
                    perm.set_mode(perm.mode() | 0o755);
                    let _ = std::fs::set_permissions(&exe, perm);
                }
            }
            // 隧道密钥随进程启动随机生成，经环境变量交给后端。后端据此把「经隧道
            // 进来的远端客人」和「房主本人」区分开——两者的源 IP 都是回环，不给
            // 它这个凭据就分不出来，客人会拿到房主的 AI 配置与限速豁免。
            // 见 server/app/services/net_access.py 的 peer_kind。
            let secret = handle.state::<netlink::Netlink>().secret().to_string();
            let (mut rx, child) = app
                .shell()
                .command(exe.to_string_lossy().to_string())
                .env(netlink::SECRET_ENV, secret)
                .spawn()?;
            handle
                .state::<Backend>()
                .child
                .lock()
                .unwrap()
                .replace(child);

            // 读取 sidecar stdout，解析它打印的 `TRPG_BACKEND_PORT <port>`。
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(bytes) = event {
                        let line = String::from_utf8_lossy(&bytes);
                        if let Some(rest) = line.trim().strip_prefix("TRPG_BACKEND_PORT ") {
                            if let Ok(p) = rest.trim().parse::<u16>() {
                                *handle.state::<Backend>().port.lock().unwrap() = Some(p);
                            }
                        }
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // 应用退出时杀掉后端 sidecar，避免残留占端口。
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(child) = app_handle.state::<Backend>().child.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
