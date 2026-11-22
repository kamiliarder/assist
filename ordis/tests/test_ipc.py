import os
import socket
import tempfile
import threading
import time
import types
import unittest

from ordis.ipc.client import ClientCallbacks, CoreClient, oneshot
from ordis.ipc.protocol import Decoder, encode, resolve_socket_path
from ordis.ipc.server import AlreadyRunning, Server, bind_socket


class FakeCore:
    """Just enough Core for the server: a turn streams text until cancelled."""

    def __init__(self):
        self.callbacks = None
        self.cfg = types.SimpleNamespace(approval_timeout=2, model="m1")
        self.messages = []
        self.busy = False
        self.cancelled = threading.Event()
        self.confirm_result = None
        self.confirm_wanted = False

    def snapshot(self):
        return {"model": self.cfg.model, "busy": self.busy, "has_key": True, "requires_key": False}

    def session_view(self):
        return {"name": "s1", "messages": list(self.messages)}

    def send_message(self, text, images=None):
        self.busy = True
        self.cancelled.clear()
        self.messages.append({"role": "user", "content": text})
        threading.Thread(target=self._turn, daemon=True).start()
        return True

    def _turn(self):
        cb = self.callbacks
        cb.on_text("\nai\n")
        cb.on_text("hello ")
        if self.confirm_wanted:
            self.confirm_result = cb.confirm("rm -rf /tmp/x")
        while not self.cancelled.wait(0.02):
            cb.on_text(".")
            if not self.confirm_wanted and not self.hold:
                break
        self.busy = False
        cb.on_finish()

    hold = False

    def cancel(self):
        self.cancelled.set()

    def reload_config(self): pass
    def set_model(self, m): self.cfg.model = m
    def list_models(self): return ["m1", "m2"]
    def switch_session(self, d): return None


class Recorder(ClientCallbacks):
    def __init__(self):
        self.events, self.cv = [], threading.Condition()

    def _add(self, *e):
        with self.cv:
            self.events.append(e)
            self.cv.notify_all()

    def on_connection(self, ok, snap): self._add("conn", ok)
    def on_history(self, name, messages): self._add("history", name, len(messages))
    def on_user(self, text, images): self._add("user", text)
    def on_text(self, text): self._add("text", text)
    def on_finish(self): self._add("finish")
    def on_confirm(self, rid, kind, text, extra): self._add("confirm", rid, kind, text)
    def on_confirm_done(self, rid): self._add("confirm_done", rid)
    def on_error(self, text): self._add("error", text)

    def wait_for(self, name, timeout=3):
        end = time.time() + timeout
        with self.cv:
            while time.time() < end:
                for e in self.events:
                    if e[0] == name:
                        return e
                self.cv.wait(0.05)
        raise AssertionError(f"no {name!r} event; got {self.events}")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "ordis", "control.sock")
        self.core = FakeCore()
        self.server = Server(self.core, bind_socket(self.path), path=self.path, owned=True)
        self.server.start()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.server.stop()
        self.dir.cleanup()

    def client(self):
        rec = Recorder()
        c = CoreClient(self.path, rec)
        self.clients.append(c)
        c.start()
        rec.wait_for("history")
        return c, rec


class ProtocolTests(unittest.TestCase):
    def test_decoder_handles_split_and_unterminated(self):
        d = Decoder()
        self.assertEqual(d.feed(b'{"a": 1}{"b"'), [{"a": 1}])
        self.assertEqual(d.feed(b': 2}\n'), [{"b": 2}])

    def test_decoder_flags_bad_line_and_continues(self):
        self.assertEqual(Decoder().feed(b'nope\n{"ok":true}\n'), [{"_invalid": True}, {"ok": True}])

    def test_default_path_is_env_independent_of_config(self):
        self.assertTrue(resolve_socket_path("").endswith("control.sock"))
        self.assertEqual(resolve_socket_path("/x/y.sock"), "/x/y.sock")


class ServerTests(Base):
    def test_socket_is_private(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_second_daemon_does_not_steal_live_socket(self):
        with self.assertRaises(AlreadyRunning):
            bind_socket(self.path)
        self.assertEqual(oneshot(self.path, "healthcheck")["status"], "healthy")

    def test_stale_socket_is_replaced(self):
        self.server.stop()
        s = socket.socket(socket.AF_UNIX); s.bind(self.path); s.close()  # dead file, no listener
        bind_socket(self.path).close()

    def test_legacy_one_shot_without_newline(self):
        s = socket.socket(socket.AF_UNIX); s.connect(self.path)
        s.sendall(b'{"action": "healthcheck"}')
        self.assertIn(b'"healthy"', s.recv(1024)); s.close()

    def test_unknown_and_invalid(self):
        self.assertIn("unknown action", oneshot(self.path, "frobnicate")["error"])

    def test_turn_streams_and_cancel_closes_it(self):
        self.core.hold = True
        c, rec = self.client()
        c.send_message("hi")
        rec.wait_for("user"); rec.wait_for("text")
        self.assertTrue(c.request("status")["busy"])
        self.assertEqual(c.request("kill")["status"], "ok")   # kill == cancel + drop the connection
        rec.wait_for("finish")
        self.assertTrue(self.core.cancelled.is_set())
        self.assertFalse(c.request("status")["busy"])

    def test_send_while_busy_is_rejected(self):
        self.core.hold = True
        c, rec = self.client()
        c.send_message("one"); rec.wait_for("user")
        self.assertIn("busy", c.request("send", text="two")["error"])
        c.cancel(); rec.wait_for("finish")

    def test_second_gui_sees_session_in_progress(self):
        self.core.hold = True
        c1, r1 = self.client()
        c1.send_message("hello there"); r1.wait_for("text")
        c2, r2 = self.client()           # a GUI opened mid-turn
        self.assertEqual(r2.wait_for("history")[2], 0)   # transcript before the turn
        self.assertEqual(r2.wait_for("user")[1], "hello there")
        self.assertIn("hello ", "".join(e[1] for e in r2.events if e[0] == "text"))
        c1.cancel(); r2.wait_for("finish")

    def test_closing_a_gui_does_not_stop_the_turn(self):
        self.core.hold = True
        c1, r1 = self.client()
        c1.send_message("hi"); r1.wait_for("text")
        c1.close(); time.sleep(0.2)
        self.assertTrue(self.core.busy)
        self.assertFalse(self.core.cancelled.is_set())
        self.core.cancel()

    def test_approval_roundtrip_and_deny_when_no_ui(self):
        self.core.confirm_wanted = True
        c, rec = self.client()
        c.send_message("run it")
        _, rid, kind, text = rec.wait_for("confirm")
        self.assertEqual((kind, text), ("confirm", "rm -rf /tmp/x"))
        c.answer(rid, True)
        rec.wait_for("confirm_done")
        end = time.time() + 2
        while self.core.confirm_result is None and time.time() < end:
            time.sleep(0.02)
        self.assertIs(self.core.confirm_result, True)
        self.core.confirm_wanted = False; self.core.cancel(); rec.wait_for("finish")

        # no client attached -> denied, never blocks
        c.close(); time.sleep(0.2)
        self.core.confirm_wanted, self.core.confirm_result = True, None
        self.assertFalse(self.server.ask("confirm", "x"))

    def test_cancel_denies_a_pending_approval(self):
        self.core.confirm_wanted = True
        c, rec = self.client()
        c.send_message("run it"); rec.wait_for("confirm")
        c.cancel()
        end = time.time() + 2
        while self.core.confirm_result is None and time.time() < end:
            time.sleep(0.02)
        self.assertIs(self.core.confirm_result, False)
        self.core.confirm_wanted = False; self.core.cancel(); rec.wait_for("finish")

    def test_client_reconnects_after_daemon_restart(self):
        c, rec = self.client()
        self.server.stop()
        end = time.time() + 3
        while ("conn", False) not in rec.events and time.time() < end:
            time.sleep(0.02)
        self.assertIn(("conn", False), rec.events)
        self.core2 = FakeCore()
        self.server = Server(self.core2, bind_socket(self.path), path=self.path, owned=True)
        self.server.start()
        end = time.time() + 5
        while not c.connected and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(c.connected)


if __name__ == "__main__":
    unittest.main()
