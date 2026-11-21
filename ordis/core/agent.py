"""LLM client (OpenAI-compatible, streaming) and the tool-calling agent loop. No UI in here."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import random
import sys
import time

import httpx

from . import prompt
from .types import CoreCallbacks

RETRY_STATUS = {429, 500, 502, 503, 504}


class CancelledError(Exception):
    """Raised when the user cancels an in-flight turn."""


class ProviderError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class Agent:
    def __init__(self, cfg, key, toolbox):
        self.cfg = cfg
        self.key = key
        self.toolbox = toolbox
        self._prompt_warning = None

    @property
    def system(self):
        return prompt.build(self.cfg, self.toolbox.names())[0]

    @staticmethod
    def _image_data_url(path: str) -> str:
        mime, _ = mimetypes.guess_type(path)
        if mime is None or not mime.startswith("image/"):
            mime = "application/octet-stream"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _stream_once(self, model, messages, on_text, state, cancel=None, images=None):
        if images:
            new_messages = list(messages)
            last = new_messages[-1]
            if last.get("role") == "user":
                parts = [{"type": "text", "text": last.get("content", "")}]
                for path in images:
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": self._image_data_url(path)},
                    })
                new_messages[-1] = {"role": "user", "content": parts}
            messages = new_messages

        payload = {
            "model": model,
            "messages": [{"role": "system", "content": self.system}] + messages,
            "tools": self.toolbox.schemas,
            "stream": True,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        headers = {"Accept": "text/event-stream"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        content, calls = "", {}
        finish, saw_done, saw_reasoning = None, False, False
        timeout = httpx.Timeout(30.0, read=120.0)
        with httpx.stream("POST", f"{self.cfg.base_url}/chat/completions", json=payload,
                          headers=headers, timeout=timeout) as r:
            if r.status_code != 200:
                r.read()
                hint = {401: " (check your API key)",
                        404: f" (model '{model}' not found, or wrong base_url {self.cfg.base_url}?)"}.get(r.status_code, "")
                ra = r.headers.get("retry-after", "")
                raise ProviderError(
                    f"HTTP {r.status_code}{hint}: {r.text[:300]}",
                    retryable=r.status_code in RETRY_STATUS,
                    retry_after=float(ra) if ra.replace(".", "", 1).isdigit() else None,
                )
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                if cancel is not None and cancel.is_set():
                    raise CancelledError("cancelled by user")
                chunk = json.loads(data)
                if chunk.get("error") and not chunk.get("choices"):
                    err = chunk["error"]
                    detail = err.get("message") if isinstance(err, dict) else err
                    raise ProviderError(f"provider error mid-stream: {detail}", retryable=True)
                if not chunk.get("choices"):
                    continue
                choice = chunk["choices"][0]
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content") or delta.get("reasoning"):
                    saw_reasoning = True
                if delta.get("content"):
                    state["started"] = True
                    content += delta["content"]
                    on_text(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    state["started"] = True
                    c = calls.setdefault(tc.get("index", 0), {
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        c["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        c["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        c["function"]["arguments"] += fn["arguments"]
        if os.environ.get("ORDIS_DEBUG"):
            print(f"[ordis] model={model} finish={finish} content_chars={len(content)} "
                  f"tool_calls={len(calls)} reasoning={saw_reasoning} done={saw_done}",
                  file=sys.stderr)
        if not (saw_done or finish):
            raise ProviderError("the stream ended early (connection dropped?)", retryable=True)
        if finish == "content_filter":
            raise ProviderError("the provider's content filter blocked this reply")
        if not content and not calls:
            if finish == "length":
                during = " while reasoning" if saw_reasoning else ""
                raise ProviderError(
                    f"the reply hit max_tokens ({self.cfg.max_tokens}) before any output{during}; "
                    "raise max_tokens in config.toml")
            raise ProviderError(
                f"the model returned an empty reply (finish_reason={finish or 'none'})",
                retryable=True)
        if finish == "length":
            on_text(f"\n[output cut off at max_tokens={self.cfg.max_tokens}]\n")
        msg = {"role": "assistant", "content": content or None}
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        return msg

    def _chat(self, messages, on_text, on_status, cancel=None, images=None):
        models = [self.cfg.model] + list(self.cfg.fallback_models)
        last = None
        for mi, model in enumerate(models):
            for attempt in range(1, self.cfg.max_attempts + 1):
                state = {"started": False}
                try:
                    return self._stream_once(model, messages, on_text, state, cancel, images=images)
                except (ProviderError, httpx.TransportError) as e:
                    retryable = e.retryable if isinstance(e, ProviderError) else True
                    if state["started"] or not retryable:
                        raise
                    last = e
                    if attempt == self.cfg.max_attempts:
                        break
                    delay = getattr(e, "retry_after", None) or min(2 ** (attempt - 1), 8)
                    delay += random.uniform(0, 0.5)
                    reason = (str(e) or type(e).__name__)[:100]
                    on_status(f"\n… {reason}; retrying in {delay:.0f}s "
                              f"({attempt}/{self.cfg.max_attempts})\n")
                    time.sleep(delay)
            if mi + 1 < len(models):
                on_status(f"\n… switching to fallback model {models[mi + 1]}\n")
        if isinstance(last, httpx.TransportError):
            raise ProviderError(f"could not reach {self.cfg.base_url}: {last}") from last
        raise last

    def run_turn(self, history, cancel=None, on_text=None, on_status=None, on_pause=None, images=None):
        warning = prompt.build(self.cfg, self.toolbox.names())[1]
        if warning != self._prompt_warning:
            self._prompt_warning = warning
            if warning:
                on_status(f"\n⚠ {warning}\n")
        rounds = 0
        while True:
            if self.cfg.max_tool_rounds and rounds >= self.cfg.max_tool_rounds:
                on_text(f"\n(stopped after {rounds} tool rounds)\n")
                return
            msg = self._chat(history, on_text, on_status, cancel, images=images)
            images = None
            history.append(msg)
            if not msg.get("tool_calls"):
                return
            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError:
                    on_status(f"\n⚙ {name} (unreadable arguments)\n")
                    result = (f"error: the arguments for {name} were not valid JSON, so nothing was "
                              "run. The output was probably cut off (max_tokens); retry with a "
                              "smaller change.")
                else:
                    on_status(f"\n⚙ {name} {json.dumps(args)}\n")
                    result = self.toolbox.call(name, args)
                first = result.splitlines()[0] if result else ""
                on_status(f"→ {first[:200]}\n")
                history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                rounds += 1
                if (self.cfg.tool_round_warn_every
                        and rounds % self.cfg.tool_round_warn_every == 0):
                    if on_pause is None or not on_pause(rounds):
                        on_text(f"\n(paused after {rounds} tool runs)\n")
                        return
