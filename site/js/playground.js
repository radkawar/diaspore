import {highlight, escapeHTML} from './common.js';
import {ready, isReady, invoke, compile, packageVersion} from './runtime.js';
import {install, curated} from './packages.js';
import {shareLink, restoreLink} from './share.js';

const $ = id => document.getElementById(id);
const source = $('source'), paint = $('highlight'), output = $('output');
const files = {};
const installed = new Map();
let busy = true, bundle = null;
let cleanJSON = '';
function updateJSONAction() {
  $('run-json').hidden = $('json-view').hidden || !bundle || $('json').value === cleanJSON;
}
$('json').addEventListener('input', updateJSONAction);
function repaint() {
  paint.innerHTML = highlight(source.value) + '\n';
  document.querySelector('.gutter').textContent = '';
  document.querySelector('.gutter').innerHTML = source.value.split('\n').map((_, i) => i + 1).join('<br>');
  syncScroll();
}
function syncScroll() {
  paint.scrollTop = source.scrollTop;
  paint.scrollLeft = source.scrollLeft;
  document.querySelector('.gutter').style.transform = `translateY(${-source.scrollTop}px)`;
  document.querySelector('.source-line').hidden = true;
}
source.addEventListener('input', repaint);
source.addEventListener('scroll', syncScroll);
source.addEventListener('keydown', event => {
  if (event.key === 'Tab') {
    event.preventDefault();
    source.setRangeText('    ', source.selectionStart, source.selectionEnd, 'end');
    repaint();
  }
});
function selectLine(line, focus = false) {
  const marker = document.querySelector('.source-line');
  const top = (line - 1) * 22;
  if (focus) {
    source.scrollTop = Math.max(0, top - source.clientHeight / 3);
    const start = source.value.split('\n').slice(0, line - 1).reduce((n, text) => n + text.length + 1, 0);
    source.focus();
    source.setSelectionRange(start, start + (source.value.split('\n')[line - 1]?.length || 0));
    syncScroll();
  }
  marker.style.top = `${20 + top - source.scrollTop}px`;
  marker.hidden = false;
}
function nodeName(node) {
  if (!node || typeof node !== 'object') return JSON.stringify(node);
  if (node.op === 'name') return node.id;
  if (node.op === 'attr') return `${nodeName(node.o)}.${node.n}`;
  if (node.op === 'const') return node.t === 'str' ? JSON.stringify(node.v) : String(node.v ?? node.t);
  return node.op || '';
}
function treeNode(label, value, depth = 0, linkSource = false) {
  if (!value || typeof value !== 'object') {
    const leaf = document.createElement('div');
    leaf.className = 'leaf';
    leaf.innerHTML = `<span class="node-label">${escapeHTML(label)}</span>: <span class="tok-string">${escapeHTML(JSON.stringify(value))}</span>`;
    return leaf;
  }
  const details = document.createElement('details');
  details.open = depth < 3;
  const summary = document.createElement('summary');
  let description = Array.isArray(value) ? `[${value.length}]` : '';
  if (value.op) {
    description = value.op;
    if (value.kind) description += ` ${value.kind}`;
    else if (value.op === 'call') description += ` ${nodeName(value.f)}(...)`;
    else if (value.name || value.id) description += ` ${value.name || value.id}`;
    else if (value.op === 'const') description += ` ${nodeName(value)}`;
  }
  summary.innerHTML = `<span class="node-label">${escapeHTML(label)}</span> <span class="node-op">${escapeHTML(description)}</span>`;
  if (value.ln && linkSource) {
    const link = document.createElement('button');
    link.className = 'line-link';
    link.textContent = `:${value.ln}`;
    link.title = `Go to source line ${value.ln}`;
    link.addEventListener('mouseenter', () => selectLine(value.ln));
    link.addEventListener('mouseleave', () => { document.querySelector('.source-line').hidden = true; });
    link.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); selectLine(value.ln, true); });
    summary.append(link);
  }
  details.append(summary);
  let populated = false;
  function populate() {
    if (!details.open || populated) return;
    populated = true;
    for (const [key, child] of Object.entries(value)) {
      if (['op', 'ln', 'src'].includes(key) || (value.op && ['kind', 'name', 'id'].includes(key))) continue;
      details.append(treeNode(key, child, depth + 1, linkSource));
    }
  }
  details.addEventListener('toggle', populate);
  populate();
  return details;
}
function showBundle(text) {
  bundle = JSON.parse(text);
  $('json').value = JSON.stringify(bundle, null, 2);
  cleanJSON = $('json').value;
  updateJSONAction();
  $('tree').replaceChildren();
  for (const [name, mod] of Object.entries(bundle.modules || {})) {
    const node = treeNode(name, mod.body || mod, 0, name === bundle.main);
    $('tree').append(node);
  }
  $('copy-json').disabled = false;
  $('download-json').disabled = false;
  $('bundle-mode').textContent = bundle.stdlib ? 'Linked' : 'Standalone';
  $('bundle-mode').title = bundle.stdlib || 'No runtime stdlib reference';
  $('run-json').disabled = busy;
}
function setBusy(value) {
  busy = value;
  for (const id of ['run', 'install', 'run-json']) $(id).disabled = !isReady || value || (id === 'run-json' && !bundle);
  $('example').disabled = value;
  $('self-contained').disabled = value;
}
function renderResult(result, elapsed) {
  output.replaceChildren();
  if (result.stdout) output.append(document.createTextNode(result.stdout));
  if (result.stderr) { const error = document.createElement('span'); error.className = 'error'; error.textContent = result.stderr; output.append(error); }
  if (!result.stdout && !result.stderr) { const empty = document.createElement('span'); empty.className = 'quiet'; empty.textContent = 'No output.'; output.append(empty); }
  $('metrics').textContent = `exit ${result.code} / ${(result.steps || 0).toLocaleString()} steps / ${Math.round(elapsed)} ms`;
}
async function execute(mode) {
  if (!isReady || busy) return;
  setBusy(true);
  $('run').textContent = 'Running...';
  $('run').setAttribute('aria-busy', 'true');
  const start = performance.now();
  try {
    if (mode === 'json') {
      const text = JSON.stringify(JSON.parse($('json').value));
      showBundle(text);
      renderResult(await invoke('run', text, '{}'), performance.now() - start);
    } else {
      const compiled = await compile(source.value, files, {self_contained: $('self-contained').checked});
      if (compiled.code !== 0) { renderResult(compiled, performance.now() - start); return; }
      showBundle(compiled.bundle);
      renderResult(await invoke('run', compiled.bundle, '{}'), performance.now() - start);
    }
  } catch (error) { output.textContent = error.message; }
  finally {
    $('run').textContent = 'Run';
    $('run').removeAttribute('aria-busy');
    setBusy(false);
  }
}
$('run').onclick = () => execute('run');
$('run-json').onclick = () => execute('json');
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); execute('run'); }
});
for (const tab of ['tree', 'json']) $(tab + '-tab').onclick = () => {
  $('tree').hidden = tab !== 'tree';
  $('json-view').hidden = tab !== 'json';
  $('tree-tab').setAttribute('aria-pressed', String(tab === 'tree'));
  $('json-tab').setAttribute('aria-pressed', String(tab === 'json'));
  updateJSONAction();
};
$('copy-json').onclick = async () => {
  try { await navigator.clipboard.writeText($('json').value); toast('JSON copied'); }
  catch { $('json-tab').click(); $('json').select(); toast('Select the JSON and copy it.'); }
};
$('download-json').onclick = () => {
  try {
    const text = JSON.stringify(JSON.parse($('json').value), null, 2) + '\n';
    const url = URL.createObjectURL(new Blob([text], {type: 'application/json'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'main.dsp.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch (error) { toast(`Cannot download bundle: ${error.message}`); }
};
async function installPackage(spec) {
  const [name, version] = spec.split('==');
  if (installed.has(name) && (!version || installed.get(name) === version)) return;
  $('package-status').textContent = `Installing ${spec}...`;
  const pkg = await install(name, version);
  Object.assign(files, pkg.files);
  installed.set(pkg.name, pkg.version);
  $('package-status').textContent = '';
  $('installed-packages').replaceChildren();
  for (const [name, version] of installed) {
    const chip = document.createElement('span');
    chip.className = 'package-chip';
    chip.textContent = `${name} ${version}`;
    $('installed-packages').append(chip);
  }
  $('packages-button').title = `${installed.size} package${installed.size === 1 ? '' : 's'} installed`;
}
const packageExamples = curated.filter(pkg => !['art', 'snowballstemmer'].includes(pkg.name));
packageExamples.push({
  name: 'diaspore', version: packageVersion, example: 'durable', title: 'Durable workflow',
});
const group = document.createElement('optgroup');
group.label = 'With packages';
for (const pkg of packageExamples) {
  const option = document.createElement('option');
  option.value = pkg.example || `package:${pkg.name}`;
  option.textContent = pkg.title || pkg.name;
  group.append(option);
}
$('example').append(group);
async function loadExample() {
  const pkg = packageExamples.find(pkg => (pkg.example || `package:${pkg.name}`) === $('example').value);
  if (pkg?.demo) {
    source.value = pkg.demo.replaceAll('; ', '\n') + '\n';
  } else {
    const response = await fetch(`examples/${pkg?.example || $('example').value}.py`);
    if (!response.ok) throw new Error('Could not load example');
    source.value = await response.text();
  }
  repaint();
  if (pkg) await installPackage(`${pkg.name}==${pkg.version}`);
}
$('example').onchange = async () => {
  setBusy(true);
  try { await loadExample(); }
  catch (error) { output.textContent = error.message; }
  finally { setBusy(false); }
};
for (const item of curated) { const option = document.createElement('option'); option.value = item.name; $('package-list').append(option); }
$('package-form').onsubmit = async event => {
  event.preventDefault();
  const name = $('package-name').value.trim();
  if (!name || busy) return;
  setBusy(true);
  try { await installPackage(name); $('package-name').value = ''; }
  catch (error) { $('package-status').textContent = error.message; }
  finally { setBusy(false); }
};
let toastTimer;
function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
}
$('share').onclick = async () => {
  try {
    const link = await shareLink(source.value, [...installed].map(([name, version]) => `${name}==${version}`));
    history.replaceState(null, '', link);
    await navigator.clipboard.writeText(link);
    toast('Link copied');
  } catch (error) { toast(`Could not copy link: ${error.message}`); }
};
const example = new URLSearchParams(location.search).get('example');
if ([...$('example').options].some(option => option.value === example)) $('example').value = example;
try {
  setBusy(true);
  await ready;
  output.innerHTML = '<span class="quiet">Ctrl+Enter to run</span>';
  try {
    const shared = await restoreLink();
    if (shared) {
      source.value = shared.source;
      repaint();
      for (const pkg of shared.packages) await installPackage(pkg);
    } else await loadExample();
  } catch (error) {
    toast(error.message);
    if (!source.value) await loadExample();
  }
  setBusy(false);
} catch (error) { output.textContent = error.message; }
