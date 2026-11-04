import { useState, type ReactNode } from 'react'
import { m } from 'motion/react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { spring } from '@/lib/motion'

export interface SettingsSection {
  key: string
  label: string
  content: ReactNode
}

/**
 * 设置页的二级页签：左栏是一级分类，这里是分类下的各个模块，一次只看一个。
 *
 * 此前每个分类把所有模块竖着摊成一长页（检索那页四张卡上下排），彼此不相干的设置
 * 揉在一起，找一项要上下翻。改成页签后结构与「AI 配置」那页一致：标题下一排页签钉在顶上，
 * 往下翻也知道自己在哪。
 *
 * 初始页签可由地址栏 `?sec=` 指定（如从别处直接跳到「联机 → 内置直连」）。
 */
export function SettingsSections({ title, intro, sections, actions }: {
  title: string
  /** 标题下的一句总述（对整个分类都成立的话，别塞进某一个页签里） */
  intro?: ReactNode
  sections: SettingsSection[]
  /** 页签行右侧的操作（跟着页签走、不随内容滚走的那层） */
  actions?: ReactNode
}) {
  const [sec, setSec] = useState(() => {
    // 直接读地址栏而不走路由钩子：面板单独渲染（测试、弹窗）时也不必套一层 Router
    const wanted = new URLSearchParams(window.location.search).get('sec')
    return sections.find((s) => s.key === wanted)?.key ?? sections[0]?.key ?? ''
  })

  return (
    <div>
      <h2 className="page-title">{title}</h2>
      {intro && <p className="settings-intro">{intro}</p>}
      <Tabs value={sec} onValueChange={setSec}>
        <div className="settings-tabs flex items-center justify-between gap-4">
          <TabsList>
            {sections.map((s) => (
              <TabsTrigger key={s.key} value={s.key} className="!flex-none !text-[length:var(--text-sm)] px-5">
                {s.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {actions}
        </div>
        {sections.map((s) => (
          <TabsContent key={s.key} value={s.key} className="!p-0 !overflow-visible">
            {/* 切页时新页从下方轻轻浮上来，和全站的入场语言一致 */}
            <m.div className="settings-section" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring.soft}>
              {s.content}
            </m.div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
