import { h as head, e as escape_html, a as attr_class, b as ensure_array_like, c as attr, d as attr_style, f as stringify, i as derived } from "../../chunks/root.js";
import "clsx";
import "@sveltejs/kit/internal";
import "../../chunks/exports.js";
import "../../chunks/utils2.js";
import "@sveltejs/kit/internal/server";
import "../../chunks/state.svelte.js";
function goto(url, opts = {}) {
  {
    throw new Error("Cannot call goto(...) on the server");
  }
}
async function req(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) throw new Error("unauthorized");
  if (!r.ok) throw new Error(await r.text() || r.statusText);
  return r.json();
}
const api = {
  me: () => req("/api/v1/me"),
  login: (username, password) => req("/api/v1/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req("/api/v1/logout", { method: "POST" }),
  stats: (days) => req(`/api/v1/stats?days=${days}`),
  turns: (q = "") => req(`/api/v1/turns?limit=60${q}`),
  installs: () => req("/api/v1/installs")
};
const fmt = {
  n: (v) => (v ?? 0).toLocaleString("en-US"),
  k: (v) => v >= 1e6 ? (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "k" : String(v ?? 0),
  ms: (v) => v == null ? "—" : v >= 6e4 ? (v / 6e4).toFixed(1) + " min" : v >= 1e3 ? (v / 1e3).toFixed(1) + " s" : v + " ms",
  ts: (t) => t ? new Date(t * 1e3).toLocaleString() : "—",
  usd: (v) => "$" + (v ?? 0).toFixed(2)
};
function _page($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    let days = 30;
    let stats = null;
    let turns = [];
    let installs = [];
    let turnFilter = "";
    let error = "";
    let loading = true;
    let showTable = {};
    async function load() {
      loading = true;
      error = "";
      try {
        [stats, turns, installs] = await Promise.all([api.stats(days), api.turns(turnFilter), api.installs()]);
      } catch (ex) {
        if (ex.message === "unauthorized") {
          goto();
          return;
        }
        error = ex.message;
      } finally {
        loading = false;
      }
    }
    function bars(rows, key) {
      const max = Math.max(1, ...rows.map((r) => r[key] || 0));
      return rows.map((r) => ({ label: r.d, v: r[key] || 0, h: (r[key] || 0) / max * 100 }));
    }
    const t = derived(() => stats?.totals);
    head("1uha8ag", $$renderer2, ($$renderer3) => {
      $$renderer3.title(($$renderer4) => {
        $$renderer4.push(`<title>FreeGAD telemetry</title>`);
      });
    });
    $$renderer2.push(`<header class="svelte-1uha8ag"><h1 class="svelte-1uha8ag">FreeGAD telemetry</h1> <div class="filters svelte-1uha8ag"><label>Range `);
    $$renderer2.select(
      { value: days, onchange: load, class: "" },
      ($$renderer3) => {
        $$renderer3.option({ value: 7 }, ($$renderer4) => {
          $$renderer4.push(`7 days`);
        });
        $$renderer3.option({ value: 30 }, ($$renderer4) => {
          $$renderer4.push(`30 days`);
        });
        $$renderer3.option({ value: 90 }, ($$renderer4) => {
          $$renderer4.push(`90 days`);
        });
        $$renderer3.option({ value: 365 }, ($$renderer4) => {
          $$renderer4.push(`1 year`);
        });
      },
      "svelte-1uha8ag"
    );
    $$renderer2.push(`</label> <button class="ghost svelte-1uha8ag">Refresh</button> <button class="ghost svelte-1uha8ag">Sign out</button></div></header> `);
    if (error) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<p class="err svelte-1uha8ag">${escape_html(error)}</p>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--> `);
    if (loading && !stats) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<p class="muted svelte-1uha8ag">Loading…</p>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--> `);
    if (stats) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<main class="svelte-1uha8ag"><section class="tiles svelte-1uha8ag"><div class="tile svelte-1uha8ag"><span class="k svelte-1uha8ag">Installs active</span><span class="v svelte-1uha8ag">${escape_html(fmt.n(t().installs))}</span><span class="s svelte-1uha8ag">${escape_html(fmt.n(t().session_installs))} started FreeCAD</span></div> <div class="tile svelte-1uha8ag"><span class="k svelte-1uha8ag">Turns</span><span class="v svelte-1uha8ag">${escape_html(fmt.n(t().turns))}</span><span class="s svelte-1uha8ag">${escape_html(fmt.n(t().api_calls))} API calls · ${escape_html(fmt.n(t().tool_calls))} tool calls</span></div> <div class="tile svelte-1uha8ag"><span class="k svelte-1uha8ag">Tokens in / out</span><span class="v svelte-1uha8ag">${escape_html(fmt.k(t().input_tokens))} / ${escape_html(fmt.k(t().output_tokens))}</span><span class="s svelte-1uha8ag">${escape_html(fmt.k(t().tokens_per_turn))} per turn</span></div> <div class="tile svelte-1uha8ag"><span class="k svelte-1uha8ag">Cache hit</span><span class="v svelte-1uha8ag">${escape_html(t().cache_hit_pct)}%</span><span class="s svelte-1uha8ag">${escape_html(fmt.k(t().cache_read_tokens))} read · ${escape_html(fmt.k(t().cache_create_tokens))} written</span></div> <div class="tile svelte-1uha8ag"><span class="k svelte-1uha8ag">Est. API cost</span><span class="v svelte-1uha8ag">${escape_html(fmt.usd(t().est_cost_usd))}</span><span class="s svelte-1uha8ag">list prices, all installs</span></div> <div class="tile svelte-1uha8ag"><span class="k svelte-1uha8ag">Turn latency</span><span class="v svelte-1uha8ag">${escape_html(fmt.ms(t().avg_ms))}</span><span class="s svelte-1uha8ag">avg · max ${escape_html(fmt.ms(t().max_ms))} · CPU avg ${escape_html(fmt.ms(t().avg_cpu_ms))}</span></div> <div${attr_class("tile svelte-1uha8ag", void 0, { "warn": t().hangs > 0 })}><span class="k svelte-1uha8ag">GUI hangs ≥ 2 s</span><span class="v svelte-1uha8ag">${escape_html(fmt.n(t().hangs))}</span><span class="s svelte-1uha8ag">${escape_html(t().turns ? (100 * t().hangs / t().turns).toFixed(1) : 0)}% of turns</span></div> <div${attr_class("tile svelte-1uha8ag", void 0, { "bad": t().errors > 0 })}><span class="k svelte-1uha8ag">Errors</span><span class="v svelte-1uha8ag">${escape_html(fmt.n(t().errors))}</span><span class="s svelte-1uha8ag">${escape_html(t().declined)} of ${escape_html(t().write_calls)} edits declined by users</span></div></section> <section class="grid2 svelte-1uha8ag"><!--[-->`);
      const each_array = ensure_array_like([
        ["turns", "Turns per day"],
        ["out", "Output tokens per day"],
        ["inp", "Uncached input tokens per day"],
        ["hangs", "GUI hangs per day"]
      ]);
      for (let $$index_2 = 0, $$length = each_array.length; $$index_2 < $$length; $$index_2++) {
        let [key, title] = each_array[$$index_2];
        $$renderer2.push(`<figure class="card svelte-1uha8ag"><figcaption class="svelte-1uha8ag">${escape_html(title)}<button class="ghost small svelte-1uha8ag">${escape_html(showTable[key] ? "chart" : "table")}</button></figcaption> `);
        if (showTable[key]) {
          $$renderer2.push("<!--[0-->");
          $$renderer2.push(`<table class="svelte-1uha8ag"><thead><tr><th class="svelte-1uha8ag">Day</th><th class="num svelte-1uha8ag">${escape_html(title)}</th></tr></thead><tbody><!--[-->`);
          const each_array_1 = ensure_array_like(stats.daily);
          for (let $$index = 0, $$length2 = each_array_1.length; $$index < $$length2; $$index++) {
            let r = each_array_1[$$index];
            $$renderer2.push(`<tr><td class="svelte-1uha8ag">${escape_html(r.d)}</td><td class="num svelte-1uha8ag">${escape_html(fmt.n(r[key]))}</td></tr>`);
          }
          $$renderer2.push(`<!--]--></tbody></table>`);
        } else if (stats.daily.length === 0) {
          $$renderer2.push("<!--[1-->");
          $$renderer2.push(`<p class="muted svelte-1uha8ag">No data in range.</p>`);
        } else {
          $$renderer2.push("<!--[-1-->");
          $$renderer2.push(`<div${attr_class("bars svelte-1uha8ag", void 0, { "status": key === "hangs" })}><!--[-->`);
          const each_array_2 = ensure_array_like(bars(stats.daily, key));
          for (let $$index_1 = 0, $$length2 = each_array_2.length; $$index_1 < $$length2; $$index_1++) {
            let b = each_array_2[$$index_1];
            $$renderer2.push(`<div class="bar svelte-1uha8ag"${attr("title", `${b.label}: ${fmt.n(b.v)}`)}><div class="fill svelte-1uha8ag"${attr_style(`height:${stringify(Math.max(b.h, b.v ? 2 : 0))}%`)}></div></div>`);
          }
          $$renderer2.push(`<!--]--></div> <div class="axis svelte-1uha8ag"><span>${escape_html(stats.daily[0].d)}</span><span>${escape_html(stats.daily[stats.daily.length - 1].d)}</span></div>`);
        }
        $$renderer2.push(`<!--]--></figure>`);
      }
      $$renderer2.push(`<!--]--></section> <section class="grid2 svelte-1uha8ag"><figure class="card svelte-1uha8ag"><figcaption class="svelte-1uha8ag">Tools — calls, GUI-thread time, hangs</figcaption> <table class="svelte-1uha8ag"><thead><tr><th class="svelte-1uha8ag">Tool</th><th class="num svelte-1uha8ag">Calls</th><th class="num svelte-1uha8ag">Errors</th><th class="num svelte-1uha8ag">Avg GUI</th><th class="num svelte-1uha8ag">Max GUI</th><th class="num svelte-1uha8ag">Hangs</th></tr></thead><tbody><!--[-->`);
      const each_array_3 = ensure_array_like(stats.tools);
      for (let $$index_3 = 0, $$length = each_array_3.length; $$index_3 < $$length; $$index_3++) {
        let r = each_array_3[$$index_3];
        $$renderer2.push(`<tr${attr_class("svelte-1uha8ag", void 0, { "warnrow": r.hangs > 0 })}><td class="svelte-1uha8ag">${escape_html(r.name)}</td><td class="num svelte-1uha8ag">${escape_html(fmt.n(r.calls))}</td><td class="num svelte-1uha8ag">${escape_html(r.errors)}</td><td class="num svelte-1uha8ag">${escape_html(fmt.ms(r.avg_gui_ms))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.ms(r.max_gui_ms))}</td><td class="num svelte-1uha8ag">${escape_html(r.hangs)}</td></tr>`);
      }
      $$renderer2.push(`<!--]--></tbody></table></figure> <figure class="card svelte-1uha8ag"><figcaption class="svelte-1uha8ag">Models &amp; effort</figcaption> <table class="svelte-1uha8ag"><thead><tr><th class="svelte-1uha8ag">Model</th><th class="num svelte-1uha8ag">Turns</th><th class="num svelte-1uha8ag">In</th><th class="num svelte-1uha8ag">Out</th><th class="num svelte-1uha8ag">Cache read</th><th class="num svelte-1uha8ag">Est. $</th></tr></thead><tbody><!--[-->`);
      const each_array_4 = ensure_array_like(stats.per_model);
      for (let $$index_4 = 0, $$length = each_array_4.length; $$index_4 < $$length; $$index_4++) {
        let m = each_array_4[$$index_4];
        $$renderer2.push(`<tr><td class="svelte-1uha8ag">${escape_html(m.model || "—")}</td><td class="num svelte-1uha8ag">${escape_html(fmt.n(m.n))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(m.inp))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(m.out))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(m.cr))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.usd(m.est_cost_usd))}</td></tr>`);
      }
      $$renderer2.push(`<!--]--></tbody></table> <p class="muted small svelte-1uha8ag">Effort: ${escape_html(Object.entries(stats.effort).map(([k, v]) => `${k} ${v}`).join(" · ") || "—")}
         ·  Latency: ${escape_html(Object.entries(stats.latency_buckets).map(([k, v]) => `${k} ${v}`).join(" · "))}</p> <p class="muted small svelte-1uha8ag">Versions: ${escape_html(stats.versions.map((v) => `${v.plugin_version || "?"} / FreeCAD ${v.freecad_version || "?"} (${v.installs})`).join(" · ") || "—")}</p> `);
      if (stats.errors.length) {
        $$renderer2.push("<!--[0-->");
        $$renderer2.push(`<table class="svelte-1uha8ag"><thead><tr><th class="svelte-1uha8ag">Error</th><th class="num svelte-1uha8ag">Count</th></tr></thead><tbody><!--[-->`);
        const each_array_5 = ensure_array_like(stats.errors);
        for (let $$index_5 = 0, $$length = each_array_5.length; $$index_5 < $$length; $$index_5++) {
          let e = each_array_5[$$index_5];
          $$renderer2.push(`<tr><td class="mono svelte-1uha8ag">${escape_html(e.error)}</td><td class="num svelte-1uha8ag">${escape_html(e.n)}</td></tr>`);
        }
        $$renderer2.push(`<!--]--></tbody></table>`);
      } else {
        $$renderer2.push("<!--[-1-->");
      }
      $$renderer2.push(`<!--]--></figure></section> <section class="card svelte-1uha8ag"><figcaption class="svelte-1uha8ag">Recent turns <span class="filters svelte-1uha8ag"><label><input type="radio"${attr("checked", turnFilter === "", true)} value=""/> all</label> <label><input type="radio"${attr("checked", turnFilter === "&amp;hangs=1", true)} value="&amp;hangs=1"/> hangs</label> <label><input type="radio"${attr("checked", turnFilter === "&amp;errors=1", true)} value="&amp;errors=1"/> errors</label></span></figcaption> <div class="scroll svelte-1uha8ag"><table class="svelte-1uha8ag"><thead><tr><th class="svelte-1uha8ag">When</th><th class="svelte-1uha8ag">Install</th><th class="svelte-1uha8ag">Model</th><th class="svelte-1uha8ag">Effort</th><th class="num svelte-1uha8ag">Objects</th><th class="num svelte-1uha8ag">Prompt</th><th class="num svelte-1uha8ag">In</th><th class="num svelte-1uha8ag">Cache</th><th class="num svelte-1uha8ag">Out</th><th class="num svelte-1uha8ag">API</th><th class="svelte-1uha8ag">Tools</th><th class="num svelte-1uha8ag">Total</th><th class="num svelte-1uha8ag">CPU</th><th class="num svelte-1uha8ag">Max block</th><th class="svelte-1uha8ag">Stop</th><th class="svelte-1uha8ag">Error</th></tr></thead><tbody><!--[-->`);
      const each_array_6 = ensure_array_like(turns);
      for (let $$index_6 = 0, $$length = each_array_6.length; $$index_6 < $$length; $$index_6++) {
        let r = each_array_6[$$index_6];
        $$renderer2.push(`<tr${attr_class("svelte-1uha8ag", void 0, { "warnrow": r.hang, "badrow": r.error })}><td class="svelte-1uha8ag">${escape_html(fmt.ts(r.ts))}</td><td class="mono svelte-1uha8ag">${escape_html(r.install_id)}</td><td class="svelte-1uha8ag">${escape_html(r.model)}</td><td class="svelte-1uha8ag">${escape_html(r.effort)}</td><td class="num svelte-1uha8ag">${escape_html(r.doc_objects ?? "—")}</td><td class="num svelte-1uha8ag">${escape_html(r.prompt_chars ?? "—")}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(r.input_tokens))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(r.cache_read_tokens))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(r.output_tokens))}</td><td class="num svelte-1uha8ag">${escape_html(r.api_calls)}</td><td class="mono small svelte-1uha8ag">${escape_html((r.tools || []).join(", "))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.ms(r.total_ms))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.ms(r.cpu_ms))}</td><td class="num svelte-1uha8ag">${escape_html(fmt.ms(r.max_gui_block_ms))}</td><td class="svelte-1uha8ag">${escape_html(r.stop_reason || "")}${escape_html(r.fallback ? " (fallback)" : "")}</td><td class="mono small svelte-1uha8ag">${escape_html(r.error || "")}</td></tr>`);
      }
      $$renderer2.push(`<!--]--></tbody></table></div></section> <section class="card svelte-1uha8ag"><figcaption class="svelte-1uha8ag">Installs (anonymous ids)</figcaption> <div class="scroll svelte-1uha8ag"><table class="svelte-1uha8ag"><thead><tr><th class="svelte-1uha8ag">Install</th><th class="svelte-1uha8ag">First seen</th><th class="svelte-1uha8ag">Last seen</th><th class="num svelte-1uha8ag">Sessions</th><th class="num svelte-1uha8ag">Turns</th><th class="num svelte-1uha8ag">Tokens</th><th class="num svelte-1uha8ag">Hangs</th><th class="num svelte-1uha8ag">Errors</th><th class="svelte-1uha8ag">Plugin</th><th class="svelte-1uha8ag">FreeCAD</th><th class="svelte-1uha8ag">OS</th></tr></thead><tbody><!--[-->`);
      const each_array_7 = ensure_array_like(installs);
      for (let $$index_7 = 0, $$length = each_array_7.length; $$index_7 < $$length; $$index_7++) {
        let r = each_array_7[$$index_7];
        $$renderer2.push(`<tr><td class="mono svelte-1uha8ag">${escape_html(r.install_id)}</td><td class="svelte-1uha8ag">${escape_html(fmt.ts(r.first_seen))}</td><td class="svelte-1uha8ag">${escape_html(fmt.ts(r.last_seen))}</td><td class="num svelte-1uha8ag">${escape_html(r.sessions)}</td><td class="num svelte-1uha8ag">${escape_html(r.turns)}</td><td class="num svelte-1uha8ag">${escape_html(fmt.k(r.tokens))}</td><td class="num svelte-1uha8ag">${escape_html(r.hangs)}</td><td class="num svelte-1uha8ag">${escape_html(r.errors)}</td><td class="svelte-1uha8ag">${escape_html(r.plugin_version)}</td><td class="svelte-1uha8ag">${escape_html(r.freecad_version)}</td><td class="svelte-1uha8ag">${escape_html(r.os)}</td></tr>`);
      }
      $$renderer2.push(`<!--]--></tbody></table></div></section></main>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]-->`);
  });
}
export {
  _page as default
};
