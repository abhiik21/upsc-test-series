/* Shared API client for the UPSC Test Series prototype. */
(() => {
  // Where the API lives:
  //  - an address saved in localStorage ('upsc_api_base') always wins;
  //  - opened from a file or from localhost on any port except 8080 (a local static server such as Live Server on 5500)
  //    -> the development API on http://127.0.0.1:8000;
  //  - anywhere else (a real domain, or the Docker/nginx setup on port 8080) -> the same site, under /api/v1.
  const localHost = ['localhost', '127.0.0.1', '[::1]', ''].includes(location.hostname);
  const devStatic = location.protocol === 'file:' || (localHost && location.port !== '8080');
  const API_BASE = localStorage.getItem('upsc_api_base') || (devStatic ? 'http://127.0.0.1:8000/api/v1' : '/api/v1');

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const token = localStorage.getItem('upsc_access_token');
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg || String(d)).join('; ') : data.detail;
      const error = new Error(detail || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  window.UPSC_API = {
    base: API_BASE,
    request,
    get: (path) => request(path),
    post: (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) }),
    put: (path, body) => request(path, { method: 'PUT', body: JSON.stringify(body) }),
    del: (path) => request(path, { method: 'DELETE' }),
    upload: (path, formData) => request(path, { method: 'POST', body: formData }),
    setSession(data) {
      if (data?.access_token) localStorage.setItem('upsc_access_token', data.access_token);
      if (data?.user) localStorage.setItem('upsc_user', JSON.stringify(data.user));
    },
    clearSession() {
      localStorage.removeItem('upsc_access_token');
      localStorage.removeItem('upsc_user');
    },
    currentUser() {
      try { return JSON.parse(localStorage.getItem('upsc_user') || 'null'); } catch { return null; }
    }
  };
})();
