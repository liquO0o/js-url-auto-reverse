self.onmessage = (e) => {
  const seed = Date.now().toString();
  const sign = btoa('wk:' + seed);
  self.postMessage({ sign, seed, payload: e.data });
};
