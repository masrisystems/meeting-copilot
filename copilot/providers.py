"""Cloud and local LLM backends behind one interface: `await provider.complete(system, prompt, schema)`.

- gemini  Google Gemini (google-genai SDK)
- claude  Anthropic Claude (anthropic SDK)
- openai / grok / custom  any OpenAI-compatible Chat Completions API (openai SDK with
          base_url): OpenAI, xAI Grok, OmniRoute, OpenRouter, LiteLLM, LM Studio, ...
- ollama  local Ollama, always available, also the fallback for every other provider
"""

import asyncio
import re

import httpx

from . import config


class Provider:
    id = ""
    label = ""
    model = ""

    @property
    def configured(self):
        return True

    async def complete(self, system, prompt, schema=None):
        raise NotImplementedError

    def retry_after(self, error):
        """Seconds to wait when `error` is a rate-limit/quota error, else None."""
        status = getattr(error, "status_code", None) or getattr(error, "code", None)
        if status != 429:
            return None
        response = getattr(error, "response", None)
        header = response.headers.get("retry-after") if response is not None else None
        return int(float(header)) if header else config.BREAKER_COOLDOWN_S


class GeminiProvider(Provider):
    id, label = "gemini", "Gemini"

    def __init__(self):
        self.model = config.GEMINI_MODEL
        self._thinking = config.GEMINI_THINKING
        self._client = None
        if config.GEMINI_API_KEY:
            from google import genai

            self._client = genai.Client(api_key=config.GEMINI_API_KEY)

    @property
    def configured(self):
        return self._client is not None

    def _config(self, system, schema):
        from google.genai import types

        kwargs = {"system_instruction": system, "temperature": 0.3}
        if schema:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_json_schema"] = schema
        if self._thinking and self._thinking != "off":
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=self._thinking.upper())
        return types.GenerateContentConfig(**kwargs)

    async def complete(self, system, prompt, schema=None):
        # Not streamed: in JSON mode the chunks arrive together at the end anyway.
        from google.genai import errors

        def call():
            return self._client.aio.models.generate_content(
                model=self.model, contents=prompt, config=self._config(system, schema))

        try:
            response = await asyncio.wait_for(call(), config.CLOUD_HARD_TIMEOUT_S)
        except errors.ClientError as e:
            # Some models reject a thinking level (3.8 Flash has no "minimal"): step down once.
            if self._thinking == "off" or "thinking" not in str(e).lower():
                raise
            self._thinking = "low" if self._thinking == "minimal" else "off"
            print(f"{self.model}: thinking level not supported, using '{self._thinking}'")
            response = await asyncio.wait_for(call(), config.CLOUD_HARD_TIMEOUT_S)
        if not response.text:
            raise RuntimeError(f"empty Gemini response ({response.candidates and response.candidates[0].finish_reason})")
        return response.text

    def retry_after(self, error):
        if getattr(error, "code", None) != 429:
            return None
        # Google puts the wait into the error body, up to a day for the free tier.
        match = re.search(r"retryDelay['\"]?:\s*['\"](\d+)", str(error))
        return int(match.group(1)) if match else config.BREAKER_COOLDOWN_S


# Server-side refusal fallback is available on these models (Claude API only).
_CLAUDE_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ClaudeProvider(Provider):
    id, label = "claude", "Claude"

    def __init__(self):
        self.model = config.CLAUDE_MODEL
        self._client = None
        if config.ANTHROPIC_API_KEY:
            import anthropic

            # No SDK retries: the hedge/breaker in llm.py decides what happens on errors.
            self._client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=0)

    @property
    def configured(self):
        return self._client is not None

    async def complete(self, system, prompt, schema=None):
        output_config = {}
        if config.CLAUDE_EFFORT != "off":
            output_config["effort"] = config.CLAUDE_EFFORT
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = dict(model=self.model, max_tokens=4096, system=system,
                      messages=[{"role": "user", "content": prompt}])
        if output_config:
            kwargs["output_config"] = output_config
        if self.model in _CLAUDE_FALLBACK_MODELS:
            # On a policy refusal the API retries the same request on a suitable model.
            response = await asyncio.wait_for(self._client.beta.messages.create(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"),
                config.CLOUD_HARD_TIMEOUT_S)
        else:
            response = await asyncio.wait_for(self._client.messages.create(**kwargs), config.CLOUD_HARD_TIMEOUT_S)
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request")
        return "".join(b.text for b in response.content if b.type == "text")


class OpenAICompatProvider(Provider):
    """Any OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, id, label, base_url, api_key, model, needs_key=True):
        self.id, self.label, self.model = id, label, model
        self._client = None
        self._json_mode = "schema"  # schema -> object -> prompt, downgraded if the endpoint rejects it
        if base_url and model and (api_key or not needs_key):
            import openai

            self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "none", max_retries=0)

    @property
    def configured(self):
        return self._client is not None

    def _response_format(self, schema):
        if not schema or self._json_mode == "prompt":
            return None
        if self._json_mode == "object":
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {"name": "copilot_reply", "schema": schema, "strict": True}}

    async def complete(self, system, prompt, schema=None):
        import openai

        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        while True:
            kwargs = {"model": self.model, "messages": messages}
            fmt = self._response_format(schema)
            if fmt:
                kwargs["response_format"] = fmt
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs), config.CLOUD_HARD_TIMEOUT_S)
                break
            except openai.BadRequestError as e:
                # Gateways and smaller providers often lack json_schema (or JSON mode at all).
                if not fmt or "response_format" not in str(e) and "json" not in str(e).lower():
                    raise
                self._json_mode = "object" if self._json_mode == "schema" else "prompt"
                print(f"{self.label}: structured output not supported, using JSON mode '{self._json_mode}'")
        text = response.choices[0].message.content or ""
        if not text:
            raise RuntimeError(f"empty {self.label} response (finish_reason={response.choices[0].finish_reason})")
        return text


class OllamaProvider(Provider):
    id, label = "ollama", "Ollama (lokal)"

    def __init__(self):
        self.model = config.OLLAMA_MODEL
        self._http = httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT_S)

    async def complete(self, system, prompt, schema=None):
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"temperature": 0.3},
        }
        if schema:
            body["format"] = schema
        r = await self._http.post(f"{config.OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def warmup(self):
        """Load the model into memory so the first fallback isn't a cold start."""
        try:
            await self._http.post(f"{config.OLLAMA_URL}/api/chat",
                                  json={"model": self.model, "messages": [], "keep_alive": "30m"})
        except httpx.HTTPError as e:
            print(f"Ollama warmup failed: {e}")

    async def installed(self):
        """Names of the installed chat models (embedding models skipped); None if Ollama is not running."""
        try:
            r = await self._http.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        return sorted(m["name"] for m in r.json().get("models", []) if "embed" not in m["name"])


def build_providers():
    """All providers in UI order; unconfigured ones (no key) are listed but not selectable."""
    return [
        GeminiProvider(),
        ClaudeProvider(),
        OpenAICompatProvider("openai", "OpenAI", config.OPENAI_BASE_URL, config.OPENAI_API_KEY, config.OPENAI_MODEL),
        OpenAICompatProvider("grok", "Grok", config.GROK_BASE_URL, config.XAI_API_KEY, config.GROK_MODEL),
        OpenAICompatProvider("custom", config.CUSTOM_LABEL, config.CUSTOM_BASE_URL, config.CUSTOM_API_KEY,
                             config.CUSTOM_MODEL, needs_key=False),
        OllamaProvider(),
    ]
