"""LLM calls, with every response cached to disk.

Two reasons the cache is not optional here:

* Cost. A full dev-set run is ~1034 calls, and the ablation re-runs the same
  questions for every configuration. Without a cache, re-running to check a
  number means paying for it again.
* Reproducibility. temperature=0 makes a model *mostly* deterministic, not
  deterministic. Caching on a hash of the exact request is what makes
  `results.md` numbers reproducible months later, and it is what lets the whole
  evaluation re-run offline.

The cache key covers everything that can change the output -- provider, model,
system prompt, user prompt, temperature, max_tokens -- so editing a prompt
correctly misses the cache instead of silently returning the old answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
DEFAULT_MAX_TOKENS = 600
_RETRIES = 4


@dataclass
class LLMResponse:
    text: str
    cached: bool
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@dataclass
class UsageStats:
    """Per-run counters, so the ablation table can report calls per question."""

    calls: int = 0  # requests actually sent to the provider
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_requests(self) -> int:
        return self.calls + self.cache_hits

    def as_dict(self) -> dict:
        return {
            "api_calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _provider_for(model: str) -> str:
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    raise ValueError(
        f"cannot infer provider for model {model!r}; set LLM_PROVIDER explicitly"
    )


class LLMClient:
    """Thin wrapper over the provider SDKs, with a content-addressed disk cache.

    Deliberately not a framework: the prompt that goes out is the prompt that is
    built in generate.py, and it is visible in the cache file on disk.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str | None = None,
        cache_dir: Path | None = None,
        offline: bool | None = None,
    ) -> None:
        self.model = model
        self.provider = provider or os.environ.get("LLM_PROVIDER") or _provider_for(model)
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Offline mode fails loudly on a cache miss instead of spending money --
        # used to prove a published number can be reproduced from the cache.
        self.offline = (
            offline if offline is not None else os.environ.get("LLM_OFFLINE") == "1"
        )
        self.usage = UsageStats()
        self._client = None  # created lazily so cached runs need no API key

    # ------------------------------------------------------------------ cache

    def _key(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        payload = json.dumps(
            {
                "provider": self.provider,
                "model": self.model,
                "system": system,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        # Shard by the first two hex chars: a flat directory with tens of
        # thousands of files is slow to list on Windows.
        return self.cache_dir / key[:2] / f"{key}.json"

    def _read_cache(self, key: str) -> dict | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            # A truncated file from an interrupted run should be re-fetched,
            # not crash the evaluation.
            return None

    def _write_cache(self, key: str, record: dict) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)  # atomic, so Ctrl-C cannot leave a half-written entry

    # ----------------------------------------------------------------- calls

    def complete(
        self,
        user: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_salt: str = "",
    ) -> LLMResponse:
        """Send one request (or serve it from cache) and return the raw text.

        `cache_salt` distinguishes otherwise-identical requests that are meant
        to differ -- self-consistency sampling passes the sample index, so five
        samples at temperature>0 do not all collapse onto one cache entry.
        """
        key = self._key(system + "\x00" + cache_salt, user, temperature, max_tokens)
        hit = self._read_cache(key)
        if hit is not None:
            self.usage.cache_hits += 1
            return LLMResponse(
                text=hit["text"],
                cached=True,
                model=hit.get("model", self.model),
                input_tokens=hit.get("input_tokens", 0),
                output_tokens=hit.get("output_tokens", 0),
            )

        if self.offline:
            raise RuntimeError(
                f"cache miss in offline mode (key {key[:12]}). "
                "Unset LLM_OFFLINE to allow live API calls."
            )

        started = time.monotonic()
        text, in_tok, out_tok = self._call_with_retries(system, user, temperature, max_tokens)
        latency = time.monotonic() - started

        self.usage.calls += 1
        self.usage.input_tokens += in_tok
        self.usage.output_tokens += out_tok

        self._write_cache(
            key,
            {
                "provider": self.provider,
                "model": self.model,
                "system": system,
                "cache_salt": cache_salt,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "text": text,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "latency_s": round(latency, 3),
            },
        )
        return LLMResponse(
            text=text,
            cached=False,
            model=self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_s=latency,
        )

    def _call_with_retries(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> tuple[str, int, int]:
        last: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                if self.provider == "anthropic":
                    return self._call_anthropic(system, user, temperature, max_tokens)
                return self._call_openai(system, user, temperature, max_tokens)
            except Exception as exc:  # rate limits, overloads, transient 5xx
                last = exc
                if attempt == _RETRIES - 1:
                    break
                # Exponential backoff with jitter: a synchronised retry storm
                # across a batch run is itself a way to stay rate-limited.
                sleep_s = (2**attempt) + random.random()
                time.sleep(sleep_s)
        raise RuntimeError(f"LLM call failed after {_RETRIES} attempts: {last}") from last

    def _call_anthropic(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> tuple[str, int, int]:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or None,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        return text, msg.usage.input_tokens, msg.usage.output_tokens

    def _call_openai(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> tuple[str, int, int]:
        if self._client is None:
            import openai

            self._client = openai.OpenAI()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage = resp.usage
        return (
            resp.choices[0].message.content or "",
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
        )


def cache_size(cache_dir: Path | None = None) -> tuple[int, int]:
    """(number of cached responses, bytes on disk)."""
    root = cache_dir or config.CACHE_DIR
    if not root.exists():
        return 0, 0
    files = list(root.rglob("*.json"))
    return len(files), sum(f.stat().st_size for f in files)
