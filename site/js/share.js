const MAX_SOURCE = 128 * 1024;
const MAX_FRAGMENT = 48 * 1024;
const packagePattern = /^[a-zA-Z0-9][a-zA-Z0-9._-]*(?:==[a-zA-Z0-9][a-zA-Z0-9.!+_-]*)?$/;

function packagesFrom(text) {
  const packages = text ? text.split(',') : [];
  if (packages.length > 20 || packages.some(name => name.length > 160 || !packagePattern.test(name))) {
    throw new Error('Invalid package list in shared link.');
  }
  return packages;
}

export async function shareLink(source, packages) {
  const bytes = new TextEncoder().encode(source);
  if (bytes.length > MAX_SOURCE) throw new Error('Source is too large to share (128 KB maximum).');
  packagesFrom(packages.join(','));
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'));
  const compressed = new Uint8Array(await new Response(stream).arrayBuffer());
  let binary = '';
  for (const byte of compressed) binary += String.fromCharCode(byte);
  const encoded = btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
  const fragment = new URLSearchParams({code: encoded, pkgs: packages.join(',')}).toString();
  if (fragment.length > MAX_FRAGMENT) throw new Error('This program is too large for a share link.');
  const url = new URL(location.href);
  url.search = '';
  url.hash = fragment;
  return url.href;
}

export async function restoreLink() {
  if (!location.hash) return null;
  if (location.hash.length > MAX_FRAGMENT + 1) throw new Error('Shared link is too large.');
  const params = new URLSearchParams(location.hash.slice(1));
  const code = params.get('code');
  if (!code || !/^[a-zA-Z0-9_-]+$/.test(code)) throw new Error('Invalid shared source.');
  const packages = packagesFrom(params.get('pkgs') || '');
  try {
    const binary = atob(code.replaceAll('-', '+').replaceAll('_', '/'));
    const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
    const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
    const chunks = [];
    let size = 0;
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.length;
      if (size > MAX_SOURCE) { await reader.cancel(); throw new Error('Source exceeds 128 KB.'); }
      chunks.push(value);
    }
    const all = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.length; }
    return {source: new TextDecoder('utf-8', {fatal: true}).decode(all), packages};
  } catch (error) { throw new Error(`Cannot open shared source: ${error.message}`); }
}
