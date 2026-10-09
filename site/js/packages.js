/** Pure-Python wheel installation into the compiler's virtual filesystem. */

const metadataCache = new Map();
const wheelCache = new Map();
const decoder = new TextDecoder('utf-8', { fatal: true });
const MAX_ARCHIVE = 32 * 1024 * 1024;
const MAX_SOURCE = 64 * 1024 * 1024;
const environment = {
  python_version: '3.12', python_full_version: '3.12.0',
  os_name: 'posix', sys_platform: 'emscripten', platform_system: 'Emscripten',
  platform_machine: 'wasm32', platform_release: '', platform_version: '',
  implementation_name: 'diaspore', implementation_version: '3.12.0',
  platform_python_implementation: 'Diaspore', extra: '',
};

/** Packages and exact demo snippets verified against CPython and Diaspore. */
export const curated = [
  { name: 'humps', version: '0.2.2',
    demo: 'from humps import camel; print(camel.case("hello_world"))' },
  { name: 'morse3', version: '0.47',
    demo: 'from morse3 import Morse; print(Morse("SOS").stringToMorse())' },
  { name: 'lorem', version: '0.1.1',
    demo: 'from lorem.text import TextLorem; print(TextLorem(words=["lorem"], srange=(3, 3)).sentence())' },
  { name: 'snowballstemmer', version: '3.1.1',
    demo: 'import snowballstemmer; print(snowballstemmer.stemmer("english").stemWords(["running", "jumped", "cats"]))' },
  { name: 'art', version: '6.5',
    demo: 'from art import text2art; print(text2art("Hi"))' },
  { name: 'easing-functions', version: '1.0.4',
    demo: 'from easing_functions import QuadEaseIn, QuadEaseOut; print(QuadEaseIn()(0.5)); print(QuadEaseOut()(0.5))' },
  { name: 'unionfind', version: '0.2.1',
    demo: 'from unionfind import unionfind; u = unionfind(5); u.unite(1, 2); print(u.issame(1, 2)); print(u.issame(1, 3))' },
  { name: 'hilbertcurve', version: '1.0.1',
    demo: 'from hilbertcurve.hilbertcurve import HilbertCurve; h = HilbertCurve(2, 2); print(h.coordinates_from_distance(3)); print(h.distance_from_coordinates([1, 0]))' },
];

function normalize(name) {
  if (!/^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$/i.test(name)) {
    throw new Error(`Invalid package name: ${name}`);
  }
  return name.toLowerCase().replace(/[-_.]+/g, '-');
}

async function response(url) {
  const result = await fetch(url);
  if (!result.ok) throw new Error(`Package download failed (${result.status}): ${url}`);
  return result;
}

function cached(cache, key, load) {
  if (!cache.has(key)) {
    const pending = load().catch(error => { cache.delete(key); throw error; });
    cache.set(key, pending);
  }
  return cache.get(key);
}

function metadata(name, version = '') {
  const suffix = version ? `/${encodeURIComponent(version)}` : '';
  return cached(metadataCache, name + suffix, async () =>
    (await response(`https://pypi.org/pypi/${name}${suffix}/json`)).json());
}

function compareVersions(left, right) {
  // Release constraints cover stable wheels; prereleases need an explicit pin.
  const parse = value => {
    const match = /^(?:(\d+)!)?(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?(?:\+([a-z0-9.]+))?$/i.exec(value);
    if (!match) throw new Error(`Unsupported package version: ${value}`);
    return { epoch: Number(match[1] || 0), release: match[2].split('.').map(Number),
      pre: match[3] ? ['a', 'b', 'rc'].indexOf(match[3]) : (match[6] && !match[5] ? -1 : 3),
      preNumber: Number(match[4] || 0), post: match[5] === undefined ? -1 : Number(match[5]),
      dev: match[6] === undefined ? Infinity : Number(match[6]), local: match[7] || '' };
  };
  const a = parse(left), b = parse(right);
  const cmp = (x, y) => x < y ? -1 : x > y ? 1 : 0;
  let result = cmp(a.epoch, b.epoch);
  for (let i = 0; !result && i < Math.max(a.release.length, b.release.length); i++) {
    result = cmp(a.release[i] || 0, b.release[i] || 0);
  }
  return result || cmp(a.pre, b.pre) || cmp(a.preNumber, b.preNumber)
    || cmp(a.post, b.post) || cmp(a.dev, b.dev) || cmp(a.local, b.local);
}

function satisfies(version, spec) {
  return spec.replace(/^\s*\(|\)\s*$/g, '').split(',').every(part => {
    if (!part.trim()) return true;
    const match = /^\s*(===|==|!=|~=|>=|<=|>|<)\s*([^\s]+)\s*$/.exec(part);
    if (!match) throw new Error(`Unsupported version constraint: ${spec}`);
    const [, op, target] = match;
    if (op === '===') return version === target;
    if (target.endsWith('.*') && (op === '==' || op === '!=')) {
      const prefix = target.slice(0, -2);
      const same = version === prefix || version.startsWith(prefix + '.');
      return op === '==' ? same : !same;
    }
    const cmp = compareVersions(version, target);
    if (op === '~=') {
      const release = /^(\d+(?:\.\d+)+)/.exec(target);
      if (!release) throw new Error(`Invalid compatible-release constraint: ${part}`);
      const upper = release[1].split('.').map(Number);
      upper.pop(); upper[upper.length - 1]++;
      return cmp >= 0 && compareVersions(version, upper.join('.')) < 0;
    }
    return { '==': cmp === 0, '!=': cmp !== 0, '>=': cmp >= 0,
      '<=': cmp <= 0, '>': cmp > 0, '<': cmp < 0 }[op];
  });
}

function markerApplies(marker) {
  if (!marker.trim()) return true;
  const tokens = marker.match(/\s+|'[^']*'|"[^"]*"|===|==|!=|~=|>=|<=|[<>()]|[a-zA-Z_][a-zA-Z_0-9]*/g) || [];
  if (tokens.join('') !== marker) throw new Error(`Unsupported dependency marker: ${marker}`);
  const words = tokens.filter(token => token.trim());
  let at = 0;
  function value() {
    const token = words[at++];
    if (token && (token[0] === "'" || token[0] === '"')) return token.slice(1, -1);
    if (Object.hasOwn(environment, token)) return environment[token];
    throw new Error(`Unknown dependency marker variable: ${token}`);
  }
  function atom() {
    if (words[at] === '(') {
      at++; const result = or();
      if (words[at++] !== ')') throw new Error(`Invalid dependency marker: ${marker}`);
      return result;
    }
    const left = value();
    let op = words[at++];
    if (op === 'not' && words[at++] === 'in') op = 'not in';
    const right = value();
    if (op === 'in') return right.includes(left);
    if (op === 'not in') return !right.includes(left);
    if (!['===', '==', '!=', '<', '>', '<=', '>=', '~='].includes(op)) throw new Error(`Invalid marker operator: ${op}`);
    if (/^\d/.test(left) && /^\d/.test(right)) return satisfies(left, op + right);
    if (op === '==' || op === '===') return left === right;
    if (op === '!=') return left !== right;
    if (op === '~=') throw new Error(`Invalid version marker: ${marker}`);
    return { '<': left < right, '>': left > right, '<=': left <= right, '>=': left >= right }[op];
  }
  function and() {
    let result = atom();
    while (words[at] === 'and') { at++; const next = atom(); result = result && next; }
    return result;
  }
  function or() {
    let result = and();
    while (words[at] === 'or') { at++; const next = and(); result = result || next; }
    return result;
  }
  const result = or();
  if (at !== words.length) throw new Error(`Invalid dependency marker: ${marker}`);
  return result;
}

function requirement(text) {
  const semicolon = text.indexOf(';');
  const body = semicolon < 0 ? text : text.slice(0, semicolon);
  if (semicolon >= 0 && !markerApplies(text.slice(semicolon + 1))) return null;
  const match = /^\s*([\w.-]+)(?:\[[\w, .-]+\])?\s*(.*?)\s*$/.exec(body);
  if (!match || match[2].includes('@')) throw new Error(`Unsupported dependency: ${text}`);
  return { name: normalize(match[1]), spec: match[2] };
}

function pureWheel(files) {
  return files.filter(file => file.packagetype === 'bdist_wheel' && !file.yanked
    && /-(?:py3|py2\.py3)-none-any\.whl$/.test(file.filename)
    && satisfies(environment.python_full_version, file.requires_python || ''))
    .sort((a, b) => a.filename.localeCompare(b.filename))[0];
}

async function select(name, constraints, pin) {
  if (pin) {
    const data = await metadata(name, pin);
    const wheel = pureWheel(data.urls);
    if (!wheel) throw new Error(`${name} ${pin} has no compatible pure-Python wheel (py3-none-any). Native extensions and source distributions are not supported.`);
    return { name, version: data.info.version, wheel, dependencies: data.info.requires_dist || [] };
  }
  const data = await metadata(name);
  const versions = Object.keys(data.releases).filter(version =>
    /^\d+(?:\.\d+)*(?:\.post\d+)?$/.test(version)
    && constraints.every(spec => satisfies(version, spec)) && pureWheel(data.releases[version]));
  versions.sort((a, b) => compareVersions(b, a));
  if (!versions.length) throw new Error(`${name} has no compatible pure-Python wheel (py3-none-any) for ${constraints.join(', ') || 'Python 3.12'}. Native extensions and source distributions are not supported.`);
  return select(name, constraints, versions[0]);
}

function pythonSource(bytes) {
  const header = new TextDecoder('latin1').decode(bytes.subarray(0, 300)).split(/\r?\n/).slice(0, 2).join('\n');
  const cookie = /coding[:=]\s*([-\w.]+)/.exec(header);
  const encoding = cookie ? cookie[1].replace(/_/g, '-') : 'utf-8';
  return new TextDecoder(encoding, { fatal: true }).decode(bytes);
}

async function unzip(buffer) {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  let end = bytes.length - 22;
  while (end >= Math.max(0, bytes.length - 65557)) {
    if (view.getUint32(end, true) === 0x06054b50
      && end + 22 + view.getUint16(end + 20, true) === bytes.length) break;
    end--;
  }
  if (end < Math.max(0, bytes.length - 65557)) throw new Error('Invalid wheel ZIP directory.');
  const count = view.getUint16(end + 10, true);
  let at = view.getUint32(end + 16, true), total = 0;
  if (view.getUint16(end + 4, true) || view.getUint16(end + 6, true)
    || count === 65535 || count > 20000 || at + view.getUint32(end + 12, true) !== end) {
    throw new Error('Unsupported wheel ZIP directory (multi-volume or ZIP64).');
  }
  const files = Object.create(null);
  for (let i = 0; i < count; i++) {
    if (at + 46 > end || view.getUint32(at, true) !== 0x02014b50) throw new Error('Invalid wheel ZIP entry.');
    const flags = view.getUint16(at + 8, true), method = view.getUint16(at + 10, true);
    const size = view.getUint32(at + 20, true), expanded = view.getUint32(at + 24, true);
    const nameSize = view.getUint16(at + 28, true), extra = view.getUint16(at + 30, true), comment = view.getUint16(at + 32, true);
    const local = view.getUint32(at + 42, true);
    if (at + 46 + nameSize + extra + comment > end) throw new Error('Invalid wheel ZIP filename.');
    let name = decoder.decode(bytes.subarray(at + 46, at + 46 + nameSize));
    at += 46 + nameSize + extra + comment;
    if (!name.endsWith('.py')) continue;
    if (name.includes('\\') || name.startsWith('/') || name.split('/').some(part => !part || part === '.' || part === '..')) throw new Error('Unsafe wheel source path.');
    if (name.split('/')[0].endsWith('.dist-info')) continue;
    if (name.split('/')[0].endsWith('.data')) {
      const parts = name.split('/');
      if (parts[1] !== 'purelib') continue;
      name = parts.slice(2).join('/');
    }
    total += expanded;
    if (total > MAX_SOURCE) throw new Error('Wheel Python sources exceed 64 MiB.');
    if (flags & 1 || ![0, 8].includes(method)) throw new Error('Unsupported wheel ZIP compression.');
    if (local + 30 > end || view.getUint32(local, true) !== 0x04034b50) throw new Error('Invalid wheel ZIP local header.');
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    if (start + size > end) throw new Error('Truncated wheel ZIP data.');
    let source = bytes.subarray(start, start + size);
    if (method === 8) {
      const stream = new Blob([source]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      const reader = stream.getReader(), chunks = [];
      let length = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.length;
        if (length > expanded) { await reader.cancel(); throw new Error('Wheel ZIP size mismatch.'); }
        chunks.push(value);
      }
      source = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { source.set(chunk, offset); offset += chunk.length; }
    }
    if (source.length !== expanded) throw new Error('Wheel ZIP size mismatch.');
    const path = '/site-packages/' + name;
    if (Object.hasOwn(files, path)) throw new Error(`Duplicate wheel source: ${name}`);
    files[path] = pythonSource(source);
  }
  if (at !== end) throw new Error('Invalid wheel ZIP directory size.');
  if (!Object.keys(files).length) throw new Error('Wheel contains no importable Python sources.');
  return files;
}

async function wheelFiles(wheel) {
  return cached(wheelCache, wheel.url, async () => {
    const url = new URL(wheel.url);
    if (url.protocol !== 'https:' || url.hostname !== 'files.pythonhosted.org') throw new Error('Unexpected PyPI wheel host.');
    if (wheel.size > MAX_ARCHIVE) throw new Error('Wheel download exceeds 32 MiB.');
    const result = await response(url.href);
    const reader = result.body.getReader(), chunks = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > MAX_ARCHIVE) { await reader.cancel(); throw new Error('Wheel download exceeds 32 MiB.'); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const hash = await crypto.subtle.digest('SHA-256', bytes);
    const hex = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
    if (hex !== wheel.digests.sha256) throw new Error('Wheel SHA-256 does not match PyPI metadata.');
    return Object.freeze(await unzip(bytes.buffer));
  });
}

/**
 * Install a wheel and its applicable dependencies without executing package code.
 * Extras are not enabled. Dependency conflicts fail rather than changing an
 * already selected version. Installation does not imply runtime compatibility.
 * @param {string} name PyPI distribution name.
 * @param {string} [version] Exact version, or the latest stable pure wheel.
 * @returns {Promise<{name: string, version: string, files: Record<string, string>}>}
 */
export async function install(name, version) {
  name = normalize(name);
  const queue = [{ name, spec: version ? '===' + version : '', pin: version }];
  const selected = new Map(), constraints = new Map();
  const files = Object.create(null);
  while (queue.length) {
    const item = queue.shift();
    const specs = constraints.get(item.name) || [];
    specs.push(item.spec); constraints.set(item.name, specs);
    if (selected.has(item.name)) {
      if (!specs.every(spec => satisfies(selected.get(item.name).version, spec))) {
        throw new Error(`Conflicting dependencies for ${item.name}: ${specs.join(', ')}. Install compatible versions explicitly.`);
      }
      continue;
    }
    const pkg = await select(item.name, specs, item.pin);
    selected.set(item.name, pkg);
    for (const [path, source] of Object.entries(await wheelFiles(pkg.wheel))) {
      if (Object.hasOwn(files, path) && files[path] !== source) throw new Error(`Packages contain conflicting source files: ${path}`);
      files[path] = source;
    }
    for (const text of pkg.dependencies) {
      const dep = requirement(text);
      if (dep) queue.push(dep);
    }
  }
  return { name, version: selected.get(name).version, files };
}

let stdlibPromise;
/** Load the stdlib shims generated by make site, keyed by /stdlib/ paths. */
export function stdlib() {
  if (!stdlibPromise) {
    const url = new URL('../stdlib.json', import.meta.url);
    url.search = new URL(import.meta.url).search;
    stdlibPromise = response(url).then(result => result.json())
      .catch(error => { stdlibPromise = undefined; throw error; });
  }
  return stdlibPromise;
}
