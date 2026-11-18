"""Agent tests against a fake OpenAI-compatible server. No GTK, no network."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from desk_ai.config import Config
from desk_ai.llm import Agent, ProviderError


def sse(*deltas):
    body = "".join(f"data: {json.dumps({'choices': [{'delta': d}]})}\n\n" for d in deltas)
    return 200, body + "data: [DONE]\n\n"


def text(s):
    return sse({"content": s})


def tool_call(name, args, call_id="call_1"):
    return sse({"tool_calls": [{"index": 0, "id": call_id,
                                "function": {"name": name, "arguments": json.dumps(args)}}]})


class FakeProvider:
    """Plays back scripted (status, body) responses and records request payloads."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                outer.requests.append(json.loads(self.rfile.read(n)))
                status, body = outer.script.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if status == 200 else "text/plain")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeToolbox:
    schemas = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}]

    def __init__(self):
        self.calls = []

    def names(self):
        return ["echo"]

    def call(self, name, args):
        self.calls.append((name, args))
        return f"echoed {args}" if name == "echo" else f"error: unknown tool '{name}'"


class AgentTests(unittest.TestCase):
    def make(self, script, **cfg_kwargs):
        provider = FakeProvider(script)
        self.addCleanup(provider.close)
        cfg = Config(base_url=provider.url, model="main", **cfg_kwargs)
        toolbox = FakeToolbox()
        return provider, toolbox, Agent(cfg, "nvapi-test", toolbox)

    def run_turn(self, agent, history):
        out, status = [], []
        with mock.patch("desk_ai.llm.time.sleep"):
            agent.run_turn(history, out.append, status.append)
        return "".join(out), "".join(status)

    def test_plain_reply(self):
        _, _, agent = self.make([text("Hello there")])
        history = [{"role": "user", "content": "hi"}]
        out, _ = self.run_turn(agent, history)
        self.assertEqual(out, "Hello there")
        self.assertEqual(history[-1]["content"], "Hello there")

    def test_retries_503_then_succeeds(self):
        provider, _, agent = self.make([(503, "down"), (503, "down"), text("ok")])
        out, status = self.run_turn(agent, [{"role": "user", "content": "hi"}])
        self.assertEqual(out, "ok")
        self.assertEqual(len(provider.requests), 3)
        self.assertIn("retrying", status)

    def test_401_is_not_retried(self):
        provider, _, agent = self.make([(401, "bad key")])
        with self.assertRaises(ProviderError) as cm:
            self.run_turn(agent, [{"role": "user", "content": "hi"}])
        self.assertIn("API key", str(cm.exception))
        self.assertEqual(len(provider.requests), 1)

    def test_gives_up_after_max_attempts(self):
        provider, _, agent = self.make([(503, "down")] * 3, max_attempts=3)
        with self.assertRaises(ProviderError):
            self.run_turn(agent, [{"role": "user", "content": "hi"}])
        self.assertEqual(len(provider.requests), 3)

    def test_falls_back_to_second_model(self):
        provider, _, agent = self.make([(503, "down")] * 2 + [text("from fallback")],
                                       max_attempts=2, fallback_models=["backup"])
        out, status = self.run_turn(agent, [{"role": "user", "content": "hi"}])
        self.assertEqual(out, "from fallback")
        self.assertEqual([r["model"] for r in provider.requests], ["main", "main", "backup"])
        self.assertIn("fallback model backup", status)

    def test_tool_round_trip(self):
        provider, toolbox, agent = self.make([tool_call("echo", {"x": 1}), text("done")])
        history = [{"role": "user", "content": "do it"}]
        out, status = self.run_turn(agent, history)
        self.assertEqual(out, "done")
        self.assertEqual(toolbox.calls, [("echo", {"x": 1})])
        roles = [m["role"] for m in history]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(history[2]["tool_call_id"], "call_1")
        self.assertIn("⚙ echo", status)
        # second request must include the tool result
        self.assertEqual(provider.requests[1]["messages"][-1]["role"], "tool")

    def test_unknown_tool_error_is_fed_back_to_model(self):
        _, toolbox, agent = self.make([tool_call("teleport", {}), text("sorry, can't")])
        history = [{"role": "user", "content": "x"}]
        self.run_turn(agent, history)
        self.assertIn("unknown tool", history[2]["content"])

    def test_tool_round_limit(self):
        _, _, agent = self.make([tool_call("echo", {})] * 2, max_tool_rounds=2)
        out, _ = self.run_turn(agent, [{"role": "user", "content": "loop"}])
        self.assertIn("stopped after 2 tool rounds", out)

    def test_system_prompt_lists_tools_and_extra(self):
        _, _, agent = self.make([], extra_system_prompt="Call me qmil.")
        self.assertIn("echo", agent.system)
        self.assertIn("Call me qmil.", agent.system)


if __name__ == "__main__":
    unittest.main()
