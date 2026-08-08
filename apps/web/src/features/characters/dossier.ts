/**
 * 档案编号：由角色 id 稳定派生。
 *
 * 是**装饰也是标识**——同一张卡在名录、档案卡、编辑弹窗上看到的必须是同一个号，
 * 所以不能用随机数，也不能各处各写一份实现。
 */
export function dossierNo(id: string): string {
  let h = 0
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) % 10000
  return String(h).padStart(4, '0')
}
