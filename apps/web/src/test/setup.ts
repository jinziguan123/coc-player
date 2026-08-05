import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => cleanup())

// jsdom 没实现 Pointer Capture 与 scrollIntoView，而 Radix 的 Select/Popover 在打开菜单时
// 会调它们——不补就是一句 `target.hasPointerCapture is not a function`，
// 表现成「下拉点不开、找不到选项」，很容易被误判成组件本身写错了。
for (const name of ['hasPointerCapture', 'setPointerCapture', 'releasePointerCapture'] as const) {
  if (!(name in window.HTMLElement.prototype)) {
    Object.defineProperty(window.HTMLElement.prototype, name, {
      value: name === 'hasPointerCapture' ? () => false : () => {},
      writable: true,
    })
  }
}
if (!window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = () => {}
}
