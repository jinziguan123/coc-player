// 「要不要去某处」建议卡的已处理记录（本机、按会话）。
//
// 为什么放 localStorage 而不是落库：**这是一条彻头彻尾的个人决定**——同一张卡在多人同桌
// 时全桌可见，甲拒绝不该让乙的卡也消失。落库就得为「每个玩家对每张卡的态度」建一张表，
// 而这件事的代价只是「换台设备时卡片会再出现一次」，不值当。
//
// 「同意」也记进来：同意之后前往已经进了暂存动作，卡片再挂着可点就会让人重复加。
const KEY_PREFIX = 'trpg_travel_suggest_done:'

function read(sessionId: string): Set<string> {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + sessionId)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()   // 隐私模式等读不到：卡片照常显示，只是刷新后会再出现一次
  }
}

export function loadHandledSuggestions(sessionId: string): Set<string> {
  return read(sessionId)
}

export function markSuggestionHandled(sessionId: string, eventId: string): Set<string> {
  const next = read(sessionId)
  next.add(eventId)
  try {
    localStorage.setItem(KEY_PREFIX + sessionId, JSON.stringify([...next]))
  } catch { /* 存不下就只在本次会话生效 */ }
  return next
}
