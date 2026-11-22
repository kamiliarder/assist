"""Cancel/kill must end the turn, not trigger a retry on a fresh connection."""

import contextlib
import threading
import time
import types
import unittest
from unittest import mock

from ordis.core import agent as agent_module


class FakeResponse:
    status_code = 200
    headers: dict = {}

    def __init__(self):
        self.closed = threading.Event()

    def iter_lines(self):  # the model is "thinking": nothing arrives until the socket is closed
        self.closed.wait(5)
        raise agent_module.httpx.ReadError("connection closed")

    def close(self):
        self.closed.set()


class Harness:
    def __init__(self):
        self.calls = 0
        self.current = None
        self.registered = threading.Event()

    @contextlib.contextmanager
    def stream(self, *a, **k):
        self.calls += 1
        self.current = FakeResponse()
        yield self.current

    def make_agent(self, attempts=4):
        cfg = types.SimpleNamespace(base_url="http://x/v1", model="m", fallback_models=[],
                                    temperature=0, max_tokens=10, max_attempts=attempts,
                                    max_tool_rounds=0, tool_round_warn_every=0)
        toolbox = types.SimpleNamespace(names=lambda: [], schemas=[], call=lambda n, a: "ok")
        return agent_module.Agent(cfg, None, toolbox,
                                  register_stream=lambda r: self.registered.set(),
                                  unregister_stream=lambda: None)


class CancelTests(unittest.TestCase):
    def run_turn(self, h, cancel):
        out = {}
        agent = h.make_agent()
        # patches must outlive this call: the turn keeps running (and reconnecting) in its thread
        for p in (mock.patch.object(agent_module.httpx, "stream", h.stream),
                  mock.patch.object(agent_module.prompt, "build", lambda cfg, names: ("sys", None))):
            p.start()
            self.addCleanup(p.stop)

        def go():
            try:
                agent.run_turn([{"role": "user", "content": "hi"}], cancel=cancel,
                               on_text=lambda t: None, on_status=lambda s: None)
            except BaseException as e:
                out["exc"] = e
        t = threading.Thread(target=go, daemon=True)
        t.start()
        self.assertTrue(h.registered.wait(2))
        return t, out

    def test_cancel_before_first_token_does_not_reconnect(self):
        h, cancel = Harness(), threading.Event()
        t, out = self.run_turn(h, cancel)
        cancel.set()              # what Core.cancel() does: flag first...
        h.current.close()         # ...then drop the connection
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(out["exc"], agent_module.CancelledError)
        self.assertEqual(h.calls, 1)

    def test_closing_the_stream_without_the_flag_is_a_network_error_that_retries(self):
        # documents why Core.cancel() must set the flag: a bare close looks like a dropped connection
        h, cancel = Harness(), threading.Event()
        t, out = self.run_turn(h, cancel)
        h.current.close()
        end = time.time() + 4
        while h.calls < 2 and time.time() < end:   # it reconnects after its backoff
            time.sleep(0.05)
        cancel.set(); h.current.close()
        t.join(3)
        self.assertIsInstance(out["exc"], agent_module.CancelledError)
        self.assertGreaterEqual(h.calls, 2)

    def test_cancel_during_retry_backoff_returns_promptly(self):
        h, cancel = Harness(), threading.Event()
        t, out = self.run_turn(h, cancel)
        h.current.close()         # network blip -> agent goes into its retry sleep
        time.sleep(0.2)
        started = time.time()
        cancel.set()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertLess(time.time() - started, 1.0)
        self.assertIsInstance(out["exc"], agent_module.CancelledError)


if __name__ == "__main__":
    unittest.main()
