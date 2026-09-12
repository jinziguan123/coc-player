import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.config import settings
from app.database import SessionLocal
from app.services.ai_quota import QuotaExceeded
from app.services.rate_limit import limiter

logger = logging.getLogger(__name__)

#: 落盘日志文件名与轮转上限。app.log 收全部应用日志；timing.log 只收回合耗时与思考占比
#: 告警——玩家说「感觉最近很慢」时，直接看这一个文件就能对账各阶段，不必在几千行里翻。
APP_LOG_NAME = "app.log"
TIMING_LOG_NAME = "timing.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
#: 给文件 handler 打的标记：重配时据此只移除自己装的 handler，不动控制台/测试临时 handler。
_FILE_HANDLER_MARK = "_coc_file_handler"
#: 记在 app logger 上的当前落盘目录：目录变了才重配，重复调用不会装两遍、写两行。
_LOG_DIR_ATTR = "_coc_log_dir"


def _is_timing_record(record: logging.LogRecord) -> bool:
    """只放行「耗时|…」与「本回合…」两类记录进 timing.log。

    ``record.getMessage()`` 会把 %-参数拼成最终文本（耗时行都是这种写法）；拿不到就丢弃，
    绝不让一条日志因为过滤器本身出错而消失。
    """
    try:
        return record.getMessage().startswith(("耗时|", "本回合"))
    except Exception:  # noqa: BLE001 — 过滤器只做筛选，不许反过来影响日志
        return False


def _file_handler(path: Path, *, timing_only: bool = False) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    if timing_only:
        handler.addFilter(_is_timing_record)
    setattr(handler, _FILE_HANDLER_MARK, True)
    return handler


def _add_file_handlers(app_logger: logging.Logger, log_dir: Path) -> None:
    """把两个落盘 handler 装到 app logger 上；失败由调用方兜底，不在这里吞。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    app_logger.addHandler(_file_handler(log_dir / APP_LOG_NAME))
    app_logger.addHandler(_file_handler(log_dir / TIMING_LOG_NAME, timing_only=True))


def _configure_app_logging() -> None:
    """给 ``app.*`` 这棵 logger 树装上 handler、级别与落盘文件。

    此前全项目没有任何日志配置，于是走 Python 的默认值：根 logger 级别 WARNING、无 handler。
    结果是**后端 27 处 logger.info 从来没有输出过**——包括各环节的「耗时|…」埋点。
    埋点在跑却看不见，比没有埋点更糟：查性能问题时会以为「日志里没有就是没发生」。

    只配 ``app`` 这一棵，不碰根 logger：uvicorn 的访问日志有自己的 handler，
    动根会让它重复输出。propagate=False 同理——防止本树的记录再冒泡到根被打第二遍。

    除了 stdout，再把同一批记录写进 ``settings.log_dir``：打包版由 Tauri 拉起的 sidecar
    stdout 并不落到那个 ``TRPG Player.log``（实测恒为 0 字节），只写 stdout 等于没有日志。
    app.log 全量、timing.log 只留「耗时|…」与思考占比告警；轮转封顶，不会无限涨。
    """
    app_logger = logging.getLogger("app")
    app_logger.setLevel(settings.log_level.upper())
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        app_logger.addHandler(handler)

    desired_dir = Path(settings.log_dir)
    if getattr(app_logger, _LOG_DIR_ATTR, None) != desired_dir:
        # 目录变了（测试换临时目录、或运行期改配置）才重配：先摘掉上次装的文件 handler，
        # 免得同一行被写两遍；控制台与调用方自己挂的 handler 一律不动。
        for old in list(app_logger.handlers):
            if getattr(old, _FILE_HANDLER_MARK, False):
                app_logger.removeHandler(old)
                try:
                    old.close()
                except Exception:  # noqa: BLE001 — 关旧 handler 失败不该挡住新配置
                    pass
        try:
            _add_file_handlers(app_logger, desired_dir)
        except Exception:  # noqa: BLE001 — 落盘失败必须降级，不能拖垮启动
            # 清掉目录标记，下次调用（如运行期修好权限后再配）还会重试，而不是永久放弃。
            setattr(app_logger, _LOG_DIR_ATTR, None)
            app_logger.warning(
                "应用日志无法写入 %s，本次仅输出到控制台", desired_dir, exc_info=True,
            )
        else:
            setattr(app_logger, _LOG_DIR_ATTR, desired_dir)
            app_logger.info(
                "应用日志写入 %s（回合耗时另见 %s）",
                desired_dir / APP_LOG_NAME, desired_dir / TIMING_LOG_NAME,
            )
    app_logger.propagate = False


_configure_app_logging()

# 迁移失败进入的「维护模式」：非空即代表启动迁移失败，所有请求返回可读错误页而非
# 以「新代码 + 旧 schema」带病运行。开发与打包环境都启用，避免业务接口继续返回 500。
_MIGRATION_ERROR: str | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 打包首次启动先从内置种子初始化 app-data（开箱即用），再把数据库升到最新（升级前自动备份）。
    global _MIGRATION_ERROR
    from app.database import run_migrations, seed_if_needed

    seed_if_needed()
    try:
        run_migrations()
    except Exception as e:
        logger.exception("启动自动迁移失败，请手动执行 alembic upgrade head")
        # 不以半新半旧 schema 运行，进入维护模式，请求返回可读错误页。
        _MIGRATION_ERROR = str(e)
    yield


app = FastAPI(title="CoC Player", version="0.1.0", lifespan=lifespan)

# 速率限制（slowapi 要求把 limiter 挂在 app.state 上）。限的是房间码枚举与烧额度的
# 操作，房主本机豁免；见 app/services/rate_limit.py。
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(QuotaExceeded)
async def _quota_exceeded_handler(_request: Request, exc: QuotaExceeded) -> JSONResponse:
    """房间 AI 配额用尽 → 429。在这里统一映射，生成入口的九个调用点不必各自处理。"""
    return JSONResponse(
        {"detail": f"本房间的 AI 配额已用尽（上限 {exc.limit_spec}），请稍后再试或在设置里调整。"},
        status_code=429,
    )

# 本进程实际绑在哪：默认按回环记，``run_desktop.py`` 启动时按开关与实挑端口改写。
# 设置页据此判断「改了开关但还没重启」，并给出客人要填的完整地址。
app.state.listening_on_lan = False
app.state.bound_port = None


@app.middleware("http")
async def _maintenance_gate(request: Request, call_next):
    """迁移失败的维护模式：除健康检查外一律返回可读错误页，提示用户升级/从备份恢复。"""
    if _MIGRATION_ERROR is not None and request.url.path != "/api/health":
        return HTMLResponse(
            "<html><head><meta charset='utf-8'><title>需要维护</title></head>"
            "<body style='font-family:serif;background:#0c0e13;color:#e8dcc0;"
            "text-align:center;padding:12vh 8vw'>"
            "<h1>数据库升级未完成</h1>"
            "<p>本次启动的自动迁移失败，为保护你的存档，应用已暂停运行。</p>"
            f"<pre style='color:#98a2ad;white-space:pre-wrap'>{_MIGRATION_ERROR}</pre>"
            "<p>升级前的自动备份位于数据目录下的 <code>trpg.db.bak-*</code>；"
            "可升级到匹配的程序版本后重试，或从备份恢复。</p>"
            "</body></html>",
            status_code=503,
        )
    return await call_next(request)


#: 局域网客户端在获批之前也能打的几个口子。少一个都会让「敲门」这件事无从发生：
#: 客人要先探得到主机、报得上名字、查得到自己排到哪儿了，房主才有得批。
_KNOCK_PATHS = frozenset({"/api/health", "/api/net/me", "/api/net/knock"})


@app.middleware("http")
async def _peer_gate(request: Request, call_next):
    """来源校验：见 ``app.services.net_access`` 的两道闸说明。

    注册在维护闸之后 → 位于其外层，未授权来源连维护页也拿不到。

    IP 那道闸只回答「这个网段可不可信」。可信网段里仍是一台台具体的设备，第二道闸
    按客户端逐个放行——直连那边本来就要房主逐个点头，局域网这边没道理整段敞开。
    直连客人不走第二道：Tauri 侧的 roster 已经批过一次，隧道密钥就是那次批准的凭据。
    """
    from app.services import lan_roster, net_access

    client = request.client.host if request.client else None
    if not net_access.is_trusted_peer(client):
        return JSONResponse(
            {"detail": "房主未允许局域网加入，或请求来自不可信网络"}, status_code=403
        )

    if net_access.peer_kind(client, request.headers) == "lan":
        db = SessionLocal()
        try:
            status = lan_roster.check_in(
                db, request.headers.get("x-player-token") or "", client
            )
        finally:
            db.close()
        if status != "approved" and request.url.path not in _KNOCK_PATHS:
            return JSONResponse(
                {
                    "detail": (
                        "房主还没同意这台设备加入" if status == "pending"
                        else "房主拒绝了这台设备"
                    ),
                    "code": f"lan_{status}",
                },
                status_code=403,
            )
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    # 打包客户端 / 跨机联机：客户端从任意 origin（tauri://、其它机器）连主机后端。
    # 鉴权走 X-Player-Token 头、不用 cookie，故允许任意来源是安全的。
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api/health")
def health():
    """健康检查兼版本握手。

    ``protocol_version`` 是房间事件协议的版本（见 ``app.services.room_events``）。
    客人连接房主前先比对：不一致说明两端版本不匹配，应当明确提示升级，而不是连上去
    以「有些事件收不到、界面莫名其妙不更新」的方式半坏。
    """
    from app.services.room_events import PROTOCOL_VERSION

    return {"status": "ok", "protocol_version": PROTOCOL_VERSION}


# 单服务 / 打包模式：若存在前端构建产物（apps/web/dist），由后端同源托管，Tauri 窗口直接指向
# 本机后端即可（同源 → /api、SSE 都不涉及跨域）。dev 下 dist 不存在则跳过，前端仍走 vite。
# 打包（PyInstaller frozen）时前端会被放到 sys._MEIPASS，下面按需覆盖。
def _frontend_dist() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web_dist"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2] / "apps" / "web" / "dist"


_DIST = _frontend_dist()
if _DIST.is_dir():
    _assets = _DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    # SPA 兜底路由不是 REST 契约的一部分，显式排除出 OpenAPI。
    # 此前它是否进入 schema 取决于被 gitignore 的 apps/web/dist 是否存在，
    # 导致干净 checkout 中「导出契约 → git diff」永远漂移。
    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        # /api/* 未命中的一律 404（交给 API 层语义），其余非 API 路径回退到 SPA 入口，
        # 由前端路由接管（刷新 /game/:id 等深链也能正常返回 index.html）。
        if full_path.startswith("api"):
            raise HTTPException(status_code=404)
        candidate = (_DIST / full_path).resolve()
        if full_path and candidate.is_file() and _DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
