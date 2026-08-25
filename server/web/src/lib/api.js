// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

async function req(path, opts = {}) {
  const r = await fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...opts });
  if (r.status === 401) throw new Error('unauthorized');
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}
export const api = {
  me: () => req('/api/v1/me'),
  login: (username, password) => req('/api/v1/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => req('/api/v1/logout', { method: 'POST' }),
  stats: (days) => req(`/api/v1/stats?days=${days}`),
  turns: (q = '') => req(`/api/v1/turns?limit=60${q}`),
  installs: () => req('/api/v1/installs')
};
export const fmt = {
  n: (v) => (v ?? 0).toLocaleString('en-US'),
  k: (v) => (v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : String(v ?? 0)),
  ms: (v) => (v == null ? '—' : v >= 60000 ? (v / 60000).toFixed(1) + ' min' : v >= 1000 ? (v / 1000).toFixed(1) + ' s' : v + ' ms'),
  ts: (t) => (t ? new Date(t * 1000).toLocaleString() : '—'),
  usd: (v) => '$' + (v ?? 0).toFixed(2)
};
