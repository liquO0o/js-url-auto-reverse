// Optional runtime hook templates (dynamic mode only)

(function initHookStore() {
  if (typeof window === 'undefined') return;

  var cfg = window.__JS_REVERSE_HOOK_CONFIG || {};
  var maxEvents = Number.isFinite(cfg.maxEvents) ? cfg.maxEvents : 800;
  var maxValueLen = Number.isFinite(cfg.maxValueLen) ? cfg.maxValueLen : 512;

  function cut(text) {
    var s = String(text);
    if (s.length <= maxValueLen) return s;
    return s.slice(0, maxValueLen) + '...(truncated)';
  }

  function normalize(value, depth) {
    if (depth > 2) return cut(value);
    if (value === null || value === undefined) return value;
    var t = typeof value;
    if (t === 'string' || t === 'number' || t === 'boolean') return t === 'string' ? cut(value) : value;

    if (Array.isArray(value)) {
      var outArr = [];
      for (var i = 0; i < Math.min(value.length, 20); i += 1) {
        outArr.push(normalize(value[i], depth + 1));
      }
      return outArr;
    }

    if (t === 'object') {
      var outObj = {};
      var keys = Object.keys(value).slice(0, 20);
      for (var j = 0; j < keys.length; j += 1) {
        var key = keys[j];
        try {
          outObj[cut(key)] = normalize(value[key], depth + 1);
        } catch (e) {
          outObj[cut(key)] = '[unreadable]';
        }
      }
      return outObj;
    }

    return cut(value);
  }

  var store = window.__jsReverseLogs;
  if (!store || !Array.isArray(store.events)) {
    store = { events: [], dropped: 0, maxEvents: maxEvents, maxValueLen: maxValueLen };
    window.__jsReverseLogs = store;
  }

  window.__jsReversePush = function push(tag, payload) {
    try {
      var evt = {
        ts: Date.now(),
        tag: cut(tag),
        payload: normalize(payload, 0)
      };
      if (store.events.length < store.maxEvents) {
        store.events.push(evt);
      } else {
        store.dropped += 1;
      }
      return evt;
    } catch (e) {
      return null;
    }
  };
})();

(function hookFetch() {
  if (typeof window === 'undefined' || !window.fetch) return;
  var raw = window.fetch;
  window.fetch = async function () {
    var args = Array.prototype.slice.call(arguments);
    try {
      window.__jsReversePush('[HOOK][fetch][in]', { url: args[0], init: args[1] });
      console.log('[HOOK][fetch][in]', args[0], args[1]);
    } catch (e) {}
    var ret = await raw.apply(this, args);
    try {
      window.__jsReversePush('[HOOK][fetch][out]', { status: ret && ret.status, ok: ret && ret.ok, url: ret && ret.url });
    } catch (e) {}
    return ret;
  };
})();

(function hookXHR() {
  if (typeof XMLHttpRequest === 'undefined') return;
  var rawOpen = XMLHttpRequest.prototype.open;
  var rawSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__hookMeta = { method: method, url: url };
    return rawOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function (body) {
    try {
      window.__jsReversePush('[HOOK][xhr][in]', { meta: this.__hookMeta || {}, body: body });
      console.log('[HOOK][xhr][in]', this.__hookMeta, body);
    } catch (e) {}
    return rawSend.apply(this, arguments);
  };
})();

(function hookCryptoJS() {
  if (typeof window === 'undefined' || !window.CryptoJS || !CryptoJS.AES || !CryptoJS.AES.encrypt) return;
  var raw = CryptoJS.AES.encrypt;
  CryptoJS.AES.encrypt = function () {
    var args = Array.prototype.slice.call(arguments);
    try {
      window.__jsReversePush('[HOOK][CryptoJS.AES.encrypt][in]', { args: args });
      console.log('[HOOK][CryptoJS.AES.encrypt][in]', args);
    } catch (e) {}
    var out = raw.apply(this, args);
    try {
      var outText = out && out.toString ? out.toString() : out;
      window.__jsReversePush('[HOOK][CryptoJS.AES.encrypt][out]', { output: outText });
      console.log('[HOOK][CryptoJS.AES.encrypt][out]', outText);
    } catch (e) {}
    return out;
  };
})();

(function hookSubtle() {
  if (typeof crypto === 'undefined' || !crypto.subtle) return;
  var subtle = crypto.subtle;

  if (subtle.encrypt) {
    var rawEncrypt = subtle.encrypt.bind(subtle);
    subtle.encrypt = async function () {
      var args = Array.prototype.slice.call(arguments);
      try {
        window.__jsReversePush('[HOOK][subtle.encrypt][in]', { algorithm: args[0] });
        console.log('[HOOK][subtle.encrypt][in]', args[0]);
      } catch (e) {}
      return rawEncrypt.apply(subtle, args);
    };
  }

  if (subtle.sign) {
    var rawSign = subtle.sign.bind(subtle);
    subtle.sign = async function () {
      var args = Array.prototype.slice.call(arguments);
      try {
        window.__jsReversePush('[HOOK][subtle.sign][in]', { algorithm: args[0] });
      } catch (e) {}
      return rawSign.apply(subtle, args);
    };
  }
})();
