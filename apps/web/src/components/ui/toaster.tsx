import { Toaster as Sonner } from 'sonner'

export function Toaster() {
  return (
    <Sonner
      position="top-right"
      // 提示浮在右上角，正好压着「新增配置」「新一局」这类按钮。等它自己消失才能点，
      // 是把用户晾在那儿——给个叉，想关就关。
      closeButton
      toastOptions={{
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
