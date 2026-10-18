import { Toaster as Sonner } from 'sonner'

export function Toaster() {
  return (
    <Sonner
      position="top-right"
      // 手机端：顶部贴安全区，左右各留 12px。toast 固定在顶部而不是底部，
      // 就不会压到底部六项导航（各页内容也给导航留过高度）。
      mobileOffset={{ top: 'calc(env(safe-area-inset-top, 0px) + 12px)', left: 12, right: 12 }}
      // 提示浮在右上角，正好压着「新增配置」「新一局」这类按钮。等它自己消失才能点，
      // 是把用户晾在那儿——给个叉，想关就关。
      closeButton
      toastOptions={{
        className: 'app-toast',
        style: {
          background: 'var(--color-bg-card)',
          border: '1px solid var(--color-border)',
          color: 'var(--color-text-primary)',
          fontFamily: 'var(--font-ui)',
          fontSize: '0.875rem',
          boxShadow: '0 4px 16px rgba(0, 0, 0, 0.5)',
        },
      }}
    />
  )
}
