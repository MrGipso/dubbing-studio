"""Offline tests: no models or GPU needed. Run: python -m unittest discover tests"""

import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_client  # noqa: E402
import tts_engines  # noqa: E402


class MockServer:
    """Tiny OpenAI-compatible server: chat completions + audio speech."""

    def __init__(self, reject_schema=False, speech_seconds=1.0):
        self.requests = []
        self.reject_schema = reject_schema
        self.speech_seconds = speech_seconds
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                mock.requests.append(("GET", self.path, None, dict(self.headers)))
                if self.path == "/v1/models":
                    return self._send(200, json.dumps({"data": [{"id": "mock-model", "owned_by": "test"}]}).encode())
                if self.path == "/v1/audio/voices":
                    return self._send(200, json.dumps({"voices": ["v1", "v2"]}).encode())
                self._send(404, b"{}")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                mock.requests.append(("POST", self.path, payload, dict(self.headers)))
                if self.path == "/v1/chat/completions":
                    fmt = (payload.get("response_format") or {}).get("type")
                    if mock.reject_schema and fmt in ("json_schema", "json_object"):
                        return self._send(400, b'{"error": "response_format not supported"}')
                    user = json.loads(payload["messages"][-1]["content"])
                    items = [{"id": unit["id"], "translation": f"EN {unit['source']}"} for unit in user["dubbing_units"]]
                    content = "<think>plan</think>" + json.dumps({"items": items})
                    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
                    return self._send(200, json.dumps(body).encode())
                if self.path == "/v1/audio/speech":
                    rate = 22050
                    t = np.linspace(0, mock.speech_seconds, int(rate * mock.speech_seconds), endpoint=False)
                    buffer = io.BytesIO()
                    sf.write(buffer, (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), rate, format="WAV")
                    return self._send(200, buffer.getvalue(), "audio/wav")
                self._send(404, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def write_srt(path):
    path.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nПервая фраза.\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nВторая фраза.\n",
        encoding="utf-8",
    )


class LLMClientTests(unittest.TestCase):
    def test_schema_request_key_and_reasoning_strip(self):
        server = MockServer()
        try:
            client = llm_client.LLMClient("custom", base_url=server.url, model="mock-model", api_key="sk-test-1234")
            text = client.complete([{"role": "user", "content": json.dumps({"dubbing_units": [{"id": 1, "source": "x"}]})}],
                                   schema={"type": "object"})
            self.assertEqual(json.loads(text)["items"][0]["translation"], "EN x")
            method, path, payload, headers = server.requests[-1]
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            self.assertEqual(headers.get("Authorization"), "Bearer sk-test-1234")
        finally:
            server.close()

    def test_falls_back_when_structured_output_is_rejected(self):
        server = MockServer(reject_schema=True)
        try:
            client = llm_client.LLMClient("custom", base_url=server.url, model="mock-model")
            messages = [{"role": "user", "content": json.dumps({"dubbing_units": [{"id": 7, "source": "y"}]})}]
            text = client.complete(messages, schema={"type": "object"})
            self.assertIn("EN y", text)
            # schema -> json_object -> plain, then remembered for the next call
            client.complete(messages, schema={"type": "object"})
            self.assertNotIn("response_format", server.requests[-1][2])
        finally:
            server.close()

    def test_list_models_openai_compatible(self):
        server = MockServer()
        try:
            models = llm_client.LLMClient("custom", base_url=server.url).list_models()
            self.assertEqual(models[0]["id"], "mock-model")
        finally:
            server.close()

    def test_translate_srt_end_to_end(self):
        server = MockServer()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                srt = Path(tmp) / "clip.srt"
                write_srt(srt)
                result = subprocess.run(
                    [sys.executable, str(ROOT / "translate_srt.py"), str(srt), "--llm-provider", "custom",
                     "--llm-url", server.url, "--model", "mock-model", "--target-language", "English"],
                    capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = (Path(tmp) / "clip.en.dub.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 2)
                self.assertEqual(json.loads(lines[0])["text"], "EN Первая фраза.")
        finally:
            server.close()


class EngineTests(unittest.TestCase):
    def test_registry_lists_engines_with_status(self):
        engines = {item["id"]: item for item in tts_engines.list_engines()}
        self.assertIn("openai_speech", engines)
        self.assertTrue(engines["openai_speech"]["status"]["installed"])
        self.assertNotIn("_template", engines)

    def test_worker_protocol_and_dub_core(self):
        server = MockServer(speech_seconds=2.0)  # longer than the 1.5 s slot -> auto-fit
        try:
            with tempfile.TemporaryDirectory() as tmp:
                plan = Path(tmp) / "clip.en.dub.jsonl"
                plan.write_text(
                    json.dumps({"id": "unit_0001", "text": "One", "duration": 1.5, "source_start": 0.0, "source_end": 1.5}) + "\n"
                    + json.dumps({"id": "unit_0002", "text": "Two", "duration": 2.5, "source_start": 2.0, "source_end": 4.5}) + "\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [sys.executable, str(ROOT / "dub.py"), str(plan), "--engine", "openai_speech",
                     "--engine-settings", json.dumps({"base_url": server.url, "voice": "v1"}),
                     "--auto-fit", "--max-auto-speed", "1.5", "--tail-room", "0.1", "--keep-segments"],
                    capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("time-stretch", result.stdout)
                audio, rate = sf.read(str(Path(tmp) / "clip.en.dub.wav"))
                self.assertEqual(rate, 24000)  # engine returned 22050, core resampled to the track rate
                first, _ = sf.read(str(Path(tmp) / "clip.en.dub_segments" / "unit_0001.wav"))
                self.assertLessEqual(len(first) / rate, 1.6 + 0.01)
                speech_calls = [r for r in server.requests if r[1] == "/v1/audio/speech"]
                self.assertEqual(speech_calls[0][2]["voice"], "v1")
        finally:
            server.close()

    def test_voices_command(self):
        server = MockServer()
        try:
            with tts_engines.EngineSession("openai_speech", log=lambda m: None) as session:
                session.load({"base_url": server.url}, "cpu")
                self.assertEqual(session.voices({"base_url": server.url}), ["v1", "v2"])
        finally:
            server.close()


class ServerHelperTests(unittest.TestCase):
    def setUp(self):
        import pipeline_ui

        self.ui = pipeline_ui

    def make_handler(self, headers):
        class FakeHandler:
            pass

        handler = FakeHandler()
        handler.headers = headers
        return handler

    def test_rejects_foreign_host_and_origin(self):
        local = self.make_handler({"Host": "127.0.0.1:8768"})
        rebinding = self.make_handler({"Host": "evil.example:8768"})
        cross_site = self.make_handler({"Host": "127.0.0.1:8768", "Origin": "https://evil.example"})
        same_site = self.make_handler({"Host": "localhost:8768", "Origin": "http://localhost:8768"})
        self.assertTrue(self.ui.request_is_local(local))
        self.assertFalse(self.ui.request_is_local(rebinding))
        self.assertFalse(self.ui.request_is_local(cross_site))
        self.assertTrue(self.ui.request_is_local(same_site))

    def test_llm_arguments_never_contain_the_key(self):
        settings = {"llm": {"provider": "openrouter", "url": "", "model": "x/y", "contextLength": 4096,
                            "autoLoad": True, "unloadOthers": False, "unloadWhenDone": True}}
        args = self.ui.llm_script_args(settings)
        self.assertEqual(args[:2], ["--llm-provider", "openrouter"])
        self.assertIn("--keep-other-models", args)
        self.assertIn("--unload-model-when-done", args)
        self.assertFalse(any("sk-" in item for item in args))

    def test_safe_stem_keeps_unicode_letters(self):
        self.assertEqual(self.ui.safe_stem("C:/x/Мой ролик (финал).wav"), "Мой_ролик__финал")


if __name__ == "__main__":
    unittest.main()
