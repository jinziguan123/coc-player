// 本地沙盒调试用（不提交）：沿用正式配置，只把 /api 代理到沙盒后端 8011
import base from './vite.config'
import { mergeConfig } from 'vite'
export default mergeConfig(base, { server: { proxy: { '/api': { target: 'http://127.0.0.1:8011', changeOrigin: true } } } })
