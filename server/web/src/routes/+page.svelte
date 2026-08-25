<!-- SPDX-License-Identifier: AGPL-3.0-or-later
     Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com -->

<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api, fmt } from '$lib/api.js';

  let days = $state(30);
  let stats = $state(null);
  let turns = $state([]);
  let installs = $state([]);
  let turnFilter = $state('');
  let error = $state('');
  let loading = $state(true);
  let showTable = $state({});

  async function load() {
    loading = true; error = '';
    try {
      [stats, turns, installs] = await Promise.all([api.stats(days), api.turns(turnFilter), api.installs()]);
    } catch (ex) {
      if (ex.message === 'unauthorized') { goto('/login'); return; }
      error = ex.message;
    } finally { loading = false; }
  }
  onMount(load);
  async function logout() { await api.logout(); goto('/login'); }

  // ---- chart helpers (single-series bar charts; blue = volume, status colors only for hang/error)
  function bars(rows, key) {
    const max = Math.max(1, ...rows.map((r) => r[key] || 0));
    return rows.map((r) => ({ label: r.d, v: r[key] || 0, h: ((r[key] || 0) / max) * 100 }));
  }
  const t = $derived(stats?.totals);
</script>

<svelte:head><title>FreeGAD telemetry</title></svelte:head>

<header>
  <h1>FreeGAD telemetry</h1>
  <div class="filters">
    <label>Range
      <select bind:value={days} onchange={load}>
        <option value={7}>7 days</option><option value={30}>30 days</option>
        <option value={90}>90 days</option><option value={365}>1 year</option>
      </select>
    </label>
    <button class="ghost" onclick={load}>Refresh</button>
    <button class="ghost" onclick={logout}>Sign out</button>
  </div>
</header>

{#if error}<p class="err">{error}</p>{/if}
{#if loading && !stats}<p class="muted">Loading…</p>{/if}

{#if stats}
<main>
  <section class="tiles">
    <div class="tile"><span class="k">Installs active</span><span class="v">{fmt.n(t.installs)}</span><span class="s">{fmt.n(t.session_installs)} started FreeCAD</span></div>
    <div class="tile"><span class="k">Turns</span><span class="v">{fmt.n(t.turns)}</span><span class="s">{fmt.n(t.api_calls)} API calls · {fmt.n(t.tool_calls)} tool calls</span></div>
    <div class="tile"><span class="k">Tokens in / out</span><span class="v">{fmt.k(t.input_tokens)} / {fmt.k(t.output_tokens)}</span><span class="s">{fmt.k(t.tokens_per_turn)} per turn</span></div>
    <div class="tile"><span class="k">Cache hit</span><span class="v">{t.cache_hit_pct}%</span><span class="s">{fmt.k(t.cache_read_tokens)} read · {fmt.k(t.cache_create_tokens)} written</span></div>
    <div class="tile"><span class="k">Est. API cost</span><span class="v">{fmt.usd(t.est_cost_usd)}</span><span class="s">list prices, all installs</span></div>
    <div class="tile"><span class="k">Turn latency</span><span class="v">{fmt.ms(t.avg_ms)}</span><span class="s">avg · max {fmt.ms(t.max_ms)} · CPU avg {fmt.ms(t.avg_cpu_ms)}</span></div>
    <div class="tile" class:warn={t.hangs > 0}><span class="k">GUI hangs ≥ 2 s</span><span class="v">{fmt.n(t.hangs)}</span><span class="s">{t.turns ? (100 * t.hangs / t.turns).toFixed(1) : 0}% of turns</span></div>
    <div class="tile" class:bad={t.errors > 0}><span class="k">Errors</span><span class="v">{fmt.n(t.errors)}</span><span class="s">{t.declined} of {t.write_calls} edits declined by users</span></div>
  </section>

  <section class="grid2">
    {#each [['turns', 'Turns per day'], ['out', 'Output tokens per day'], ['inp', 'Uncached input tokens per day'], ['hangs', 'GUI hangs per day']] as [key, title]}
      <figure class="card">
        <figcaption>{title}<button class="ghost small" onclick={() => (showTable[key] = !showTable[key])}>{showTable[key] ? 'chart' : 'table'}</button></figcaption>
        {#if showTable[key]}
          <table><thead><tr><th>Day</th><th class="num">{title}</th></tr></thead>
            <tbody>{#each stats.daily as r}<tr><td>{r.d}</td><td class="num">{fmt.n(r[key])}</td></tr>{/each}</tbody></table>
        {:else if stats.daily.length === 0}
          <p class="muted">No data in range.</p>
        {:else}
          <div class="bars" class:status={key === 'hangs'}>
            {#each bars(stats.daily, key) as b}
              <div class="bar" title={`${b.label}: ${fmt.n(b.v)}`}><div class="fill" style="height:{Math.max(b.h, b.v ? 2 : 0)}%"></div></div>
            {/each}
          </div>
          <div class="axis"><span>{stats.daily[0].d}</span><span>{stats.daily[stats.daily.length - 1].d}</span></div>
        {/if}
      </figure>
    {/each}
  </section>

  <section class="grid2">
    <figure class="card">
      <figcaption>Tools — calls, GUI-thread time, hangs</figcaption>
      <table><thead><tr><th>Tool</th><th class="num">Calls</th><th class="num">Errors</th><th class="num">Avg GUI</th><th class="num">Max GUI</th><th class="num">Hangs</th></tr></thead>
        <tbody>{#each stats.tools as r}
          <tr class:warnrow={r.hangs > 0}><td>{r.name}</td><td class="num">{fmt.n(r.calls)}</td><td class="num">{r.errors}</td><td class="num">{fmt.ms(r.avg_gui_ms)}</td><td class="num">{fmt.ms(r.max_gui_ms)}</td><td class="num">{r.hangs}</td></tr>
        {/each}</tbody></table>
    </figure>
    <figure class="card">
      <figcaption>Models &amp; effort</figcaption>
      <table><thead><tr><th>Model</th><th class="num">Turns</th><th class="num">In</th><th class="num">Out</th><th class="num">Cache read</th><th class="num">Est. $</th></tr></thead>
        <tbody>{#each stats.per_model as m}
          <tr><td>{m.model || '—'}</td><td class="num">{fmt.n(m.n)}</td><td class="num">{fmt.k(m.inp)}</td><td class="num">{fmt.k(m.out)}</td><td class="num">{fmt.k(m.cr)}</td><td class="num">{fmt.usd(m.est_cost_usd)}</td></tr>
        {/each}</tbody></table>
      <p class="muted small">Effort: {Object.entries(stats.effort).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}
        &nbsp;·&nbsp; Latency: {Object.entries(stats.latency_buckets).map(([k, v]) => `${k} ${v}`).join(' · ')}</p>
      <p class="muted small">Versions: {stats.versions.map((v) => `${v.plugin_version || '?'} / FreeCAD ${v.freecad_version || '?'} (${v.installs})`).join(' · ') || '—'}</p>
      {#if stats.errors.length}
        <table><thead><tr><th>Error</th><th class="num">Count</th></tr></thead>
          <tbody>{#each stats.errors as e}<tr><td class="mono">{e.error}</td><td class="num">{e.n}</td></tr>{/each}</tbody></table>
      {/if}
    </figure>
  </section>

  <section class="card">
    <figcaption>Recent turns
      <span class="filters">
        <label><input type="radio" bind:group={turnFilter} value="" onchange={load} /> all</label>
        <label><input type="radio" bind:group={turnFilter} value="&hangs=1" onchange={load} /> hangs</label>
        <label><input type="radio" bind:group={turnFilter} value="&errors=1" onchange={load} /> errors</label>
      </span>
    </figcaption>
    <div class="scroll">
    <table><thead><tr><th>When</th><th>Install</th><th>Model</th><th>Effort</th><th class="num">Objects</th><th class="num">Prompt</th><th class="num">In</th><th class="num">Cache</th><th class="num">Out</th><th class="num">API</th><th>Tools</th><th class="num">Total</th><th class="num">CPU</th><th class="num">Max block</th><th>Stop</th><th>Error</th></tr></thead>
      <tbody>{#each turns as r}
        <tr class:warnrow={r.hang} class:badrow={r.error}>
          <td>{fmt.ts(r.ts)}</td><td class="mono">{r.install_id}</td><td>{r.model}</td><td>{r.effort}</td>
          <td class="num">{r.doc_objects ?? '—'}</td><td class="num">{r.prompt_chars ?? '—'}</td>
          <td class="num">{fmt.k(r.input_tokens)}</td><td class="num">{fmt.k(r.cache_read_tokens)}</td><td class="num">{fmt.k(r.output_tokens)}</td>
          <td class="num">{r.api_calls}</td><td class="mono small">{(r.tools || []).join(', ')}</td>
          <td class="num">{fmt.ms(r.total_ms)}</td><td class="num">{fmt.ms(r.cpu_ms)}</td><td class="num">{fmt.ms(r.max_gui_block_ms)}</td>
          <td>{r.stop_reason || ''}{r.fallback ? ' (fallback)' : ''}</td><td class="mono small">{r.error || ''}</td>
        </tr>
      {/each}</tbody></table>
    </div>
  </section>

  <section class="card">
    <figcaption>Installs (anonymous ids)</figcaption>
    <div class="scroll">
    <table><thead><tr><th>Install</th><th>First seen</th><th>Last seen</th><th class="num">Sessions</th><th class="num">Turns</th><th class="num">Tokens</th><th class="num">Hangs</th><th class="num">Errors</th><th>Plugin</th><th>FreeCAD</th><th>OS</th></tr></thead>
      <tbody>{#each installs as r}
        <tr><td class="mono">{r.install_id}</td><td>{fmt.ts(r.first_seen)}</td><td>{fmt.ts(r.last_seen)}</td><td class="num">{r.sessions}</td><td class="num">{r.turns}</td><td class="num">{fmt.k(r.tokens)}</td><td class="num">{r.hangs}</td><td class="num">{r.errors}</td><td>{r.plugin_version}</td><td>{r.freecad_version}</td><td>{r.os}</td></tr>
      {/each}</tbody></table>
    </div>
  </section>
</main>
{/if}

<style>
  :global(body) { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--ink); }
  :global(:root) { --bg: #fcfcfb; --ink: #0b0b0b; --muted: #52514e; --card: #ffffff; --line: #e4e3df; --accent: #2a78d6; --warn: #fab219; --bad: #c7371f; --warnbg: #fff6dd; --badbg: #fde8e4; }
  @media (prefers-color-scheme: dark) { :global(:root) { --bg: #1a1a19; --ink: #ffffff; --muted: #c3c2b7; --card: #242423; --line: #383835; --accent: #3987e5; --warnbg: #3a3312; --badbg: #42201a; } }
  header { display: flex; justify-content: space-between; align-items: center; padding: 14px 22px; border-bottom: 1px solid var(--line); flex-wrap: wrap; gap: 8px; }
  h1 { font-size: 1.2rem; margin: 0; }
  main { padding: 18px 22px; display: grid; gap: 16px; max-width: 1500px; margin: 0 auto; }
  .filters { display: flex; gap: 10px; align-items: center; font-size: .9rem; }
  select, button { font: inherit; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--card); color: var(--ink); cursor: pointer; }
  .ghost.small { padding: 2px 8px; font-size: .75rem; float: right; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
  .tile { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: grid; gap: 2px; }
  .tile .k { font-size: .78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; }
  .tile .v { font-size: 1.5rem; font-weight: 600; font-variant-numeric: tabular-nums; }
  .tile .s { font-size: .78rem; color: var(--muted); }
  .tile.warn { border-color: var(--warn); } .tile.bad { border-color: var(--bad); }
  .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin: 0; min-width: 0; }
  figcaption { font-weight: 600; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .bars { display: flex; align-items: flex-end; gap: 2px; height: 140px; }
  .bar { flex: 1; height: 100%; display: flex; align-items: flex-end; min-width: 3px; }
  .fill { width: 100%; background: var(--accent); border-radius: 3px 3px 0 0; }
  .bars.status .fill { background: var(--warn); }
  .bar:hover .fill { opacity: .75; }
  .axis { display: flex; justify-content: space-between; font-size: .72rem; color: var(--muted); margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  th { color: var(--muted); font-weight: 500; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .mono { font-family: ui-monospace, Consolas, monospace; }
  .small { font-size: .78rem; }
  .muted { color: var(--muted); }
  .err { color: var(--bad); padding: 0 22px; }
  .scroll { overflow-x: auto; }
  .warnrow { background: var(--warnbg); } .badrow { background: var(--badbg); }
</style>
