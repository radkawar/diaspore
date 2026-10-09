import {highlight} from './common.js';
import {ready, isReady, invoke} from './runtime.js';
import {install, stdlib} from './packages.js';

const input = document.getElementById('repl-input');
const transcript = document.getElementById('transcript');
const terminal = document.getElementById('terminal');
const prompt = document.getElementById('prompt');
const banner = document.getElementById('repl-banner');
const files = {};
const history = [];
let historyIndex = 0, draft = '', block = [], busy = true;
function append(text, className = '') {
  if (!text) return;
  const entry = document.createElement('pre');
  entry.className = className;
  entry.textContent = text;
  transcript.append(entry);
}
function echo(text, continuation) {
  const entry = document.createElement('pre');
  entry.innerHTML = text.split('\n').map((line, i) => `<span class="prompt">${continuation || i ? '...' : '&gt;&gt;&gt;'}</span> ${highlight(line)}`).join('\n') + '\n';
  transcript.append(entry);
}
function resize() { input.style.height = '26px'; input.style.height = `${input.scrollHeight}px`; terminal.scrollTop = terminal.scrollHeight; }
function unlock() {
  busy = false;
  input.disabled = !isReady;
  document.getElementById('reset').disabled = !isReady;
  document.getElementById('clear').disabled = !isReady;
  if (isReady) input.focus();
  resize();
}
async function submit() {
  if (!isReady || busy) return;
  const line = input.value;
  input.value = '';
  echo(line, block.length > 0);
  if (line.trim()) { history.push(line); historyIndex = history.length; }
  if (block.length || /:\s*(?:#.*)?$/.test(line)) {
    if (line.trim() || !block.length) {
      block.push(line);
      prompt.textContent = '...';
      input.value = line.match(/^\s*/)[0] + (/:\s*(?:#.*)?$/.test(line) ? '    ' : '');
      resize();
      return;
    }
  }
  const source = block.length ? block.join('\n') + '\n' : line;
  block = [];
  prompt.textContent = '>>>';
  if (!source.trim()) { resize(); return; }
  busy = true;
  input.disabled = true;
  document.getElementById('reset').disabled = true;
  try {
    const pip = source.match(/^%?pip\s+install\s+(\S+)\s*$/);
    if (pip) {
      append(`Installing ${pip[1]} from PyPI...\n`, 'quiet');
      const pkg = await install(pip[1]);
      Object.assign(files, pkg.files);
      append(`Installed ${pkg.name} ${pkg.version}\n`, 'quiet');
    } else {
      const result = await invoke('repl', source, JSON.stringify(files));
      append(result.stdout);
      append(result.stderr, 'error');
    }
  } catch (error) { append(error.message + '\n', 'error'); }
  finally { unlock(); }
}
input.addEventListener('input', resize);
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(); }
  else if (event.key === 'Tab') { event.preventDefault(); input.setRangeText('    ', input.selectionStart, input.selectionEnd, 'end'); resize(); }
  else if (event.key === 'Escape') { block = []; input.value = ''; prompt.textContent = '>>>'; resize(); }
  else if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && !input.value.includes('\n')) {
    event.preventDefault();
    if (historyIndex === history.length) draft = input.value;
    historyIndex = Math.max(0, Math.min(history.length, historyIndex + (event.key === 'ArrowUp' ? -1 : 1)));
    input.value = historyIndex === history.length ? draft : history[historyIndex];
    input.setSelectionRange(input.value.length, input.value.length);
    resize();
  }
});
document.getElementById('clear').onclick = () => { transcript.replaceChildren(); input.focus(); };
document.getElementById('reset').onclick = async () => {
  if (!isReady || busy) return;
  busy = true;
  input.disabled = true;
  try { await invoke('reset'); block = []; prompt.textContent = '>>>'; input.value = ''; append('Session reset.\n', 'quiet'); }
  catch (error) { append(error.message + '\n', 'error'); }
  finally { unlock(); }
};
terminal.addEventListener('click', () => {
  if (!window.getSelection().toString()) input.focus();
});
window.addEventListener('resize', resize);
try {
  await Promise.all([ready, stdlib().then(shims => Object.assign(files, shims))]);
  banner.textContent = 'Diaspore. Type %pip install NAME to add a package.\n';
  unlock();
} catch (error) { banner.textContent = 'Unable to load Diaspore.\n'; append(error.message + '\n', 'error'); }
