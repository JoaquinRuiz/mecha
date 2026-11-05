// Runs before first paint (loaded synchronously from <head>). No inline script: strict CSP.
;(() => {
  const root = document.documentElement
  let theme = 'dark'
  try {
    const stored = window.localStorage.getItem('mecha.theme')
    if (stored === 'light' || stored === 'dark') theme = stored
  } catch {
    // storage blocked: keep the default
  }
  root.classList.toggle('dark', theme === 'dark')
  root.classList.toggle('light', theme === 'light')
  root.style.colorScheme = theme
  const lang = (navigator.languages?.[0] || navigator.language || 'en').toLowerCase()
  root.lang = lang.startsWith('es') ? 'es' : 'en'
})()
