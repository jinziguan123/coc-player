export const BIOMES = [
  'plain',
  'forest',
  'water',
  'coast',
  'desert',
  'mountain',
  'swamp',
  'urban',
  'ruin',
  'interior',
  'road',
] as const

export const BIOME_LABELS: Record<string, string> = {
  plain: '原野',
  forest: '密林',
  water: '水域',
  coast: '海岸',
  desert: '荒漠',
  mountain: '山地',
  swamp: '沼泽',
  urban: '城镇',
  ruin: '废墟',
  interior: '室内',
  road: '道路',
}

/** 沙盘地貌专属纹理（gpt-image-2 生成），不产生运行时外部网络请求。 */
export const BIOME_TEXTURES: Record<string, string> = {
  plain: '/terrain/plain.webp',
  forest: '/terrain/forest.webp',
  water: '/terrain/water.webp',
  coast: '/terrain/coast.webp',
  desert: '/terrain/desert.webp',
  mountain: '/terrain/mountain.webp',
  swamp: '/terrain/swamp.webp',
  urban: '/terrain/urban.webp',
  ruin: '/terrain/ruin.webp',
  interior: '/terrain/interior.webp',
  road: '/terrain/road.webp',
}

/** 每种地貌准备几张变体贴图。
 *
 *  同一地貌的成片区域若共用一张图，平铺痕迹会非常明显（尤其相邻两格接缝处的图案完全对齐）。
 *  变体按 (q,r) 确定性选取（见 terrain.hexRng），同一格每次渲染结果稳定。
 *
 *  命名约定：主图 `<biome>.webp`，变体 `<biome>-2.webp`、`<biome>-3.webp`…
 *  **变体是可选的**——素材没生成时自动回落到主图，不会出现空白格，
 *  所以这个数字可以先调大、素材慢慢补。 */
export const BIOME_VARIANTS = 3

/** 某地貌第 i 张变体的路径（i=0 即主图）。 */
export function biomeTextureUrl(biome: string, variant: number): string {
  const base = BIOME_TEXTURES[biome] || BIOME_TEXTURES.plain
  if (variant <= 0) return base
  return base.replace(/\.webp$/, `-${variant + 1}.webp`)
}
