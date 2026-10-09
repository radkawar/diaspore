import {highlight, escapeHTML} from './common.js';
import {ready, invoke} from './runtime.js';
import {shareLink, restoreLink} from './share.js';
import {stdlib, install} from './packages.js';

const $ = id => document.getElementById(id);
const source = $('source'), paint = $('highlight'), marker = document.querySelector('.source-line');
const examples = {
  recursion: `def factorial(n):
    if n <= 1:
        return 1
    smaller = factorial(n - 1)
    result = n * smaller
    print(n, "->", result)
    return result

answer = factorial(5)
print("5! =", answer)
`,
  generator: `def triangular(count):
    total = 0
    for number in range(1, count + 1):
        total += number
        yield total

shapes = triangular(5)
for dots in shapes:
    print("*" * dots)
print("Done")
`,
  class: `class Lantern:
    def __init__(self, name):
        self.name = name
        self.brightness = 0

    def turn_up(self, amount):
        self.brightness += amount
        print(self.name, "*" * self.brightness)

lamp = Lantern("Moon")
for amount in [1, 2, 3]:
    lamp.turn_up(amount)
print("Brightness:", lamp.brightness)
`,
};
let events = [], cursor = 0, busy = true, mainModule = '', result = null;
let fullOutput = '', fullError = '', packages = [];
const files = {};
function syncScroll() {
  paint.scrollTop = source.scrollTop;
  paint.scrollLeft = source.scrollLeft;
  document.querySelector('.gutter').style.transform = `translateY(${-source.scrollTop}px)`;
  const event = events[cursor];
  marker.hidden = !event?.line || event.module !== mainModule || event.kind === 'end';
  if (!marker.hidden) marker.style.top = `${20 + (event.line - 1) * 22 - source.scrollTop}px`;
}
function repaint() {
  paint.innerHTML = highlight(source.value) + '\n';
  document.querySelector('.gutter').innerHTML = source.value.split('\n').map((_, i) => i + 1).join('<br>');
  syncScroll();
}
function controls() {
  $('debug').disabled = busy;
  $('example').disabled = busy;
  source.readOnly = busy;
  $('back').disabled = busy || !events.length || cursor === 0;
  for (const id of ['step', 'over', 'continue']) $(id).disabled = busy || !events.length || cursor === events.length - 1;
  $('out').disabled = busy || !events.length || cursor === events.length - 1 || events[cursor].depth === 0;
  $('scrubber').disabled = busy || !events.length;
}
function clearTrace() {
  events = [];
  result = null;
  cursor = 0;
  $('stack').replaceChildren();
  $('locals').replaceChildren();
  $('output').textContent = '';
  $('metrics').textContent = '';
  $('trace-state').textContent = 'F5 to debug';
  $('trace-state').classList.remove('error');
  $('step-count').textContent = '';
  $('source-position').textContent = 'Python';
  $('scrubber').max = '0';
  $('scrubber').value = '0';
  syncScroll();
  controls();
}
function seek(index) {
  if (!events.length) return;
  cursor = Math.max(0, Math.min(events.length - 1, index));
  const event = events[cursor];
  $('scrubber').value = cursor;
  $('step-count').textContent = `${cursor + 1} / ${events.length}`;
  $('scrubber').setAttribute('aria-valuetext', `Event ${cursor + 1} of ${events.length}, ${event.kind}, line ${event.line}`);
  const label = {statement: `Line ${event.line}`, call: `Call ${event.function}`, return: 'Return', yield: 'Yield', exception: 'Exception'}[event.kind];
  $('trace-state').textContent = event.kind === 'end'
    ? `Finished / exit ${result.code}${result.truncated ? ' / trace limited to 20,000 events' : ''}`
    : `${label}${event.value ? ` / ${event.value}` : ''}${result.truncated ? ' / trace truncated' : ''}`;
  $('trace-state').classList.toggle('error', event.kind === 'exception' || (event.kind === 'end' && result.code !== 0));
  $('source-position').textContent = event.line ? `${event.module}:${event.line}` : 'Python';
  $('stack').innerHTML = [...(event.stack || [])].reverse().map(frame => `<li>${escapeHTML(frame.function || '<module>')}<small>${escapeHTML(frame.module)}${frame.line ? `:${frame.line}` : ''}</small></li>`).join('');
  $('locals').innerHTML = Object.entries(event.locals || {}).map(([name, value]) => `<dt>${escapeHTML(name)}</dt><dd>${escapeHTML(value)}</dd>`).join('');
  if (event.localsTruncated) $('locals').insertAdjacentHTML('beforeend', '<dd class="quiet">32 locals shown</dd>');
  $('output').replaceChildren(document.createTextNode(fullOutput.slice(0, event.outEnd)));
  if (event.errEnd) {
    const error = document.createElement('span');
    error.className = 'error';
    error.textContent = fullError.slice(0, event.errEnd);
    $('output').append(error);
  }
  $('output').scrollTop = $('output').scrollHeight;
  if (event.line && event.module === mainModule) {
    const top = 20 + (event.line - 1) * 22;
    if (top < source.scrollTop + 20 || top > source.scrollTop + source.clientHeight - 44) source.scrollTop = Math.max(0, top - source.clientHeight / 3);
  }
  syncScroll();
  controls();
}
function advance(mode) {
  if (busy || !events.length) return;
  if (mode === 'back') return seek(cursor - 1);
  if (mode === 'continue') return seek(events.length - 1);
  if (mode === 'step') return seek(cursor + 1);
  const depth = events[cursor].depth;
  for (let i = cursor + 1; i < events.length; i++) {
    const next = events[i];
    if (next.kind === 'end' || (next.kind === 'statement' && (mode === 'out' ? next.depth < depth : next.depth <= depth))) return seek(i);
  }
  seek(events.length - 1);
}
async function debug() {
  if (busy) return;
  clearTrace();
  busy = true;
  controls();
  $('trace-state').textContent = 'Running...';
  $('debug').textContent = 'Running...';
  try {
    const compiled = await invoke('compile', source.value, JSON.stringify(files));
    if (compiled.code !== 0) {
      $('output').textContent = compiled.stderr;
      $('trace-state').textContent = 'Compile error';
      return;
    }
    mainModule = JSON.parse(compiled.bundle).main;
    result = await invoke('trace', compiled.bundle, '{}');
    events = result.events || [];
    fullOutput = '';
    fullError = '';
    for (const event of events) {
      fullOutput += event.stdout || '';
      fullError += event.stderr || '';
      event.outEnd = fullOutput.length;
      event.errEnd = fullError.length;
    }
    fullOutput = result.stdout || '';
    fullError = result.stderr || '';
    const last = (!result.truncated && events.at(-1)) || {locals: {}, stack: [], depth: 0};
    events.push({...last, kind: 'end', line: 0, value: '', outEnd: fullOutput.length, errEnd: fullError.length});
    $('scrubber').max = events.length - 1;
    $('metrics').textContent = `${(result.steps || 0).toLocaleString()} VM steps`;
    seek(events.findIndex(event => event.kind === 'statement'));
  } catch (error) {
    $('trace-state').textContent = 'Run failed';
    $('output').textContent = error.message;
  } finally {
    busy = false;
    $('debug').textContent = 'Debug';
    controls();
  }
}
source.addEventListener('input', () => { clearTrace(); repaint(); });
source.addEventListener('scroll', syncScroll);
source.addEventListener('keydown', event => {
  if (event.key === 'Tab' && !source.readOnly) {
    event.preventDefault();
    source.setRangeText('    ', source.selectionStart, source.selectionEnd, 'end');
    clearTrace();
    repaint();
  }
});
$('debug').onclick = debug;
for (const id of ['back', 'step', 'over', 'out', 'continue']) $(id).onclick = () => advance(id);
$('scrubber').oninput = () => seek(Number($('scrubber').value));
$('example').onchange = () => {
  source.value = examples[$('example').value];
  clearTrace();
  repaint();
};
document.addEventListener('keydown', event => {
  if (event.key === 'F5' || ((event.ctrlKey || event.metaKey) && event.key === 'Enter')) { event.preventDefault(); debug(); return; }
  const action = event.key === 'F10' ? (event.shiftKey ? 'back' : 'over') : event.key === 'F11' ? (event.shiftKey ? 'out' : 'step') : event.key === 'F8' ? 'continue' : null;
  if (action) { event.preventDefault(); if (!$(action).disabled) advance(action); }
});
let toastTimer;
function toast(text) {
  clearTimeout(toastTimer);
  $('toast').textContent = text;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
}
$('share').onclick = async () => {
  try {
    const link = await shareLink(source.value, packages);
    history.replaceState(null, '', link);
    await navigator.clipboard.writeText(link);
    toast('Link copied');
  } catch (error) { toast(`Could not copy link: ${error.message}`); }
};
source.value = examples.recursion;
repaint();
try {
  await Promise.all([ready, stdlib().then(shims => Object.assign(files, shims))]);
  const shared = await restoreLink();
  if (shared) {
    source.value = shared.source;
    packages = shared.packages;
    for (const spec of packages) {
      const [name, version] = spec.split('==');
      const pkg = await install(name, version);
      Object.assign(files, pkg.files);
    }
    repaint();
  }
  busy = false;
  controls();
} catch (error) {
  $('trace-state').textContent = 'Unavailable';
  $('output').textContent = error.message;
}
