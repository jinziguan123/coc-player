/**
 * CoC 7 版派生值（HP / MP / SAN / MOV / 伤害加值 / 体格 / 闪避）。
 *
 * 这是 `server/app/rules/coc/character.py::compute_derived` 的前端镜像：建卡时属性每点一下
 * 就要刷新预览，走一次网络太黏；但**两份实现必然会漂**——原先内联在建卡页里的那份
 * 就漂出了两个 bug（MOV 的 9 永远取不到、伤害加值 165 以上全归 1D6）。
 * 所以这里的每个数字都由 cocDerived.test.ts 钉住，和 Python 侧 test_coc_chargen.py 逐条对齐；
 * 改规则要两边一起改，测试会拦住只改一边的情况。
 */

export interface DerivedStats {
  hp: number
  mp: number
  san: number
  mov: number
  db: string
  build: number
  dodge: number
}

/** 伤害加值 / 体格表（按 STR+SIZ）。7 版在 164 以上每 80 点进一档。 */
const DAMAGE_BONUS: ReadonlyArray<readonly [number, string, number]> = [
  [64, '-2', -2],
  [84, '-1', -1],
  [124, '0', 0],
  [164, '1D4', 1],
  [204, '1D6', 2],
  [284, '2D6', 3],
  [364, '3D6', 4],
  [444, '4D6', 5],
]

export function damageBonus(combined: number): { db: string; build: number } {
  for (const [ceiling, db, build] of DAMAGE_BONUS) {
    if (combined <= ceiling) return { db, build }
  }
  const extra = Math.floor((combined - 445) / 80) + 1
  return { db: `${4 + extra}D6`, build: 5 + extra }
}

/** 年龄对移动力的减值（40 岁起每十年 −1）。 */
export function moveAgePenalty(age: number): number {
  if (age >= 80) return 5
  if (age >= 70) return 4
  if (age >= 60) return 3
  if (age >= 50) return 2
  if (age >= 40) return 1
  return 0
}

export function deriveStats(attrs: Record<string, number>, age: number): DerivedStats {
  const str = attrs.STR ?? 50
  const con = attrs.CON ?? 50
  const siz = attrs.SIZ ?? 50
  const dex = attrs.DEX ?? 50
  const pow = attrs.POW ?? 50

  // 三档的判据不同：**两者都** > SIZ 才是 9，都 < SIZ 是 7，其余是 8。
  // 写成「任一 ≥ SIZ 就是 8」会把第三档整个吞掉。
  let mov: number
  if (str > siz && dex > siz) mov = 9
  else if (str < siz && dex < siz) mov = 7
  else mov = 8
  mov -= moveAgePenalty(age)

  const { db, build } = damageBonus(str + siz)
  return {
    hp: Math.floor((con + siz) / 10),
    mp: Math.floor(pow / 5),
    san: pow,
    mov,
    db,
    build,
    dodge: Math.floor(dex / 2),
  }
}
