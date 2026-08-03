"""生成图片的落盘存储：文件存数据目录 images/（与 trpg.db 同处），事件只存相对 URL。

不存 SQLite BLOB——图片会让库文件暴涨，且迁移前的整库自动备份会跟着膨胀。
入盘统一转 JPEG（质量 85）：1024² 约 200KB，体积只有 PNG 的约 1/7。
"""

from __future__ import annotations

import base64
import io
import logging
import uuid

from app.config import settings

logger = logging.getLogger(__name__)

IMAGES_DIR = settings.db_path.parent / "images"


#: 手动上传的单张配图上限。生成图约 200KB，留足给相机直出/截图，又不至于把数据目录撑爆。
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def save_image_bytes(raw: bytes) -> str | None:
    """把图片字节转存为 JPEG 文件，返回相对 URL ``/api/images/{name}``；失败返回 None。

    与生成图共用同一条落盘路径：一律解码后重存为 JPEG。这既统一了体积与扩展名，也顺带把
    上传文件重新编码一遍——不认识的字节在 Image.open 就失败，不会原样落进图片目录。
    """
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(raw))
        im.load()                      # 提前触发解码：坏图在这里就抛，而不是 save 时才发现
        im = im.convert("RGB")
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.jpg"
        im.save(IMAGES_DIR / name, "JPEG", quality=85)
        return f"/api/images/{name}"
    except Exception:  # noqa: BLE001 — 存图失败只弃图，绝不上抛
        logger.exception("图片落盘失败（已弃图）")
        return None


def save_image_b64(b64: str) -> str | None:
    """把 base64 图片转存为 JPEG 文件，返回相对 URL ``/api/images/{name}``；失败返回 None。"""
    try:
        return save_image_bytes(base64.b64decode(b64))
    except Exception:  # noqa: BLE001 — base64 本身就坏时同样只弃图
        logger.exception("生成图片落盘失败（已弃图）")
        return None
