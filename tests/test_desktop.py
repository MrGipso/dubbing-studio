"""Python side of the desktop app: data folders, files by path, quitting with the window."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402
import pipeline_ui  # noqa: E402
import project  # noqa: E402


def post(base, path, body=None):
    request = Request(base + path, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


class PathsTests(unittest.TestCase):
    def test_checkout_keeps_everything_next_to_the_code(self):
        self.assertFalse(paths.SEPARATE_DATA)
        self.assertEqual(paths.resolve_output_dir(""), paths.LIBRARY_DIR / "outputs")
        absolute = Path(tempfile.gettempdir()) / "somewhere"
        self.assertEqual(paths.resolve_output_dir(str(absolute)), absolute)

    def test_desktop_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = (
                "import json, paths, tts_engines, app_settings, project, pipeline_ui\n"
                "print(json.dumps({'settings': str(app_settings.SETTINGS_FILE), 'projects': str(project.PROJECTS_DIR),"
                " 'outputs': str(pipeline_ui.OUTPUTS_DIR), 'runtime': str(tts_engines.RUNTIME_DIR),"
                " 'engine': str(tts_engines.engine_data_dir('demucs')),"
                " 'install': tts_engines.install_environment('demucs')}))\n"
            )
            env = {**os.environ, "DUB_DATA_DIR": str(Path(tmp) / "data"), "DUB_LIBRARY_DIR": str(Path(tmp) / "library")}
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), env=env, check=True)
            found = {key: value if isinstance(value, dict) else Path(value) for key, value in json.loads(result.stdout).items()}
            data, library = Path(tmp) / "data", Path(tmp) / "library"
            self.assertEqual(found["settings"], data / "config" / "settings.local.json")
            self.assertEqual(found["projects"], library / "projects")
            self.assertEqual(found["outputs"], library / "outputs")
            self.assertEqual(found["runtime"], data / ".runtime")
            self.assertEqual(found["engine"], data / "engines" / "demucs")
            self.assertEqual(Path(found["install"]["UV_PROJECT_ENVIRONMENT"]), data / "engines" / "demucs" / ".venv")


class FilesByPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = project.PROJECTS_DIR
        project.PROJECTS_DIR = self.tmp / "projects"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), pipeline_ui.Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        project.PROJECTS_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_and_voice_sample_from_disk(self):
        source = self.tmp / "Мой ролик.wav"
        sf.write(str(source), np.zeros(22050, dtype=np.float32), 22050)
        created = post(self.base, "/api/projects/from-path", {"path": str(source), "targets": ["German"], "speaker_count": 0})["project"]
        self.assertEqual(created["name"], "Мой ролик")
        self.assertEqual(created["targets"], ["German"])
        self.assertEqual(created["speaker_count"], 0)
        self.assertTrue(source.is_file())  # the original stays where it was

        sample = self.tmp / "voice.wav"
        sf.write(str(sample), np.zeros(22050, dtype=np.float32), 22050)
        attached = post(self.base, f"/api/projects/{quote(created['id'])}/attach", {"kind": "voice", "path": str(sample), "speaker": "S1"})["project"]
        voice = attached["speakers"]["S1"]["voice"]
        self.assertEqual(voice["mode"], "file")
        self.assertTrue((project.PROJECTS_DIR / created["id"] / voice["file"]).is_file())

        with self.assertRaises(Exception):
            post(self.base, "/api/projects/from-path", {"path": str(self.tmp / "missing.mp4")})
        state = json.loads(urlopen(self.base + "/api/state").read())
        self.assertIn("ffmpeg", state["tools"])
        self.assertFalse(state["desktop"])


class ServerLifetimeTests(unittest.TestCase):
    def start(self, tmp):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DUB_DATA_DIR": str(Path(tmp) / "data"),
               "DUB_LIBRARY_DIR": str(Path(tmp) / "library"), "DUB_DESKTOP": "1"}
        process = subprocess.Popen([sys.executable, str(ROOT / "pipeline_ui.py"), "--port", "0", "--exit-with-stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   cwd=str(ROOT), env=env)
        lines = []
        for _ in range(20):  # warnings may come first; the shell looks for this line too
            line = process.stdout.readline().decode("utf-8").strip()
            lines.append(line)
            if line.startswith("Dubbing Studio: http://127.0.0.1:"):
                return process, line.split(": ", 1)[1]
        process.kill()
        self.fail("\n".join(lines))

    def wait(self, process):
        try:
            return process.wait(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
            for stream in (process.stdin, process.stdout):
                if stream and not stream.closed:
                    stream.close()

    def test_free_port_and_shutdown_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            process, url = self.start(tmp)
            state = json.loads(urlopen(url + "/api/state", timeout=10).read())
            self.assertTrue(state["desktop"])
            post(url, "/api/shutdown")
            self.assertEqual(self.wait(process), 0)

    def test_jobs_do_not_hang_on_the_window_pipe(self):
        # The server reads the window's pipe in a thread; a child started meanwhile
        # must not inherit that pipe (on Windows it would hang while starting).
        code = (
            "import subprocess, sys, threading, pipeline_ui\n"
            "pipe = pipeline_ui.detach_stdin()\n"
            "threading.Thread(target=lambda: open(pipe, 'rb', closefd=True).read(), daemon=True).start()\n"
            "result = subprocess.run([sys.executable, '-c', 'print(42)'], capture_output=True, text=True, timeout=30)\n"
            "print('child', result.stdout.strip(), flush=True)\n"
        )
        process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=str(ROOT))
        try:
            output, errors = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
        self.assertIn(b"child 42", output, errors.decode("utf-8", "replace"))

    def test_quits_when_the_window_goes_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            process, url = self.start(tmp)
            time.sleep(0.3)
            process.stdin.close()  # what happens when the desktop shell exits or crashes
            self.assertEqual(self.wait(process), 0)


if __name__ == "__main__":
    unittest.main()
