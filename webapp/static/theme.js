(() => {
  const root = document.documentElement;
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  let savedTheme;
  try { savedTheme = localStorage.getItem('data-analyzer-theme'); } catch {}
  let followsSystem = !['light', 'dark'].includes(savedTheme);

  function applyTheme(theme) {
    root.dataset.theme = theme;
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
      const label = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
      toggle.setAttribute('aria-label', label);
      toggle.title = label;
    }
  }

  applyTheme(followsSystem ? (preference.matches ? 'dark' : 'light') : savedTheme);
  preference.addEventListener('change', event => {
    if (followsSystem) applyTheme(event.matches ? 'dark' : 'light');
  });
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(root.dataset.theme);
    document.getElementById('theme-toggle')?.addEventListener('click', () => {
      const theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      followsSystem = false;
      applyTheme(theme);
      try { localStorage.setItem('data-analyzer-theme', theme); } catch {}
    });
  });
})();
