function buildSign(payload) {
  const ts = Date.now();
  const raw = JSON.stringify(payload) + ':' + ts;
  return btoa(raw);
}

function send(payload) {
  const sign = buildSign(payload);
  return fetch('/api/order', {
    method: 'POST',
    body: JSON.stringify({ payload, sign })
  });
}
