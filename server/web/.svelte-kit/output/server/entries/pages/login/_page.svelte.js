import { c as attr, e as escape_html } from "../../../chunks/root.js";
import "@sveltejs/kit/internal";
import "../../../chunks/exports.js";
import "../../../chunks/utils2.js";
import "@sveltejs/kit/internal/server";
import "../../../chunks/state.svelte.js";
function _page($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    let username = "admin";
    let password = "";
    let busy = false;
    $$renderer2.push(`<main class="wrap svelte-1x05zx6"><form class="card svelte-1x05zx6"><h1 class="svelte-1x05zx6">FreeGAD telemetry</h1> <p class="muted svelte-1x05zx6">Sign in to see usage statistics.</p> <label class="svelte-1x05zx6">Username <input${attr("value", username)} autocomplete="username" class="svelte-1x05zx6"/></label> <label class="svelte-1x05zx6">Password <input type="password"${attr("value", password)} autocomplete="current-password" class="svelte-1x05zx6"/></label> `);
    {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--> <button${attr("disabled", busy, true)} class="svelte-1x05zx6">${escape_html("Sign in")}</button></form></main>`);
  });
}
export {
  _page as default
};
