#!/bin/bash
set -e

# 密钥从环境变量读，别写回文件（仓库 CI 带 gitleaks，硬编码一旦提交即泄漏）
API_KEY="${TERRAIN_API_KEY:?请先设置 TERRAIN_API_KEY 环境变量}"
BASE_URL="https://lucen.cc"
OUTPUT_DIR="apps/web/public/terrain"

mkdir -p "$OUTPUT_DIR"

# 11 种地貌及其生成提示词（无缝平铺纹理，俯视视角，奇幻RPG风格）
declare -A PROMPTS
PROMPTS[plain]="A seamless tileable top-down texture of open grassy plains with scattered tiny wildflowers, fantasy RPG map style, soft natural greens and warm golden highlights, no text no watermark, game texture"
PROMPTS[forest]="A seamless tileable top-down texture of dense dark forest canopy, fantasy RPG map style, deep greens with occasional lighter highlights, overhead view of treetops, no text no watermark, game texture"
PROMPTS[water]="A seamless tileable top-down texture of calm ocean or lake water with subtle ripple patterns, fantasy RPG map style, deep blues and teals, no text no watermark, game texture"
PROMPTS[coast]="A seamless tileable top-down texture of sandy coastline where land meets water, fantasy RPG map style, sandy beige blending into shallow blue water, no text no watermark, game texture"
PROMPTS[desert]="A seamless tileable top-down texture of sandy desert with subtle dune patterns, fantasy RPG map style, warm golden sand tones, no text no watermark, game texture"
PROMPTS[mountain]="A seamless tileable top-down texture of rocky mountain terrain with ridges and peaks, fantasy RPG map style, gray stone with lighter highlights, no text no watermark, game texture"
PROMPTS[swamp]="A seamless tileable top-down texture of murky swamp or marshland with patches of dark water and vegetation, fantasy RPG map style, muddy greens and browns, no text no watermark, game texture"
PROMPTS[urban]="A seamless tileable top-down texture of medieval town cobblestone pavement, fantasy RPG map style, warm browns and grays, no text no watermark, game texture"
PROMPTS[ruin]="A seamless tileable top-down texture of ancient ruins with cracked stone floors and rubble, fantasy RPG map style, weathered gray and mossy tones, no text no watermark, game texture"
PROMPTS[interior]="A seamless tileable top-down texture of indoor wooden floorboards or stone dungeon floor, fantasy RPG map style, warm dark browns and muted grays, no text no watermark, game texture"
PROMPTS[road]="A seamless tileable top-down texture of a dirt road or trail with cart tracks, fantasy RPG map style, earthy browns with subtle wheel rut details, no text no watermark, game texture"

# 按固定顺序处理，确保一致性
BIOMES=("plain" "forest" "water" "coast" "desert" "mountain" "swamp" "urban" "ruin" "interior" "road")

echo "=== 开始生成 11 张沙盘地貌纹理 ==="
echo "输出目录: $OUTPUT_DIR"
echo ""

for biome in "${BIOMES[@]}"; do
  prompt="${PROMPTS[$biome]}"
  echo "--- [$biome] 生成中..."
  
  # 调用图像生成 API
  response=$(curl -s -f "$BASE_URL/v1/images/generations" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_KEY" \
    -H "x-openai-actor-authorization: local-image-extension" \
    -d "$(python3 -c "import json; print(json.dumps({'model':'gpt-image-2','prompt': '$prompt','n':1,'size':'1024x1024'}))" 2>/dev/null || echo "{\"model\":\"gpt-image-2\",\"prompt\":\"$prompt\",\"n\":1,\"size\":\"1024x1024\"}")" 2>&1)
  
  # 提取图片 URL
  image_url=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['url'])" 2>/dev/null)
  
  if [ -z "$image_url" ]; then
    echo "  [ERROR] 无法获取图片 URL, 响应: $response"
    continue
  fi
  
  echo "  图片 URL: $image_url"
  
  # 下载 PNG 原图
  png_file="$OUTPUT_DIR/${biome}.png"
  curl -s -L -o "$png_file" "$image_url"
  
  if [ -f "$png_file" ] && [ -s "$png_file" ]; then
    echo "  PNG 已保存: $png_file ($(du -h "$png_file" | cut -f1))"
    
    # 转换为 webp（与项目现有纹理格式一致）
    webp_file="$OUTPUT_DIR/${biome}.webp"
    if command -v cwebp &>/dev/null; then
      cwebp -q 85 "$png_file" -o "$webp_file" 2>/dev/null
      echo "  WebP 已保存: $webp_file ($(du -h "$webp_file" | cut -f1))"
    elif command -v sips &>/dev/null; then
      # macOS 自带 sips，但不支持 webp；用 ffmpeg 或 imagemagick
      if command -v convert &>/dev/null; then
        convert "$png_file" -quality 85 "$webp_file"
        echo "  WebP 已保存 (ImageMagick): $webp_file ($(du -h "$webp_file" | cut -f1))"
      else
        echo "  [WARN] 未找到 cwebp/convert，跳过 WebP 转换"
      fi
    else
      echo "  [WARN] 未找到 WebP 转换工具，保留 PNG"
    fi
  else
    echo "  [ERROR] 下载失败"
  fi
  
  echo ""
  # 适当间隔，避免触发限流
  sleep 2
done

echo "=== 全部完成! ==="
echo "文件列表:"
ls -lh "$OUTPUT_DIR/"
