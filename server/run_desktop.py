"""桌面版后端入口（PyInstaller 打成 sidecar 二进制，由 Tauri 外壳启动）。

挑一个可用端口起 uvicorn，并把端口以固定前缀打到 stdout，Tauri 外壳据此轮询
/api/health 就绪后，把窗口指向 http://127.0.0.1:<port>。

监听地址由「允许局域网加入」开关决定（见 ``app.services.net_access``）：默认只绑
回环，房主在设置里打开后重启才绑全部网卡。socket 绑定不能热改，所以必须重启——
界面上会明确提示。
"""
from __future__ import annotations

import socket

PORT_LINE_PREFIX = "COC_BACKEND_PORT "
PREFERRED_PORT = 8756


def _pick_port(host: str, preferred: int = PREFERRED_PORT) -> int:
    """优先用固定端口；被占用则让系统分配一个空闲端口。"""
    for candidate in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, candidate))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def main() -> None:
    import uvicorn

    from app.main import app
    from app.services import net_access

    host = net_access.bind_host()
    port = _pick_port(host)
    # 记下真实绑定情况：设置页据此判断「改了开关但还没重启」，并拼出客人要填的地址。
    app.state.listening_on_lan = host != net_access.LOOPBACK_HOST
    app.state.bound_port = port

    # 先告知外壳端口（外壳随后轮询 /api/health 确认就绪，不依赖此行的时序）。
    print(f"{PORT_LINE_PREFIX}{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
