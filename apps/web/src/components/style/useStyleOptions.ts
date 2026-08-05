// 文风 / 画风的预设清单（后端 /sessions/style-options）。
//
// 单独一个文件是为了让 StylePicker.tsx 只导出组件（否则 fast refresh 失效）。
import { useEffect, useState } from 'react'
import { api } from '@/api/client'

export interface StyleOption { id: string; label: string; hint: string }
export interface StyleOptions { narrative: StyleOption[]; image: StyleOption[] }

let cached: StyleOptions | null = null

/** 预设清单只取一次：整个应用共用，切页面/开关弹窗都不该再打一次请求。 */
export function useStyleOptions(): StyleOptions | null {
  const [options, setOptions] = useState<StyleOptions | null>(cached)
  useEffect(() => {
    if (cached) return
    let alive = true
    api.get<StyleOptions>('/sessions/style-options')
      .then((res) => {
        cached = res
        if (alive) setOptions(res)
      })
      .catch(() => { /* 取不到就只留「跟随默认 / 自定义」两档，不弹错打断编辑 */ })
    return () => { alive = false }
  }, [])
  return options
}
