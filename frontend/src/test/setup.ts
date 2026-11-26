import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom implements neither of these, and both are touched by the chart and map
// renderers. Stubbing them keeps component tests focused on our own behaviour.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// jsdom has no object-URL implementation, and the photo uploader creates one
// preview URL per selected file. A stable stub keeps the assertions about the
// uploader's own behaviour rather than about the browser API.
if (!('createObjectURL' in URL)) {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: (file: File) => `blob:mock/${file.name}`,
  })
}
if (!('revokeObjectURL' in URL)) {
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: () => {} })
}

// `sonner` mounts a portal outside the container; unmounting every component
// after each test keeps its toasts from leaking into the next assertion.
afterEach(() => {
  cleanup()
  // A test that mounts the app must not leak a session into the next one.
  window.localStorage.clear()
  window.sessionStorage.clear()
})

vi.stubGlobal('scrollTo', () => {})
