import { getServerUrl } from '@/api/client'

/**
 * 后端给的图片是 `/api/images/xxx.jpg` 这类相对路径。联机客人的后端在房主机器上，
 * 直接用相对路径会去自己本机找——要拼上当前连接的服务器地址。已是绝对地址的原样返回。
 */
export function absoluteImageUrl(src: string): string {
  return /^https?:\/\//i.test(src) ? src : `${getServerUrl()}${src}`
}
