/* Attach the Flask-WTF token to same-origin fetch() calls.
 *
 * Pages that submit normal forms also include a hidden csrf_token input. This
 * wrapper covers the JSON/form-data calls so existing dashboard code did not
 * need a CSRF argument on every fetch.
 */
(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const token = meta ? meta.getAttribute('content') : '';
  if (!token || typeof window.fetch !== 'function') return;

  const nativeFetch = window.fetch.bind(window);

  window.fetch = function (input, init) {
    const request = typeof Request !== 'undefined' && input instanceof Request ? input : null;
    const url = request ? request.url : String(input);
    let sameOrigin = true;
    try {
      sameOrigin = new URL(url, window.location.href).origin === window.location.origin;
    } catch (err) {
      sameOrigin = false;
    }

    const options = init ? Object.assign({}, init) : {};
    const method = String(options.method || (request && request.method) || 'GET').toUpperCase();
    if (sameOrigin && method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      const headers = new Headers(options.headers || (request ? request.headers : undefined));
      if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
      options.headers = headers;
    }
    return nativeFetch(input, options);
  };
})();
