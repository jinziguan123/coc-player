/** 滚到人物卡的某个区块（或字段，传 block='center'）并闪一下边框，让人知道跳到了哪。 */
export function jumpTo(id: string, block: ScrollLogicalPosition = 'start') {
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ block, behavior: 'smooth' })
  el.classList.add('is-flash')
  setTimeout(() => el.classList.remove('is-flash'), 1200)
}
