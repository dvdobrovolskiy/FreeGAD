# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""%APPDATA%\\FreeGAD\\config.json

    provider          "anthropic" (default) or "openai" (any OpenAI-compatible API: OpenAI, OpenRouter, ...)
    apiKeyEnc         Anthropic key      | model         Anthropic model
    openaiApiKeyEnc   OpenAI-compat key  | openaiModel   OpenAI-compat model | openaiBaseUrl
    maxTokens, effort, fallbacks (Anthropic only), autoApprove, telemetry, installId, telemetryUrl

Keys are stored DPAPI-encrypted for the current Windows user, never in plain text. A plain-text
"apiKey" (or the installer's apikey.pending file) is migrated to the encrypted field on first read.
If nothing is configured, ANTHROPIC_API_KEY / OPENAI_API_KEY from the environment are used.
"""
import json
import os

from . import dpapi

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-5"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_EFFORT = "high"

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
KNOWN_MODELS = ["claude-opus-5", "claude-fable-5", "claude-sonnet-5", "claude-opus-4-8"]
KNOWN_OPENAI_MODELS = ["gpt-5", "gpt-5-mini", "gpt-4.1", "o3",
                       "anthropic/claude-opus-4.8", "openai/gpt-5", "google/gemini-2.5-pro"]   # OpenRouter-style ids
KNOWN_BASE_URLS = [DEFAULT_OPENAI_BASE_URL, "https://openrouter.ai/api/v1"]


def normalize_provider(p):
    return PROVIDER_OPENAI if str(p or "").strip().lower() == PROVIDER_OPENAI else PROVIDER_ANTHROPIC


def app_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "FreeGAD")


def file_path():
    return os.path.join(app_dir(), "config.json")


def ensure_dirs():
    """Create %APPDATA%/FreeGAD, memory/ and memory/documents/ so every store is ready on first run."""
    for d in (app_dir(), os.path.join(app_dir(), "memory"), os.path.join(app_dir(), "memory", "documents")):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def _read_json():
    try:
        with open(file_path(), "r", encoding="utf-8") as f:
            o = json.load(f)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _template():
    return {
        "provider": PROVIDER_ANTHROPIC,
        "apiKeyEnc": "",
        "model": DEFAULT_MODEL,
        "openaiApiKeyEnc": "",
        "openaiModel": DEFAULT_OPENAI_MODEL,
        "openaiBaseUrl": DEFAULT_OPENAI_BASE_URL,
        "maxTokens": DEFAULT_MAX_TOKENS,
        "effort": DEFAULT_EFFORT,
        "fallbacks": True,
        "autoApprove": False,
        "telemetry": True,
        "installId": "",
        "telemetryUrl": "",
    }


def pending_key_path():
    return os.path.join(app_dir(), "apikey.pending")


def _take_pending_key():
    """The installer drops the key in a plain file - either a bare Anthropic key (old format) or JSON
    {"provider": .., "apiKey": .., "baseUrl": ..}. Returns (provider, key, base_url) once, then deletes it."""
    p = pending_key_path()
    try:
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            text = f.read().strip()
        try:
            os.remove(p)
        except Exception:
            pass
    except Exception:
        return None
    if not text:
        return None
    if text.startswith("{"):
        try:
            j = json.loads(text)
        except Exception:
            return None
        key = str(j.get("apiKey") or "").strip()
        if not key:
            return None
        return normalize_provider(j.get("provider")), key, str(j.get("baseUrl") or "").strip() or None
    return PROVIDER_ANTHROPIC, text, None


def _write(o):
    os.makedirs(app_dir(), exist_ok=True)
    tmp = file_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, indent=2)
    os.replace(tmp, file_path())


def _decrypt(enc):
    """(key, source) for a stored blob; (None, None) if empty or written by another Windows user."""
    enc = (enc or "").strip()
    if not enc:
        return None, None
    try:
        key = dpapi.unprotect(enc)
        return (key, "config") if key else (None, None)
    except Exception:
        return None, None


class Config:
    def __init__(self):
        self.provider = PROVIDER_ANTHROPIC
        self.anthropic_key = None
        self.anthropic_key_source = None      # "config", "env" or None
        self.openai_key = None
        self.openai_key_source = None
        self.anthropic_model = DEFAULT_MODEL
        self.openai_model = DEFAULT_OPENAI_MODEL
        self.openai_base_url = DEFAULT_OPENAI_BASE_URL
        self.max_tokens = DEFAULT_MAX_TOKENS
        self.effort = DEFAULT_EFFORT
        self.fallbacks = True
        self.auto_approve = False
        self.telemetry = True
        self.install_id = ""
        self.telemetry_url = ""

    # ---- views of the ACTIVE provider (what the rest of the addon uses)
    @property
    def is_openai(self):
        return self.provider == PROVIDER_OPENAI

    @property
    def provider_label(self):
        return "OpenAI-compatible" if self.is_openai else "Anthropic"

    @property
    def env_var_name(self):
        return "OPENAI_API_KEY" if self.is_openai else "ANTHROPIC_API_KEY"

    @property
    def api_key(self):
        return self.openai_key if self.is_openai else self.anthropic_key

    @property
    def api_key_source(self):
        return self.openai_key_source if self.is_openai else self.anthropic_key_source

    @property
    def model(self):
        return self.openai_model if self.is_openai else self.anthropic_model

    @property
    def has_api_key(self):
        return bool(self.api_key and self.api_key.strip())

    def key_for(self, provider):
        """(key, source) of one provider, regardless of which is active."""
        if normalize_provider(provider) == PROVIDER_OPENAI:
            return self.openai_key, self.openai_key_source
        return self.anthropic_key, self.anthropic_key_source

    @staticmethod
    def load():
        c = Config()
        ensure_dirs()
        o = _read_json()
        if o is None:
            o = _template()
            try:
                _write(o)
            except Exception:
                pass

        c.provider = normalize_provider(o.get("provider"))
        c.anthropic_model = str(o.get("model") or DEFAULT_MODEL)
        c.openai_model = str(o.get("openaiModel") or DEFAULT_OPENAI_MODEL)
        c.openai_base_url = str(o.get("openaiBaseUrl") or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL
        try:
            c.max_tokens = int(o.get("maxTokens") or DEFAULT_MAX_TOKENS)
        except Exception:
            c.max_tokens = DEFAULT_MAX_TOKENS
        c.effort = str(o.get("effort") or DEFAULT_EFFORT)
        if c.effort not in EFFORTS:
            c.effort = DEFAULT_EFFORT
        c.fallbacks = bool(o.get("fallbacks", True))
        c.auto_approve = bool(o.get("autoApprove", False))
        c.telemetry = bool(o.get("telemetry", True))
        c.telemetry_url = str(o.get("telemetryUrl") or "")
        c.install_id = str(o.get("installId") or "")
        if not c.install_id:
            # random, not derived from anything identifying; only used to group events
            import uuid
            c.install_id = uuid.uuid4().hex
            o["installId"] = c.install_id
            try:
                _write(o)
            except Exception:
                pass

        # Installer hand-off: may also switch the provider and base URL.
        pending = _take_pending_key()
        if pending:
            provider, key, base_url = pending
            try:
                save_api_key(key, provider, base_url=base_url)
                c.provider = provider
                if provider == PROVIDER_OPENAI:
                    c.openai_key, c.openai_key_source = key, "config"
                    if base_url:
                        c.openai_base_url = base_url.rstrip("/")
                else:
                    c.anthropic_key, c.anthropic_key_source = key, "config"
            except Exception:
                pass

        # Legacy plain-text "apiKey": take it, then re-save encrypted.
        legacy = (o.get("apiKey") or "").strip()
        if legacy:
            c.anthropic_key, c.anthropic_key_source = legacy, "config"
            try:
                save_api_key(legacy, PROVIDER_ANTHROPIC, keep_provider=True)
            except Exception:
                pass

        if c.anthropic_key is None:
            c.anthropic_key, c.anthropic_key_source = _decrypt(o.get("apiKeyEnc"))
        if c.openai_key is None:
            c.openai_key, c.openai_key_source = _decrypt(o.get("openaiApiKeyEnc"))

        if not c.anthropic_key:
            env = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
            if env:
                c.anthropic_key, c.anthropic_key_source = env, "env"
        if not c.openai_key:
            env = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if env:
                c.openai_key, c.openai_key_source = env, "env"
        return c


def _key_field(provider):
    return "openaiApiKeyEnc" if normalize_provider(provider) == PROVIDER_OPENAI else "apiKeyEnc"


def save_api_key(api_key, provider=PROVIDER_ANTHROPIC, base_url=None, keep_provider=False):
    """Encrypt and store a key for one provider and make that provider active (unless keep_provider)."""
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("API key is empty.")
    provider = normalize_provider(provider)
    o = _read_json() or _template()
    o.pop("apiKey", None)
    o[_key_field(provider)] = dpapi.protect(api_key)
    if not keep_provider:
        o["provider"] = provider
    if base_url and provider == PROVIDER_OPENAI:
        o["openaiBaseUrl"] = base_url.strip().rstrip("/")
    _write(o)


def clear_api_key(provider=None):
    """Remove one provider's stored key, or both when provider is None."""
    o = _read_json()
    if o is None:
        return
    o.pop("apiKey", None)
    if provider is None:
        o.pop("apiKeyEnc", None)
        o.pop("openaiApiKeyEnc", None)
    else:
        o.pop(_key_field(provider), None)
    _write(o)


def save_settings(provider=None, model=None, openai_model=None, openai_base_url=None, max_tokens=None,
                  effort=None, fallbacks=None, auto_approve=None, telemetry=None):
    o = _read_json() or _template()
    if provider is not None:
        o["provider"] = normalize_provider(provider)
    if model is not None:
        o["model"] = model
    if openai_model is not None:
        o["openaiModel"] = openai_model
    if openai_base_url is not None:
        o["openaiBaseUrl"] = openai_base_url.strip().rstrip("/")
    if max_tokens is not None:
        o["maxTokens"] = int(max_tokens)
    if effort is not None:
        o["effort"] = effort
    if fallbacks is not None:
        o["fallbacks"] = bool(fallbacks)
    if auto_approve is not None:
        o["autoApprove"] = bool(auto_approve)
    if telemetry is not None:
        o["telemetry"] = bool(telemetry)
    _write(o)
