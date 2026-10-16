/** 滚到人物卡的某个区块并闪一下边框，让人知道跳到了哪。 */
export function jumpTo(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ block: 'start', behavior: 'smooth' })
  el.classList.add('is-flash')
  setTimeout(() => el.classList.remove('is-flash'), 1200)
}
