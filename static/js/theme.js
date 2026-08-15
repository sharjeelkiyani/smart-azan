(function(){
  const KEY = 'smartazan-theme'; // stores 'light' | 'dark' | 'auto'
  let currentMode = 'auto';

  // Night = from today's Maghrib until the next Fajr (the Islamic day/night
  // boundary), using only the time-of-day so it stays correct even if the
  // page is left open for days without reloading. Falls back to the
  // system's light/dark preference when there's no timetable data yet.
  function isNight() {
    const cfg = window.SMART_AZAN_NIGHT || {};
    if (!cfg.maghribIso || !cfg.fajrIso) {
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    const maghribTime = new Date(cfg.maghribIso).toTimeString().slice(0, 5);
    const fajrTime = new Date(cfg.fajrIso).toTimeString().slice(0, 5);
    const nowTime = new Date().toTimeString().slice(0, 5);
    if (maghribTime <= fajrTime) return nowTime >= maghribTime && nowTime < fajrTime;
    return nowTime >= maghribTime || nowTime < fajrTime;
  }

  function effectiveTheme(mode) {
    return mode === 'auto' ? (isNight() ? 'dark' : 'light') : mode;
  }

  function applyVisual(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    if (theme === 'dark') {
      let el = document.getElementById('dark-vars');
      if (!el) {
        el = document.createElement('style'); el.id = 'dark-vars';
        el.textContent = `
        :root[data-theme="dark"]{
          --bg:#07130f; --card:#0f231b; --text:#eef7f1; --muted:#9db8a9;
          --primary:#2fbf82; --primary-dark:#0b6e4f; --gold:#e0bf5a;
          --success:#34d399; --danger:#f87171; --border:#1c3a2c;
          --shadow:0 6px 20px rgba(0,0,0,.5);
        }`;
        document.head.appendChild(el);
      }
    }
  }

  function updateControls() {
    document.querySelectorAll('#theme-modes-sidebar button').forEach(b => {
      b.classList.toggle('active', b.dataset.mode === currentMode);
    });
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      const labels = { light: '☀️ Light', dark: '🌙 Dark', auto: '🌓 Auto' };
      btn.textContent = labels[currentMode] || labels.auto;
    }
  }

  function setMode(mode) {
    currentMode = mode;
    try { localStorage.setItem(KEY, mode); } catch (e) {}
    applyVisual(effectiveTheme(mode));
    updateControls();
  }

  function init() {
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) {}
    setMode(saved || 'auto');

    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', () => {
        const order = ['light', 'dark', 'auto'];
        setMode(order[(order.indexOf(currentMode) + 1) % order.length]);
      });
    }

    document.querySelectorAll('#theme-modes-sidebar button').forEach(b => {
      b.addEventListener('click', () => setMode(b.dataset.mode));
    });

    // Re-evaluate periodically so 'auto' mode flips automatically at
    // Maghrib/Fajr if the page is left open across the transition.
    setInterval(() => { if (currentMode === 'auto') applyVisual(effectiveTheme('auto')); }, 60000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
