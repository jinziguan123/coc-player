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

    # extra="ignore"：.env 里的历史遗留变量（如已废弃的 OPENAI_API_KEY）不该让进程起不来。
    # pydantic-settings 默认是 forbid，于是从仓库根启动后端会崩在一句难懂的校验错误上；
    # 打包版若被以带 .env 的目录为 CWD 拉起也一样。本类只认下面声明的几项，其余忽略。
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
