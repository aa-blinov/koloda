// The client of the Koloda API. Every call of the interface goes through here,
// so the shape of an answer is written down in one place.

async function request(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  if (!response.ok) {
    const error = new Error((data && data.error) || 'Сервис не отвечает');
    error.status = response.status;
    throw error;
  }
  return data;
}

const get = (url) => request(url, { method: 'GET' });
const post = (url, body) => request(url, { method: 'POST', body: JSON.stringify(body ?? {}) });
const put = (url, body) => request(url, { method: 'PUT', body: JSON.stringify(body ?? {}) });
const del = (url, body) => request(url, { method: 'DELETE', body: JSON.stringify(body ?? {}) });

export const api = {
  // Профиль
  me: () => get('/api/auth/me'),  // ответ: { account: {...} }
  login: (username, password) => post('/api/auth/login', { username, password }),
  register: (username, password, email) => post('/api/auth/register', { username, password, email }),
  logout: () => post('/api/auth/logout'),
  verifyEmail: (token) => post('/api/auth/verify', { token }),
  setEmail: (password, email) => post('/api/auth/email', { password, email }),
  resendVerification: (email) => post('/api/auth/resend-verification', { email }),
  forgot: (email) => post('/api/auth/forgot', { email }),
  resetPassword: (token, password) => post('/api/auth/reset', { token, password }),

  // Состояние профиля
  state: () => get('/api/state'),
  mergeState: (body) => post('/api/state/merge', body),
  readPosts: (posts, unread) => post('/api/state/read', { posts, unread }),
  setSubscription: (channel, subscribed) => post('/api/state/subscription', { channel, subscribed }),
  exportState: () => get('/api/state/export'),
  settings: () => get('/api/settings'),
  saveSettings: (settings) => put('/api/settings', settings),

  // Лента
  feed: (params) => get('/api/feed?' + new URLSearchParams(params)),
  preview: (params) => get('/api/preview?' + new URLSearchParams(params)),
  history: () => get('/api/history'),

  // Каталог
  catalog: (params) => get('/api/catalog?' + new URLSearchParams(params)),
  categories: () => get('/api/categories'),
  avatars: (channels, fresh = false) =>
    get('/api/avatars?channels=' + encodeURIComponent(channels.join(',')) + (fresh ? '&fresh=1' : '')),
  check: (channel) => get('/api/check?c=' + encodeURIComponent(channel)),

  // Сохранённое
  saved: (params) => get('/api/saved?' + new URLSearchParams(params)),
  setSaved: (channel, msg_id, on) => post('/api/saved', { channel, msg_id, on }),

  // Коллекции
  collections: () => get('/api/collections'),
  saveCollection: (body) => post('/api/collections/save', body),
  deleteCollection: (id) => post('/api/collections/delete', { id }),

  // Медиа
  media: (post) => get('/api/media?post=' + encodeURIComponent(post)),

  // Telegram
  tgStatus: () => get('/api/tg/status'),
  tgChannels: () => get('/api/tg/channels'),
  tgLinkStart: () => post('/api/tg/link/start'),
  tgLinkPoll: (token) => post('/api/tg/link/poll', { token }),
  tgLinkCancel: (token) => post('/api/tg/link/cancel', { token }),
  tgSubscribe: (channel, subscribed) => post('/api/tg/subscribe', { channel, subscribed }),
  tgSync: () => post('/api/tg/sync'),
  tgDiscover: () => post('/api/tg/discover'),
  tgUnlink: () => post('/api/tg/unlink'),
};

export default api;
