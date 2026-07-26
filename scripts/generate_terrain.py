#!/usr/bin/env python3
"""为沙盘生成 11 张地貌纹理图片，调用 lucen.cc 的 gpt-image-2 模型。"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# lucen.cc 证书可能不被默认信任，跳过验证
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

API_KEY = "sk-157a4033171ac778771f9fd18e228d6fedd38ec3f5b3725b7d6f50e074143fdd"
BASE_URL = "https://lucen.cc"
OUTPUT_DIR = Path("apps/web/public/terrain")

BIOMES: list[tuple[str, str]] = [
    ("plain", "A seamless tileable top-down texture of open grassy plains with scattered tiny wildflowers, fantasy RPG map style, soft natural greens and warm golden highlights, no text no watermark, game texture, square"),
    ("forest", "A seamless tileable top-down texture of dense dark forest canopy from above, fantasy RPG map style, deep greens with occasional lighter highlights, overhead view of treetops, no text no watermark, game texture, square"),
    ("water", "A seamless tileable top-down texture of calm ocean water with subtle ripple patterns, fantasy RPG map style, deep blues and teals, no text no watermark, game texture, square"),
    ("coast", "A seamless tileable top-down texture of sandy coastline where land meets water, fantasy RPG map style, sandy beige blending into shallow blue water, no text no watermark, game texture, square"),
    ("desert", "A seamless tileable top-down texture of sandy desert with subtle dune patterns, fantasy RPG map style, warm golden sand tones, no text no watermark, game texture, square"),
    ("mountain", "A seamless tileable top-down texture of rocky mountain terrain with ridges and peaks from above, fantasy RPG map style, gray stone with lighter highlights, no text no watermark, game texture, square"),
    ("swamp", "A seamless tileable top-down texture of murky swamp or marshland with patches of dark water and vegetation, fantasy RPG map style, muddy greens and browns, no text no watermark, game texture, square"),
    ("urban", "A seamless tileable top-down texture of medieval town cobblestone pavement, fantasy RPG map style, warm browns and grays, no text no watermark, game texture, square"),
    ("ruin", "A seamless tileable top-down texture of ancient ruins with cracked stone floors and rubble, fantasy RPG map style, weathered gray and mossy tones, no text no watermark, game texture, square"),
    ("interior", "A seamless tileable top-down texture of indoor wooden floorboards, fantasy RPG map style, warm dark browns, no text no watermark, game texture, square"),
    ("road", "A seamless tileable top-down texture of a dirt road or trail with cart tracks, fantasy RPG map style, earthy browns with subtle wheel rut details, no text no watermark, game texture, square"),
]


def generate_image(prompt: str) -> str | None:
    """调用图像生成 API，返回图片下载 URL。"""
    body = json.dumps({
        "model": "gpt-image-2",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/v1/images/generations",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "x-openai-actor-authorization": "local-image-extension",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["data"][0]["url"]
    except Exception as e:
        print(f"  [ERROR] API 调用失败: {e}")
        return None


def download_image(url: str, dest: Path) -> bool:
    """下载图片到目标路径。"""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"  [ERROR] 下载失败: {e}")
        return False


def convert_to_webp(png_path: Path, webp_path: Path) -> bool:
    """将 PNG 转为 WebP。"""
    # 尝试 cwebp
    try:
        subprocess.run(
            ["cwebp", "-q", "85", str(png_path), "-o", str(webp_path)],
            check=True, capture_output=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # 尝试 ImageMagick
    try:
        subprocess.run(
            ["convert", str(png_path), "-quality", "85", str(webp_path)],
            check=True, capture_output=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # 尝试 ffmpeg
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(png_path), "-quality", "85", str(webp_path)],
            check=True, capture_output=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    return False


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=== 开始生成 11 张沙盘地貌纹理 ===")
    print(f"输出目录: {OUTPUT_DIR.resolve()}")
    print(f"共 {len(BIOMES)} 个地貌\n")

    for i, (biome, prompt) in enumerate(BIOMES, 1):
        print(f"[{i}/{len(BIOMES)}] {biome} - 生成中...")

        image_url = generate_image(prompt)
        if not image_url:
            print(f"  [SKIP] 跳过 {biome}\n")
            continue

        print(f"  图片 URL: {image_url}")

        png_path = OUTPUT_DIR / f"{biome}.png"
        if not download_image(image_url, png_path):
            print(f"  [SKIP] 跳过 {biome}\n")
            continue

        size_kb = png_path.stat().st_size / 1024
        print(f"  PNG 已保存: {png_path} ({size_kb:.0f} KB)")

        # 转换 WebP
        webp_path = OUTPUT_DIR / f"{biome}.webp"
        if convert_to_webp(png_path, webp_path):
            size_kb = webp_path.stat().st_size / 1024
            print(f"  WebP 已保存: {webp_path} ({size_kb:.0f} KB)")
        else:
            print(f"  [WARN] 未找到 WebP 转换工具 (cwebp/convert/ffmpeg)，保留 PNG")

        print()

        # 避免触发限流
        if i < len(BIOMES):
            time.sleep(2)

    print("=== 全部完成! ===")
    print("文件列表:")
    for f in sorted(OUTPUT_DIR.iterdir()):
        size = f.stat().st_size / 1024
        print(f"  {f.name}  ({size:.0f} KB)")


if __name__ == "__main__":
    main()
