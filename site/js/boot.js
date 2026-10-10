try {
  const response = await fetch(new URL('../build.json', import.meta.url), {cache: 'no-store'});
  if (!response.ok) throw new Error('Site assets are not built. Run make site.');
  const build = await response.json();
  globalThis.diasporeBuild = build.version;
  globalThis.diasporeStdlib = build.stdlib;
  const base = new URL('../', import.meta.url);
  const imports = {};
  for (const path of build.modules) {
    const url = new URL(path, base).href;
    imports[url] = `${url}?v=${build.version}`;
  }
  const map = document.createElement('script');
  map.type = 'importmap';
  map.textContent = JSON.stringify({imports});
  document.head.append(map);
  document.querySelector('link[rel="stylesheet"]').href = new URL(`style.css?v=${build.version}`, base);
  const entry = new URL(document.documentElement.dataset.entry, base).href;
  if (document.documentElement.hasAttribute('data-runtime')) {
    const {ready} = await import(new URL('js/runtime.js', base).href);
    await Promise.all([ready, import(entry)]);
  } else {
    await import(entry);
  }
} catch (error) {
  const status = document.querySelector('.runtime-status');
  if (status) status.textContent = 'Unable to load';
  const output = document.querySelector('#output, #transcript');
  if (output) output.textContent = error.message;
  else console.error(error);
}
