import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings


def _data_base() -> Path:
    """数据根目录：
    - 开发/源码运行：仓库内 server/（db 与素材落在项目里，行为与以前一致）。
    - 打包运行（PyInstaller frozen）：用户可写的 app-data 目录（只读的 .app/安装目录不能写库）。
      mac → ~/Library/Application Support/TRPGPlayer；win → %APPDATA%/TRPGPlayer；
      其它 → ~/.local/share/TRPGPlayer。
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "TRPGPlayer"
        elif sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home()) / "TRPGPlayer"
        else:
            base = Path.home() / ".local" / "share" / "TRPGPlayer"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(__file__).parent.parent


_BASE = _data_base()


class Settings(BaseSettings):
    # AI 密钥/地址的唯一真源是设置页（ai_settings.json 的激活 profile）；此处不再放 AI 配置，
    # 也不再从 .env 读取（旧的 DEEPSEEK_API_KEY/BASE_URL 回退已移除）。
    db_path: Path = _BASE / "trpg.db"
    debug: bool = True
    # SQL 回显独立于 debug：默认关闭，避免每次请求把整串 SELECT/INSERT 刷屏。
    # 真要调 SQL 时在 .env 设 SQL_ECHO=true。
    sql_echo: bool = False
    # 应用自身日志级别（不影响 uvicorn 的访问日志，它有自己的一套）。
    # 默认 INFO：回合耗时、配图完成、生成取消这些运行信息本就是给人看的。
    # 嫌吵可在 .env 设 LOG_LEVEL=WARNING。
    log_level: str = "INFO"
    # 应用日志落盘目录（LOG_DIR 可覆盖）。开发态在 server/logs/，打包态在 app-data/logs/，
    # 与数据库、素材同处。app.log 收全部应用日志；timing.log 只收「耗时|…」与思考占比告警，
    # 方便直接对账「一个回合慢在哪一段」——此前日志只进 stdout，打包版那个文件恒为空。
    log_dir: Path = _BASE / "logs"
    # 第三方规则插件目录（PLUGINS_DIR 可覆盖）。开发态 server/plugins/，打包态 app-data/plugins/。
    # 插件以本应用的权限运行、不做沙箱，只放你信任的插件。见 app/rules/plugins.py。
    plugins_dir: Path = _BASE / "plugins"
    # 终检/去口癖这类「检查与改写」任务是否先用快模型、不可用再回退叙事模型。
    # 默认开启：快模型槽位通常关思考，这两步能省下每次 20~40s；设为 false 全部走叙事模型。
    checks_use_fast_model: bool = True
    # KP 上下文的易变档（线索台账、NPC 记忆、幕后动态、摘录、封路……）放在哪：
    # true = 作为一条「当前局面」快照放到历史尾部（最后一条玩家消息之前），系统提示只剩
    #        静态+半静态两档，「系统提示 + 剧情摘要 + 历史」成为跨回合稳定的缓存前缀；
    # false = 旧装配，易变档紧跟在系统提示末尾（排在历史之前，每轮都让整段历史缓存失效）。
    # 留着开关是为了对比两种摆法的叙事质量，见 app.ai.context._assemble_kp_messages。
    kp_volatile_tail: bool = True

    # extra="ignore"：.env 里的历史遗留变量（如已废弃的 OPENAI_API_KEY）不该让进程起不来。
    # pydantic-settings 默认是 forbid，于是从仓库根启动后端会崩在一句难懂的校验错误上；
    # 打包版若被以带 .env 的目录为 CWD 拉起也一样。本类只认下面声明的几项，其余忽略。
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
