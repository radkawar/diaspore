const root = document.documentElement;
const preference = matchMedia('(prefers-color-scheme: dark)');
function currentTheme() {
  return root.dataset.theme || (preference.matches ? 'dark' : 'light');
}
function labelTheme() {
  const theme = currentTheme();
  const toggle = document.querySelector('.theme');
  if (toggle) {
    toggle.innerHTML = theme === 'dark'
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 14A8.5 8.5 0 0 1 10 3.5 8.5 8.5 0 1 0 20.5 14Z"/></svg>';
    toggle.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
    toggle.title = toggle.getAttribute('aria-label');
  }
}
labelTheme();
preference.addEventListener('change', labelTheme);
document.querySelector('.theme')?.addEventListener('click', () => {
  const theme = currentTheme() === 'dark' ? 'light' : 'dark';
  root.dataset.theme = theme;
  try { localStorage.setItem('diaspore-theme', theme); } catch {}
  labelTheme();
});

export function escapeHTML(text) {
  return String(text).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
}

export function highlight(source) {
  const keywords = new Set('and as assert async await break case class continue def del elif else except False finally for from global if import in is lambda match None nonlocal not or pass raise return True try while with yield'.split(' '));
  const builtins = new Set('abs all any bool dict enumerate float int len list map max min next print range repr reversed set sorted str sum tuple type zip'.split(' '));
  const tokens = /#[^\n]*|(?:[rRuUbBfF]{0,2})(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*(?:"|$)|'(?:\\.|[^'\\\n])*(?:'|$))|\b(?:0[xX][\da-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)\b|\b[A-Za-z_]\w*\b/g;
  let html = '', end = 0;
  for (const match of source.matchAll(tokens)) {
    html += escapeHTML(source.slice(end, match.index));
    const word = match[0];
    const kind = word.startsWith('#') ? 'comment' : /^[rRuUbBfF]{0,2}["']/.test(word) ? 'string' : /^\d/.test(word) ? 'number' : keywords.has(word) ? 'keyword' : builtins.has(word) ? 'builtin' : '';
    html += kind ? `<span class="tok-${kind}">${escapeHTML(word)}</span>` : escapeHTML(word);
    end = match.index + word.length;
  }
  return html + escapeHTML(source.slice(end));
}

document.querySelectorAll('[data-python]').forEach(el => { el.innerHTML = highlight(el.textContent); });

document.querySelectorAll('[popover]').forEach(popover => {
  const anchor = document.querySelector(`[popovertarget="${popover.id}"]`);
  if (!anchor) return;
  function position() {
    if (!popover.matches(':popover-open')) return;
    const bounds = anchor.getBoundingClientRect();
    popover.style.left = `${Math.max(18, Math.min(innerWidth - popover.offsetWidth - 18, bounds.right - popover.offsetWidth))}px`;
    popover.style.top = `${bounds.bottom + 8}px`;
  }
  popover.addEventListener('toggle', event => {
    if (event.newState === 'open') {
      position();
      popover.querySelector('input')?.focus();
    }
  });
  window.addEventListener('resize', position);
});
