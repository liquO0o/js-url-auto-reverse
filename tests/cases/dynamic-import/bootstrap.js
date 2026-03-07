async function boot() {
  const mod = await import('./chunk-sign.js');
  const w = new Worker('./worker-sign.js');
  if (navigator.serviceWorker) {
    navigator.serviceWorker.register('./sw.js');
  }
  w.postMessage({ ping: 1 });
  return mod.run();
}
boot();
