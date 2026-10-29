"""Universal LLM client for translation and script stages.

Talks to any OpenAI-compatible server (LM Studio, llama.cpp, vLLM, KoboldCpp,
Jan, OpenRouter, OpenAI, DeepSeek, Groq, Gemini, ...) or to Ollama's native
API. Model management (load/unload to free VRAM) is an optional capability of
a provider: LM Studio and Ollama support it, plain OpenAI-compatible servers
just receive requests.

Providers are presets in config/llm_providers.json. API keys are never passed
on the command line: they come from DUB_LLM_API_KEY, the provider's own env var
(e.g. OPENROUTER_API_KEY) or config/secrets.local.json.

CLI check:
  python llm_client.py --llm-provider lmstudio --list-models
  python llm_client.py --llm-provider ollama --model qwen3:8b --test "Say hi"
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
PROVIDERS_FILE = ROOT / "config" / "llm_providers.json"
SECRETS_FILE = ROOT / "config" / "secrets.local.json"
API_KEY_ENV = "DUB_LLM_API_KEY"
DEFAULT_PROVIDER = "lmstudio"

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """Connection, HTTP or empty-response error from the LLM backend."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Presets and secrets


def load_providers():
    data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
    return {item["id"]: item for item in data.get("providers") or []}


def provider_preset(provider_id):
    providers = load_providers()
    provider_id = str(provider_id or DEFAULT_PROVIDER).strip().lower()
    if provider_id in providers:
        return providers[provider_id]
    custom = dict(providers.get("custom") or {"id": "custom", "api": "openai"})
    custom["id"] = provider_id or "custom"
    return custom


def load_secrets():
    if not SECRETS_FILE.is_file():
        return {}
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_api_key(provider_id, api_key):
    secrets = load_secrets()
    keys = secrets.setdefault("llm_api_keys", {})
    if api_key:
        keys[provider_id] = api_key
    else:
        keys.pop(provider_id, None)
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(json.dumps(secrets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stored_api_key(provider_id):
    return str((load_secrets().get("llm_api_keys") or {}).get(provider_id) or "")


def resolve_api_key(preset, explicit=""):
    if explicit:
        return explicit
    if os.environ.get(API_KEY_ENV):
        return os.environ[API_KEY_ENV]
    key_env = preset.get("key_env")
    if key_env and os.environ.get(key_env):
        return os.environ[key_env]
    return stored_api_key(preset.get("id", ""))


def mask_key(api_key):
    if not api_key:
        return ""
    return "••••" + api_key[-4:] if len(api_key) > 8 else "••••"


# --------------------------------------------------------------------------
# HTTP


def http_json(method, url, payload=None, timeout=120, headers=None):
    data = None
    all_headers = {"Content-Type": "application/json"}
    all_headers.update(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method, headers=all_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:600]
        except OSError:
            pass
        raise LLMError(f"HTTP {exc.code} from {url}: {body or exc.reason}", status=exc.code) from exc
    except (URLError, TimeoutError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise LLMError(f"Cannot reach {url}: {reason}") from exc


def configure_stdio():
    """UTF-8 logs even when Windows defaults pipes to the ANSI code page."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def strip_reasoning(text):
    text = THINK_BLOCK_RE.sub("", str(text or ""))
    # An unclosed <think> means the model ran out of tokens while reasoning.
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


def message_text(message):
    content = (message or {}).get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def normalized_words(value):
    return [part for part in re.split(r"[^a-z0-9]+", str(value).lower()) if part]


# --------------------------------------------------------------------------
# Client


class LLMClient:
    def __init__(
        self,
        provider=DEFAULT_PROVIDER,
        base_url="",
        model="",
        api_key="",
        context_length=8192,
        auto_load=True,
        unload_others=True,
        unload_when_done=False,
        timeout=120,
        model_timeout=600,
        lmstudio_options=None,
        log=None,
    ):
        self.preset = provider_preset(provider)
        self.provider = self.preset.get("id", "custom")
        self.label = self.preset.get("label", self.provider)
        self.base_url = str(base_url or self.preset.get("base_url") or "").strip().rstrip("/")
        if not self.base_url:
            raise LLMError(f"No server URL set for provider '{self.provider}'.")
        self.api = self.preset.get("api", "openai")
        self.manage = self.preset.get("manage")
        self.model = str(model or "").strip()
        self.api_key = resolve_api_key(self.preset, api_key)
        self.context_length = int(context_length or 0)
        self.auto_load = bool(auto_load)
        self.unload_others = bool(unload_others)
        self.unload_when_done = bool(unload_when_done)
        self.timeout = int(timeout or 120)
        self.model_timeout = int(model_timeout or 600)
        self.lmstudio_options = lmstudio_options or {}
        self.log = log or (lambda message: print(message, flush=True))
        self.active_model = ""
        self._json_mode_supported = True
        self._schema_supported = True

    # -- urls / headers ------------------------------------------------------

    @property
    def native_base(self):
        """Server root without the OpenAI /v1 suffix (LM Studio / Ollama APIs)."""
        base = self.base_url
        return base[:-3] if base.endswith("/v1") else base

    @property
    def openai_base(self):
        base = self.base_url
        if self.api == "ollama" and not base.endswith("/v1"):
            base += "/v1"
        return base

    def headers(self):
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.preset.get("extra_headers") or {})
        return headers

    def describe(self):
        return f"{self.label} / {self.active_model or self.model or 'model not selected'}"

    # -- discovery -----------------------------------------------------------

    def list_models(self):
        """Return [{"id", "name", "loaded", "details"}] for the UI and auto-pick."""
        if self.manage == "lmstudio":
            return [
                {
                    "id": model.get("key"),
                    "name": model.get("display_name") or model.get("key"),
                    "loaded": bool(model.get("loaded_instances")),
                    "details": " · ".join(
                        part for part in (
                            (model.get("quantization") or {}).get("name", ""),
                            model.get("params_string") or "",
                        ) if part
                    ),
                }
                for model in self._lmstudio_models()
            ]
        if self.api == "ollama":
            tags = http_json("GET", self.native_base + "/api/tags", timeout=self.timeout, headers=self.headers())
            loaded = set()
            try:
                running = http_json("GET", self.native_base + "/api/ps", timeout=self.timeout, headers=self.headers())
                loaded = {item.get("name") for item in running.get("models") or []}
            except LLMError:
                pass
            return [
                {
                    "id": item.get("name"),
                    "name": item.get("name"),
                    "loaded": item.get("name") in loaded,
                    "details": (item.get("details") or {}).get("parameter_size", ""),
                }
                for item in tags.get("models") or []
            ]
        data = http_json("GET", self.openai_base + "/models", timeout=self.timeout, headers=self.headers())
        return [
            {"id": item.get("id"), "name": item.get("id"), "loaded": False, "details": item.get("owned_by", "")}
            for item in data.get("data") or []
            if isinstance(item, dict) and item.get("id")
        ]

    def prepare(self):
        """Make sure a model is ready and return the id used in requests."""
        if self.manage == "lmstudio" and self.auto_load:
            self.active_model = self._lmstudio_ensure()
        elif self.model:
            self.active_model = self.model
        else:
            models = self.list_models()
            chosen = next((item for item in models if item["loaded"]), None) or (models[0] if models else None)
            if not chosen:
                raise LLMError(f"{self.label}: no models available. Choose a model in settings.")
            self.active_model = chosen["id"]
        self.log(f"LLM: {self.describe()}")
        return self.active_model

    # -- generation ----------------------------------------------------------

    def complete(self, messages, temperature=0.2, timeout=None, json_mode=False, max_tokens=None, schema=None):
        """Send a chat request and return the assistant text (reasoning stripped).

        schema: optional JSON Schema (root must be an object). Servers that support
        structured output (LM Studio, llama.cpp, vLLM, Ollama, OpenAI, ...) then
        constrain decoding, so even small models return valid JSON. Servers that
        reject it are remembered and get a plain request instead.
        """
        model = self.active_model or self.prepare()
        timeout = int(timeout or self.timeout)
        if self.api == "ollama":
            content = self._ollama_chat(model, messages, temperature, timeout, json_mode, max_tokens, schema)
        else:
            content = self._openai_chat(model, messages, temperature, timeout, json_mode, max_tokens, schema)
        content = strip_reasoning(content)
        if not content:
            raise LLMError("Model returned an empty response.")
        return content

    def _openai_chat(self, model, messages, temperature, timeout, json_mode, max_tokens, schema):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        formats = []
        if schema and self._schema_supported:
            formats.append(("schema", {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            }))
        if (json_mode or schema) and self._json_mode_supported:
            formats.append(("json", {"type": "json_object"}))
        formats.append(("plain", None))

        url = self.openai_base + "/chat/completions"
        for kind, response_format in formats:
            if response_format:
                payload["response_format"] = response_format
            else:
                payload.pop("response_format", None)
            try:
                data = http_json("POST", url, payload=payload, timeout=timeout, headers=self.headers())
                break
            except LLMError as exc:
                if kind == "plain" or exc.status not in (400, 404, 415, 422, 500):
                    raise
                # Remember that this server rejects the format and fall back.
                if kind == "schema":
                    self._schema_supported = False
                else:
                    self._json_mode_supported = False
        try:
            return message_text(data["choices"][0]["message"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response from {url}: {str(data)[:300]}") from exc

    def _ollama_chat(self, model, messages, temperature, timeout, json_mode, max_tokens, schema):
        options = {"temperature": temperature}
        if self.context_length:
            options["num_ctx"] = self.context_length
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        payload = {"model": model, "messages": messages, "stream": False, "options": options}
        if schema:
            payload["format"] = schema
        elif json_mode:
            payload["format"] = "json"
        data = http_json("POST", self.native_base + "/api/chat", payload=payload, timeout=timeout, headers=self.headers())
        return message_text(data.get("message"))

    # -- lifecycle -----------------------------------------------------------

    def finish(self):
        """Unload the model if requested (frees VRAM for the TTS stage)."""
        if not self.unload_when_done or not self.active_model:
            return
        try:
            if self.manage == "lmstudio":
                self.log(f"Unloading LM Studio model: {self.active_model}")
                self._lmstudio_unload(self.active_model)
            elif self.manage == "ollama":
                self.log(f"Unloading Ollama model: {self.active_model}")
                http_json(
                    "POST",
                    self.native_base + "/api/generate",
                    payload={"model": self.active_model, "keep_alive": 0},
                    timeout=self.model_timeout,
                    headers=self.headers(),
                )
            else:
                self.log(f"{self.label} cannot unload models remotely; free VRAM manually if needed.")
        except LLMError as exc:
            self.log(f"WARNING: could not unload model: {exc}")

    def unload_all(self):
        """Unload every loaded model of a managed provider (UI button)."""
        if self.manage == "lmstudio":
            for model in self._lmstudio_models():
                for instance in model.get("loaded_instances") or []:
                    if instance.get("id"):
                        self._lmstudio_unload(instance["id"])
        elif self.manage == "ollama":
            for item in self.list_models():
                if item["loaded"]:
                    http_json(
                        "POST",
                        self.native_base + "/api/generate",
                        payload={"model": item["id"], "keep_alive": 0},
                        timeout=self.model_timeout,
                        headers=self.headers(),
                    )

    # -- LM Studio management --------------------------------------------------

    def _lmstudio_models(self):
        data = http_json("GET", self.native_base + "/api/v1/models", timeout=self.timeout, headers=self.headers())
        return [model for model in data.get("models") or [] if model.get("type") == "llm"]

    def _lmstudio_unload(self, instance_id):
        http_json(
            "POST",
            self.native_base + "/api/v1/models/unload",
            payload={"instance_id": instance_id},
            timeout=self.model_timeout,
            headers=self.headers(),
        )

    def _lmstudio_find(self, models, requested):
        for model in models:
            variants = model.get("variants") or []
            if requested in (model.get("key"), model.get("selected_variant")) or requested in variants:
                return model
        requested_words = normalized_words(requested)
        if not requested_words:
            return None
        best, best_score = None, 0
        for model in models:
            haystack = " ".join(
                str(model.get(name) or "") for name in ("key", "display_name", "publisher", "params_string")
            ).lower()
            score = sum(1 for word in requested_words if word in haystack)
            if requested.lower() in haystack:
                score += len(requested_words) + 4
            if score > best_score:
                best, best_score = model, score
        return best

    def _lmstudio_ensure(self):
        models = self._lmstudio_models()
        if not self.model:
            loaded = next((model for model in models if model.get("loaded_instances")), None)
            if loaded:
                return loaded["loaded_instances"][0]["id"]
            raise LLMError("LM Studio: no model selected and none is loaded. Choose a model in settings.")

        target = self._lmstudio_find(models, self.model)
        if not target:
            available = ", ".join(str(model.get("key")) for model in models)
            raise LLMError(f"LM Studio model not found: {self.model}. Available: {available}")

        loaded_instances = target.get("loaded_instances") or []
        if loaded_instances:
            instance_id = loaded_instances[0]["id"]
            self.log(f"LM Studio model already loaded: {instance_id}")
        else:
            if self.unload_others:
                self._lmstudio_unload_others(keep=set())
            self.log(f"Loading LM Studio model: {target['key']}")
            payload = {"model": target["key"], "echo_load_config": True}
            if self.context_length:
                payload["context_length"] = self.context_length
            payload.update({key: value for key, value in self.lmstudio_options.items() if value is not None})
            result = http_json(
                "POST",
                self.native_base + "/api/v1/models/load",
                payload=payload,
                timeout=self.model_timeout,
                headers=self.headers(),
            )
            instance_id = result.get("instance_id") or target["key"]
            self.log(f"Loaded LM Studio model: {instance_id}")

        if self.unload_others:
            self._lmstudio_unload_others(keep={instance_id})
        return instance_id

    def _lmstudio_unload_others(self, keep):
        for model in self._lmstudio_models():
            for instance in model.get("loaded_instances") or []:
                instance_id = instance.get("id")
                if instance_id and instance_id not in keep:
                    self.log(f"Unloading other LM Studio model: {instance_id}")
                    self._lmstudio_unload(instance_id)


# --------------------------------------------------------------------------
# Shared CLI arguments for all LLM-based scripts


def add_llm_arguments(parser):
    group = parser.add_argument_group("LLM")
    group.add_argument(
        "--llm-provider",
        default=os.environ.get("DUB_LLM_PROVIDER", DEFAULT_PROVIDER),
        help="Preset id from config/llm_providers.json (lmstudio, ollama, openrouter, custom, ...).",
    )
    group.add_argument("--llm-url", "--lm-studio-url", dest="llm_url", default="", help="Server URL. Empty = preset URL.")
    group.add_argument("--model", "--llm-model", dest="model", default="", help="Model id. Empty = loaded/first model.")
    group.add_argument("--no-auto-load-model", action="store_true", help="Do not load/unload models (LM Studio).")
    group.add_argument(
        "--unload-other-models", "--unload-other-lm-studio-models",
        dest="unload_other_models", action="store_true", default=True,
    )
    group.add_argument(
        "--keep-other-models", "--keep-other-lm-studio-models",
        dest="unload_other_models", action="store_false",
    )
    group.add_argument(
        "--unload-model-when-done", "--unload-lm-studio-model-when-done",
        dest="unload_model_when_done", action="store_true",
    )
    group.add_argument("--model-timeout", type=int, default=600)
    group.add_argument("--lm-context-length", "--llm-context-length", dest="lm_context_length", type=int, default=8192)
    group.add_argument("--lm-eval-batch-size", type=int, default=0)
    group.add_argument("--lm-flash-attention", action=argparse.BooleanOptionalAction, default=True)
    group.add_argument("--lm-offload-kv-cache-to-gpu", action=argparse.BooleanOptionalAction, default=True)
    return group


def client_from_args(args, model=None):
    return LLMClient(
        provider=args.llm_provider,
        base_url=args.llm_url,
        model=args.model if model is None else model,
        context_length=args.lm_context_length,
        auto_load=not args.no_auto_load_model,
        unload_others=args.unload_other_models,
        unload_when_done=args.unload_model_when_done,
        timeout=getattr(args, "timeout", 120),
        model_timeout=args.model_timeout,
        lmstudio_options={
            "eval_batch_size": args.lm_eval_batch_size or None,
            "flash_attention": args.lm_flash_attention,
            "offload_kv_cache_to_gpu": args.lm_offload_kv_cache_to_gpu,
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Check an LLM provider: list models or send a test prompt.")
    add_llm_arguments(parser)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--test", default="", help="Prompt to send.")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    client = client_from_args(args)
    if args.list_models or not args.test:
        for item in client.list_models():
            mark = "*" if item["loaded"] else " "
            print(f"{mark} {item['id']}  {item['details']}")
    if args.test:
        started = time.time()
        client.prepare()
        print(client.complete([{"role": "user", "content": args.test}], temperature=0.2))
        print(f"({time.time() - started:.1f}s)")
        client.finish()


if __name__ == "__main__":
    main()
