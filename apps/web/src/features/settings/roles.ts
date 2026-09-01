/**
 * 三个岗位是什么、各自管什么。
 *
 * 单独成文件是因为 fast refresh：组件文件里混着导出常量，热更新就退化成整页刷新。
 *
 * 名字按**它管什么**起，不按代码里怎么叫——背景见 ModelRoles.tsx。
 */
export interface RoleSpec {
  key: 'narrator' | 'aide' | 'reader'
  /** 岗位名。 */
  label: string
  /** 一句话说清：设了它，游戏里什么会变。 */
  duty: string
  /** 空着的时候会怎样——这比「未设置」有用得多。 */
  vacant: string
}

export interface RoleHolder {
  id: string
  name: string
  model_name: string
}

export const ROLES: RoleSpec[] = [
  {
    key: 'narrator',
    label: '叙事',
    duty: '主持人的叙述、NPC 的对话，以及骰子和规则的判定',
    vacant: '必须指定一个，否则开不了团',
  },
  {
    key: 'aide',
    label: '副手',
    duty: '规划下一步、AI 队友的行动、滚动摘要这些幕后活',
    vacant: '不指定就跟着叙事模型走',
  },
  {
    key: 'reader',
    label: '读图',
    duty: '解析扫描件和图文模组',
    vacant: '不指定就没法导入图片模组',
  },
]
