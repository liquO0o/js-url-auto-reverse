export async function run() {
  const nonce = Math.random().toString(36).slice(2);
  const sign = btoa('x:' + nonce);
  await WebAssembly.instantiateStreaming(fetch('./sign.wasm'), {});
  return fetch('/api/dyn', {
    method: 'POST',
    body: JSON.stringify({ sign, nonce })
  });
}
//# sourceMappingURL=chunk-sign.js.map
