"""LLM client (OpenAI-compatible, streaming) and the tool-calling agent loop. No GTK in here."""
import json
import random
import time

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def build_system(tool_names, extra=""):
    text = (
        "You are a concise desktop assistant on a NixOS machine running the niri Wayland compositor.\n"
        f"You can act through these tools: {', '.join(tool_names)}. "
        "Never guess other tool names.\n"
        "Rules:\n"
        "- To launch a GUI app, use open_app.\n"
        "- To run a command the user asks for, use run_command with exactly that command.\n"
        "- If no tool fits the request, say plainly what you can't do and suggest the closest option.\n"
        "- After a tool returns, report its real result. If it says error or denied, say so; "
        "never claim success you didn't see.\n"
        "Keep replies short."
        "Your own configurations are placed at ~/Projects/assist/"
    )
    return f"{text}\n\n{extra.strip()}" if extra.strip() else text


class Agent:
    def __init__(self, cfg, key, toolbox):
        self.cfg = cfg
        self.key = key
        self.toolbox = toolbox
        self.system = build_system(toolbox.names(), cfg.extra_system_prompt)

    # -- one streamed request

    def _stream_once(self, model, messages, on_text, state):
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": self.system}] + messages,
            "tools": self.toolbox.schemas,
            "stream": True,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "text/event-stream"}
        content, calls = "", {}
        timeout = httpx.Timeout(30.0, read=120.0)
        with httpx.stream("POST", f"{self.cfg.base_url}/chat/completions", json=payload,
                          headers=headers, timeout=timeout) as r:
            if r.status_code != 200:
                r.read()
                hint = {401: " (check your API key)",
                        404: f" (model '{model}' not found?)"}.get(r.status_code, "")
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
                    break
                chunk = json.loads(data)
                if not chunk.get("choices"):
                    continue
                delta = chunk["choices"][0].get("delta", {})
                # reasoning models may send delta["reasoning_content"]; we ignore it
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
        msg = {"role": "assistant", "content": content or None}
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        return msg

    # -- retry / fallback

    def _chat(self, messages, on_text, on_status):
        """Retries transient errors with backoff, then falls back to other models.

        Only retries if no output has been shown yet, so a retry never duplicates text.
        """
        models = [self.cfg.model] + list(self.cfg.fallback_models)
        last = None
        for mi, model in enumerate(models):
            for attempt in range(1, self.cfg.max_attempts + 1):
                state = {"started": False}
                try:
                    return self._stream_once(model, messages, on_text, state)
                except (ProviderError, httpx.TransportError) as e:
                    retryable = e.retryable if isinstance(e, ProviderError) else True
                    if state["started"] or not retryable:
                        raise
                    last = e
                    if attempt == self.cfg.max_attempts:
                        break
                    delay = getattr(e, "retry_after", None) or min(2 ** (attempt - 1), 8)
                    delay += random.uniform(0, 0.5)
                    on_status(f"\n… provider error, retrying in {delay:.0f}s "
                              f"({attempt}/{self.cfg.max_attempts})\n")
                    time.sleep(delay)
            if mi + 1 < len(models):
                on_status(f"\n… switching to fallback model {models[mi + 1]}\n")
        raise last

    # -- agent loop

    def run_turn(self, history, on_text, on_status, on_pause=None):
        """model -> tools -> model, without a hard limit.

        `on_pause(rounds)` is called every `cfg.tool_round_warn_every` tool
        runs; return False (or raise) to stop the turn, True to resume.
        """
        rounds = 0
        while True:
            if self.cfg.max_tool_rounds and rounds >= self.cfg.max_tool_rounds:
                on_text(f"\n(stopped after {rounds} tool rounds)\n")
                return
            msg = self._chat(history, on_text, on_status)
            history.append(msg)
            if not msg.get("tool_calls"):
                return
            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
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
