// 第三方规则插件：本机装了哪些、有没有加载上、没加载的原因。
//
// 插件是代码、以本应用的权限运行——这一点必须写在界面上，而不是只写在文档里。
// 插件提供的规则装上后默认关着，到「本桌规矩 → 调整」里打开（那里会标「插件」）。
import { useEffect, useState } from 'react'
import { GiPuzzle } from 'react-icons/gi'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type PluginList = components['schemas']['RulePluginList']

export function RulePluginsCard({ ruleSystem }: { ruleSystem: string }) {
  const [data, setData] = useState<PluginList | null>(null)

  useEffect(() => {
    let alive = true
    api.get<PluginList>(`/rulebooks/plugins/${ruleSystem}`)
      .then((d) => { if (alive) setData(d) })
      .catch(() => { if (alive) setData(null) })
    return () => { alive = false }
  }, [ruleSystem])

  if (!data) return null
  const muted = { color: 'var(--color-text-secondary)' }

  return (
    <div className="card" aria-label="规则插件">
      <h3 className="card-title flex items-center gap-2"><GiPuzzle /> 规则插件</h3>
      {data.plugins.length === 0 ? (
        <p className="text-sm" style={muted}>
          没有安装插件。要用第三方规则，把插件文件放进 <code>{data.directory}</code> 后重启应用；
          写法与示例见项目的 docs/plugins。
        </p>
      ) : (
        <ul className="space-y-2">
          {data.plugins.map((p) => (
            <li key={p.file} className="text-sm rounded-md px-3 py-2"
              style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}>
              <div className="flex items-center justify-between gap-2">
                <span style={{ color: 'var(--color-text-primary)' }}>
                  {p.name || p.file}{p.version && <span style={muted}> v{p.version}</span>}
                </span>
                <span className="chip" style={{
                  fontSize: 'var(--text-2xs)',
                  color: p.status === 'loaded' ? 'var(--color-accent)' : 'var(--color-danger)',
                }}>
                  {p.status === 'loaded' ? '已加载' : '未加载'}
                </span>
              </div>
              {p.description && <div className="text-xs mt-0.5" style={muted}>{p.description}</div>}
              {p.status === 'loaded' ? (
                <div className="text-xs mt-0.5" style={muted}>
                  提供 {p.components.length} 条规则，默认关闭，到「本桌规矩 → 调整」里打开。
                </div>
              ) : (
                <div className="text-xs mt-0.5" style={{ color: 'var(--color-danger)' }}>{p.error}</div>
              )}
              <div className="mt-0.5" style={{ ...muted, fontSize: 'var(--text-2xs)' }}>{p.file}</div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 leading-snug" style={{ ...muted, fontSize: 'var(--text-2xs)' }}>
        插件是代码，以本应用的权限在这台电脑上运行，没有沙箱——只装你信任的插件。
        只是想改参数或武器、职业这些数据的话，用「导出 / 导入房规」就够了，房规文件不含代码。
        插件目录：{data.directory}（应用启动时加载）。
      </p>
    </div>
  )
}
