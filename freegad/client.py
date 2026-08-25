# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Minimal raw-HTTP LLM clients (stdlib only).

FreeCAD ships its own embedded Python, where installing SDKs is unreliable, so - like AutoGAD -
the addon talks to the APIs directly with urllib.

The agent speaks the Anthropic Messages shape everywhere (system blocks, tool_use / tool_result
blocks, usage.input_tokens ...). ClaudeClient sends that as-is; OpenAIClient translates it to the
OpenAI Chat Completions shape (OpenAI, OpenRouter, any compatible server) and the response back,
so the agent loop is provider-agnostic. make_client(cfg) picks the one the config selects.
"""
import json
import urllib.error
import urllib.request
import uuid

from . import config as config_mod

API_URL = "https://api.anthropic.com/v1/messages"
MODELS_URL = "https://api.anthropic.com/v1/models"
API_VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
TIMEOUT_S = 600


class ApiError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        msg = body
        try:
            j = json.loads(body)
            err = j.get("error")
            msg = (err.get("message") if isinstance(err, dict) else err) or body
        except Exception:
            pass
        super().__init__(f"API {status}: {msg}")


def _post(url, headers, body, timeout=TIMEOUT_S):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            txt = e.read().decode("utf-8", "replace")
        except Exception:
            txt = str(e)
        raise ApiError(e.code, txt)


def _with_cache_breakpoint(messages):
    """Copy of `messages` with cache_control on the last block of the last message.

    The system prompt carries one breakpoint (document snapshot); this adds a second on the
    conversation tail, so each tool-use iteration re-reads the growing history from cache
    (10% of the input price) instead of paying full price for it again."""
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        content = [dict(b) for b in content]
    else:
        return messages
    if content:
        content[-1] = dict(content[-1])
        content[-1]["cache_control"] = {"type": "ephemeral"}
    last["content"] = content
    out[-1] = last
    return out


# ------------------------------------------------------------------ Anthropic

class ClaudeClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def _headers(self):
        h = {"x-api-key": self.cfg.api_key, "anthropic-version": API_VERSION}
        if self.cfg.fallbacks:
            h["anthropic-beta"] = FALLBACK_BETA
        return h

    def create_message(self, system, messages, tools):
        body = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "system": system,
            "messages": _with_cache_breakpoint(messages),
            "tools": tools,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.cfg.effort},
        }
        if self.cfg.fallbacks:
            # Server-side refusal fallback: if a safety classifier declines the request,
            # Anthropic re-runs it on the recommended substitute model instead of refusing.
            body["fallbacks"] = "default"
        try:
            return _post(API_URL, self._headers(), body)
        except ApiError as e:
            raise Exception("Claude " + str(e)) from None


# ------------------------------------------------------------------ OpenAI-compatible

def _map_effort(effort):
    """Anthropic effort levels -> OpenAI reasoning_effort (low / medium / high)."""
    return effort if effort in ("low", "medium") else "high"


def _image_part(block):
    src = (block or {}).get("source") or {}
    if src.get("type") == "url":
        url = src.get("url")
    else:
        url = "data:%s;base64,%s" % (src.get("media_type") or "image/png", src.get("data") or "")
    return {"type": "image_url", "image_url": {"url": url}}


def _translate_message(m, out):
    role = m.get("role")
    content = m.get("content")
    if role == "user":
        if isinstance(content, str):
            out.append({"role": "user", "content": content})
            return
        parts = []          # text/image parts that follow the tool results
        for block in content or []:
            t = block.get("type")
            if t == "tool_result":
                text, images = "", []
                rc = block.get("content")
                if isinstance(rc, str):
                    text = rc
                else:
                    for rb in rc or []:
                        if rb.get("type") == "text":
                            text += rb.get("text") or ""
                        elif rb.get("type") == "image":
                            images.append(_image_part(rb))
                if images:
                    text += " [image attached in the next message]"
                out.append({"role": "tool", "tool_call_id": block.get("tool_use_id"), "content": text})
                parts.extend(images)
            elif t == "text":
                parts.append({"type": "text", "text": block.get("text") or ""})
            elif t == "image":
                parts.append(_image_part(block))
        if len(parts) == 1 and parts[0].get("type") == "text":
            out.append({"role": "user", "content": parts[0]["text"]})
        elif parts:
            out.append({"role": "user", "content": parts})
    elif role == "assistant":
        text, calls = "", []
        for block in content or []:
            t = block.get("type")
            if t == "text":
                text += block.get("text") or ""
            elif t == "tool_use":
                calls.append({"id": block.get("id"), "type": "function",
                              "function": {"name": block.get("name"),
                                           "arguments": json.dumps(block.get("input") or {})}})
            # thinking / fallback blocks have no OpenAI equivalent and are dropped
        msg = {"role": "assistant", "content": text if (text or not calls) else None}
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)


def _translate_response(resp):
    choice = ((resp.get("choices") or [{}])[0]) or {}
    msg = choice.get("message") or {}
    content = []

    text = msg.get("content")
    if isinstance(text, list):
        text = "".join(p.get("text") or "" for p in text if p.get("type") == "text")
    refusal = msg.get("refusal")
    if refusal:
        text = (text or "") + refusal
    if text:
        content.append({"type": "text", "text": text})

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        try:
            inp = json.loads(args) if args and args.strip() else {}
            if not isinstance(inp, dict):
                inp = {}
        except Exception:
            inp = {"_unparsed_arguments": args}
        content.append({"type": "tool_use", "id": tc.get("id") or "call_" + uuid.uuid4().hex[:12],
                        "name": fn.get("name"), "input": inp})

    finish = choice.get("finish_reason")
    stop = {"tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}.get(finish)
    if stop is None:
        stop = "refusal" if refusal else "end_turn"

    u = resp.get("usage") or {}
    prompt = int(u.get("prompt_tokens") or 0)
    cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    usage = {"input_tokens": max(0, prompt - cached), "output_tokens": int(u.get("completion_tokens") or 0),
             "cache_read_input_tokens": cached, "cache_creation_input_tokens": 0}
    if u.get("cost") is not None:          # OpenRouter reports the real charge
        try:
            usage["cost_usd"] = float(u["cost"])
        except Exception:
            pass

    out = {"content": content, "stop_reason": stop, "model": resp.get("model"), "usage": usage}
    if stop == "refusal":
        out["stop_details"] = {"type": "refusal", "explanation": refusal or "content filter"}
    return out


class OpenAIClient:
    def __init__(self, cfg):
        self.cfg = cfg
        base = (cfg.openai_base_url or config_mod.DEFAULT_OPENAI_BASE_URL).rstrip("/")
        self.url = base + "/chat/completions"
        self.openrouter = "openrouter.ai" in base.lower()
        self.no_reasoning = False                          # server rejected reasoning_effort
        self.use_max_tokens = "api.openai.com" not in base.lower()   # vs max_completion_tokens

    def _body(self, system, messages, tools):
        msgs = []
        sys_text = "\n\n".join(b.get("text") for b in system if b.get("text"))
        if sys_text:
            msgs.append({"role": "system", "content": sys_text})
        for m in messages:
            _translate_message(m, msgs)
        body = {"model": self.cfg.model, "messages": msgs,
                ("max_tokens" if self.use_max_tokens else "max_completion_tokens"): self.cfg.max_tokens}
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": t.get("name"), "description": t.get("description") or "",
                                           "parameters": t.get("input_schema") or {"type": "object"}}}
                             for t in tools]
        if not self.no_reasoning:
            body["reasoning_effort"] = _map_effort(self.cfg.effort)
        if self.openrouter:
            body["usage"] = {"include": True}        # returns usage.cost in USD
        return body

    def create_message(self, system, messages, tools):
        headers = {"Authorization": "Bearer " + (self.cfg.api_key or "")}
        if self.openrouter:
            headers["X-Title"] = "FreeGAD"
        for attempt in range(3):
            try:
                return _translate_response(_post(self.url, headers, self._body(system, messages, tools)))
            except ApiError as e:
                low = str(e).lower()
                if e.status == 400 and attempt < 2:
                    # Compatible servers differ in which parameters they accept; adapt and retry.
                    if not self.no_reasoning and "reasoning" in low:
                        self.no_reasoning = True
                        continue
                    if "max_tokens" in low or "max_completion_tokens" in low:
                        self.use_max_tokens = not self.use_max_tokens
                        continue
                raise Exception(("OpenRouter " if self.openrouter else "OpenAI-compatible ") + str(e)) from None


def make_client(cfg):
    return OpenAIClient(cfg) if cfg.is_openai else ClaudeClient(cfg)


def verify_api_key(api_key, provider=config_mod.PROVIDER_ANTHROPIC, base_url=None, timeout=20):
    """Cheap key check: GET /models. Returns (ok, message)."""
    if config_mod.normalize_provider(provider) == config_mod.PROVIDER_OPENAI:
        url = (base_url or config_mod.DEFAULT_OPENAI_BASE_URL).rstrip("/") + "/models"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", "Bearer " + api_key)
    else:
        req = urllib.request.Request(MODELS_URL + "?limit=1", method="GET")
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", API_VERSION)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True, "API key accepted."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "API key rejected (HTTP %d)." % e.code
        return False, "Unexpected HTTP %d while verifying the key." % e.code
    except Exception as ex:
        return False, "Could not reach the API: %s" % ex
