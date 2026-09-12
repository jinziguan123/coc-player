/**
 * KP 表单里那几个下拉的候选项。
 *
 * 单独成文件是为了 fast refresh：组件文件里混着导出函数，热更新会退化成整页刷新。
 */
import type { ComboboxOption } from '@/components/ui/combobox'

/** 只认 catalogs 的形状，不去导 HumanKpPanel 的 Workspace——那是页面自己的类型，
 *  为了三份候选把它拽过来反而绑死了两边。 */
interface CatalogLike {
  catalogs: {
    characters: { id: string; name: string }[]
    scenes: { id: string; name: string }[]
    npcs: { id: string; name: string }[]
    handouts: { id: string; name: string }[]
  }
}

/** 四份候选：与从前那几个 datalist 取值一致——场景与手书填 id、旁注名字；
 *  NPC 填名字、旁注 id（KP 写台词时打的是名字）。
 *  调查员填 id、旁注名字：`move_actor` 要的是角色 id，而重名并非不可能。 */
export function catalogOptions(workspace: CatalogLike | null) {
  return {
    characters: (workspace?.catalogs.characters || []).map((i) => ({ value: i.id, hint: i.name })),
    scenes: (workspace?.catalogs.scenes || []).map((i) => ({ value: i.id, hint: i.name })),
    npcs: (workspace?.catalogs.npcs || []).map((i) => ({ value: i.name, hint: i.id })),
    handouts: (workspace?.catalogs.handouts || []).map((i) => ({ value: i.id, hint: i.name })),
  } satisfies Record<string, ComboboxOption[]>
}
