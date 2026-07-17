import { useMessage, useDialog } from 'naive-ui'

/**
 * Bridge between Pinia store error handling and Naive UI message API.
 * The store can't directly call useMessage() (it must be used inside setup),
 * so we expose a singleton ref that components mount the message API onto.
 */
export const messageApi: { instance: ReturnType<typeof useMessage> | null } = {
  instance: null,
}

export const dialogApi: { instance: ReturnType<typeof useDialog> | null } = {
  instance: null,
}

/**
 * Call this in a component that's inside NMessageProvider to wire up the bridge.
 * Safe to call outside a provider context (silently skips).
 */
export function wireNaiveUI(): void {
  try {
    messageApi.instance = useMessage()
    dialogApi.instance = useDialog()
  } catch {
    // Outside NMessageProvider context (e.g. in tests) -- silently skip
  }
}

/**
 * Show an error message from anywhere (stores, API error handlers).
 */
export function notifyError(message: string): void {
  messageApi.instance?.error(message, { duration: 5000 })
}

/**
 * Show a success message from anywhere.
 */
export function notifySuccess(message: string): void {
  messageApi.instance?.success(message, { duration: 3000 })
}

/**
 * Show an info message from anywhere.
 */
export function notifyInfo(message: string): void {
  messageApi.instance?.info(message, { duration: 3000 })
}
