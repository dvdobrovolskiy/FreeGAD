<!-- SPDX-License-Identifier: AGPL-3.0-or-later
     Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com -->

<script>
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  let username = $state('admin');
  let password = $state('');
  let error = $state('');
  let busy = $state(false);

  async function submit(e) {
    e.preventDefault();
    busy = true; error = '';
    try {
      await api.login(username, password);
      goto('/');
    } catch (ex) {
      error = ex.message === 'unauthorized' ? 'Wrong username or password.' : ex.message;
    } finally { busy = false; }
  }
</script>

<main class="wrap">
  <form class="card" onsubmit={submit}>
    <h1>FreeGAD telemetry</h1>
    <p class="muted">Sign in to see usage statistics.</p>
    <label>Username <input bind:value={username} autocomplete="username" /></label>
    <label>Password <input type="password" bind:value={password} autocomplete="current-password" /></label>
    {#if error}<p class="err">{error}</p>{/if}
    <button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
  </form>
</main>

<style>
  :global(body) { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--ink); }
  :global(:root) { --bg: #fcfcfb; --ink: #0b0b0b; --muted: #52514e; --card: #ffffff; --line: #e4e3df; --accent: #2a78d6; }
  @media (prefers-color-scheme: dark) { :global(:root) { --bg: #1a1a19; --ink: #ffffff; --muted: #c3c2b7; --card: #242423; --line: #383835; --accent: #3987e5; } }
  .wrap { min-height: 100vh; display: grid; place-items: center; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 28px; width: min(360px, 90vw); display: grid; gap: 12px; }
  h1 { margin: 0; font-size: 1.3rem; }
  .muted { color: var(--muted); margin: 0; }
  label { display: grid; gap: 4px; font-size: .9rem; }
  input { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); font-size: 1rem; }
  button { padding: 10px; border: 0; border-radius: 8px; background: var(--accent); color: #fff; font-size: 1rem; cursor: pointer; }
  .err { color: #c7371f; margin: 0; }
</style>
