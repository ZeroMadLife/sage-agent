import { nextTick } from 'vue'

type ViewTransitionHandle = {
  updateCallbackDone: Promise<void>
  finished: Promise<void>
}

type TransitionDocument = Document & {
  startViewTransition?: (update: () => Promise<void>) => ViewTransitionHandle
}

function prefersReducedMotion() {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export async function runViewTransition(
  update: () => Promise<void> | void,
  name = 'page',
) {
  const transitionDocument = document as TransitionDocument
  const start = transitionDocument.startViewTransition?.bind(transitionDocument)
  if (!start || prefersReducedMotion()) {
    await update()
    await nextTick()
    return
  }

  document.documentElement.dataset.viewTransition = name
  try {
    const transition = start(async () => {
      await update()
      await nextTick()
    })
    await transition.updateCallbackDone
    await transition.finished
  } finally {
    delete document.documentElement.dataset.viewTransition
  }
}
