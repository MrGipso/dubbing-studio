"""Project pipeline tests without models: run with python -m unittest discover tests"""

import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pipeline_ui  # noqa: E402
import project  # noqa: E402
import tts_engines  # noqa: E402


def tone(path, seconds=3.0, rate=22050):
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate)


class TempProjects(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved_dir = project.PROJECTS_DIR
        project.PROJECTS_DIR = self.tmp / "projects"

    def tearDown(self):
        project.PROJECTS_DIR = self.saved_dir
        shutil.rmtree(self.tmp, ignore_errors=True)


class ReferenceTests(unittest.TestCase):
    def test_picks_clean_continuous_speech(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "э", "avg_logprob": -1.5, "no_speech_prob": 0.9},
            {"start": 2.0, "end": 6.0, "text": "Первая фраза.", "avg_logprob": -0.2, "no_speech_prob": 0.05},
            {"start": 6.3, "end": 11.0, "text": "Вторая фраза.", "avg_logprob": -0.25, "no_speech_prob": 0.05},
            {"start": 14.0, "end": 16.0, "text": "Далеко.", "avg_logprob": -0.1, "no_speech_prob": 0.05},
        ]
        start, end, text = project.pick_reference(segments)
        self.assertEqual((start, end), (2.0, 11.0))
        self.assertEqual(text, "Первая фраза. Вторая фраза.")

    def test_falls_back_to_longest_segment(self):
        segments = [{"start": 0.0, "end": 3.0, "text": "Коротко.", "avg_logprob": -0.3, "no_speech_prob": 0.1}]
        self.assertEqual(project.pick_reference(segments)[2], "Коротко.")
        self.assertIsNone(project.pick_reference([]))


class MixTests(unittest.TestCase):
    def test_mix_includes_only_available_tracks(self):
        mix = {"voice": 1.0, "background": 0.7, "music": 0.3, "loudnorm": True}
        args = project.build_mix_command("v.wav", "bg.wav", None, 40.0, mix, "out.wav")
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("amix=inputs=2", graph)
        self.assertIn("loudnorm", graph)
        self.assertEqual(args[args.index("-t") + 1], "40.000")

        args = project.build_mix_command("v.wav", None, "m.mp3", 10.0, {**mix, "loudnorm": False}, "out.wav")
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("-stream_loop", args)
        self.assertIn("afade=t=out", graph)
        self.assertNotIn("loudnorm", graph)


class ProgressTests(unittest.TestCase):
    def test_parse_progress_lines(self):
        self.assertEqual(pipeline_ui.parse_progress("PROGRESS 3/10 Перевод фраз"),
                         ("progress", {"done": 3, "total": 10, "label": "Перевод фраз"}))
        self.assertEqual(pipeline_ui.parse_progress("STAGE 2/8 Распознавание речи")[0], "stage")
        self.assertIsNone(pipeline_ui.parse_progress("Translating dubbing units: 1, 2"))


class ProjectStateTests(TempProjects):
    def test_changes_invalidate_later_stages(self):
        source = self.tmp / "clip.wav"
        tone(source)
        directory, data = project.create_from_file(source, "Тест", ["German"])
        self.assertEqual(data["source"]["duration"], 3.0)
        self.assertFalse(data["source"]["has_video"])
        data["transcribed"] = True
        data["tracks"]["de"].update(translated=True, voiced=True, exported=True)
        project.save_project(directory, data)
        self.assertEqual(project.load_project(directory)["state"], {"prepared": True, "voiced": True, "exported": True})

        self.assertFalse(project.update_project(directory, {"mix": {"music": 0.1}})["tracks"]["de"]["exported"])
        state = project.update_project(directory, {"voice": {"mode": "engine"}})["state"]
        self.assertTrue(state["prepared"])
        self.assertFalse(state["voiced"])

        # Adding a language keeps the finished one and plans only the new one.
        data = project.update_project(directory, {"targets": ["German", "French"]})
        self.assertTrue(data["tracks"]["de"]["translated"])
        self.assertFalse(data["tracks"]["fr"]["translated"])
        self.assertFalse(data["state"]["prepared"])
        steps = project.plan_steps(data, "all")
        self.assertEqual(steps[0], ("translate", "French"))
        self.assertIn(("voice", "German"), steps)  # German voice was invalidated by the voice change
        self.assertNotIn(("translate", "German"), steps)
        self.assertEqual(project.plan_steps(data, "translate"), [("translate", "French")])

        data = project.update_project(directory, {"active": "French"})
        self.assertEqual(data["active"], "French")
        self.assertEqual(project.update_project(directory, {"active": "Klingon"})["active"], "German")

        self.assertFalse(project.update_project(directory, {"separate": False})["transcribed"])
        # Unknown keys from the UI are ignored.
        self.assertNotIn("evil", project.update_project(directory, {"evil": 1}))

    def test_old_single_language_projects_are_migrated(self):
        source = self.tmp / "clip.wav"
        tone(source)
        directory, _ = project.create_from_file(source, "Старый")
        (directory / "work" / "transcript.json").write_text("{}", encoding="utf-8")
        old = {
            "name": "Старый", "target": "Spanish",
            "state": {"prepared": True, "voiced": True, "exported": False},
            "edited": [3], "dirty": [3], "voice_track": "output/x.es.voice.wav", "video": "",
            "source": project.load_project(directory)["source"],
        }
        (directory / "project.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        data = project.load_project(directory)
        self.assertEqual(data["targets"], ["Spanish"])
        self.assertTrue(data["transcribed"])
        spanish = data["tracks"]["es"]
        self.assertEqual((spanish["translated"], spanish["voiced"], spanish["exported"]), (True, True, False))
        self.assertEqual(spanish["dirty"], [3])
        self.assertEqual(spanish["voice_track"], "output/x.es.voice.wav")
        self.assertNotIn("voice_track", data)

    def test_voice_sample_candidates_and_choice(self):
        source = self.tmp / "clip.wav"
        tone(source, seconds=40.0)
        directory, data = project.create_from_file(source, "Голос")
        work = directory / "work"
        shutil.copy(source, work / "audio.wav")
        segments = [
            {"start": 0.5, "end": 4.5, "text": "Раз.", "avg_logprob": -0.2, "no_speech_prob": 0.05},
            {"start": 4.8, "end": 9.0, "text": "Два.", "avg_logprob": -0.2, "no_speech_prob": 0.05},
            {"start": 20.0, "end": 24.0, "text": "Три.", "avg_logprob": -0.4, "no_speech_prob": 0.05},
            {"start": 24.3, "end": 29.5, "text": "Четыре.", "avg_logprob": -0.4, "no_speech_prob": 0.05},
        ]
        (work / "transcript.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
        project.stage_voice_sample(directory, data)
        project.save_project(directory, data)
        voice = project.load_project(directory)["voice"]
        self.assertEqual(len(voice["candidates"]), 2)
        self.assertEqual(voice["auto_text"], "Раз. Два.")
        chosen = project.choose_sample(directory, 1)["voice"]
        self.assertEqual(chosen["auto_index"], 1)
        self.assertEqual(chosen["auto_text"], "Три. Четыре.")
        self.assertAlmostEqual(sf.info(str(directory / "voice" / "original.wav")).duration, 9.74, places=1)

    def test_delete_moves_to_trash(self):
        source = self.tmp / "clip.wav"
        tone(source)
        directory, _ = project.create_from_file(source, "Удалить")
        trash = project.delete_project(directory)
        self.assertFalse(directory.exists())
        self.assertTrue((trash / "project.json").is_file())
        self.assertEqual(project.list_projects(), [])


class SeparatorProtocolTests(unittest.TestCase):
    def test_separate_command(self):
        tmp = Path(tempfile.mkdtemp())
        saved = tts_engines.ENGINES_DIR
        try:
            engine = tmp / "fake_sep"
            engine.mkdir()
            (engine / "engine.json").write_text(json.dumps({"kind": "separator", "name": "Fake", "runtime": "main"}), encoding="utf-8")
            (engine / "adapter.py").write_text(
                "import shutil\n"
                "from pathlib import Path\n"
                "def load(settings, device):\n    return {'sample_rate': 22050}\n"
                "def separate(input_path, out_dir):\n"
                "    out = Path(out_dir)\n"
                "    shutil.copy(input_path, out / 'vocals.wav')\n"
                "    shutil.copy(input_path, out / 'background.wav')\n"
                "    print('PROGRESS 1/1 sep', flush=True)\n"
                "    return {'vocals': str(out / 'vocals.wav'), 'background': str(out / 'background.wav')}\n",
                encoding="utf-8",
            )
            tts_engines.ENGINES_DIR = tmp
            self.assertEqual([item["id"] for item in tts_engines.list_engines(kind="separator")], ["fake_sep"])
            self.assertEqual(tts_engines.list_engines(kind="tts"), [])
            tone(tmp / "mix.wav")
            logs = []
            with tts_engines.EngineSession("fake_sep", log=logs.append) as session:
                session.load({}, "cpu")
                result = session.separate(tmp / "mix.wav", tmp)
            self.assertTrue(Path(result["vocals"]).is_file())
            self.assertIn("PROGRESS 1/1 sep", logs)
        finally:
            tts_engines.ENGINES_DIR = saved
            shutil.rmtree(tmp, ignore_errors=True)


class WarmEngineTests(unittest.TestCase):
    def test_resident_worker_loads_the_model_once(self):
        tmp = Path(tempfile.mkdtemp())
        saved = (tts_engines.ENGINES_DIR, tts_engines.RUNTIME_DIR)
        try:
            engine = tmp / "fake_tts"
            engine.mkdir()
            (engine / "engine.json").write_text(json.dumps({"name": "Fake TTS", "runtime": "venv"}), encoding="utf-8")
            (engine / "engine.local.json").write_text(json.dumps({"python": sys.executable}), encoding="utf-8")
            (engine / "adapter.py").write_text(
                "from pathlib import Path\n"
                "import numpy as np\n"
                "LOADS = Path(__file__).with_name('loads.txt')\n"
                "def load(settings, device):\n"
                "    LOADS.write_text(str(int(LOADS.read_text() or 0) + 1) if LOADS.exists() else '1')\n"
                "    return {'sample_rate': 16000}\n"
                "def synthesize(text, language=None, voice=None, duration=None, speed=1.0, hints=None):\n"
                "    return np.zeros(1600, dtype=np.float32), 16000\n",
                encoding="utf-8",
            )
            tts_engines.ENGINES_DIR = tmp
            tts_engines.RUNTIME_DIR = tmp / ".runtime"
            logs = []
            for index in range(2):
                with tts_engines.EngineSession("fake_tts", log=logs.append, startup_timeout=60, resident=True) as session:
                    session.load({"quality": 1}, "cpu")
                    result = session.synthesize("hi", tmp / f"out{index}.wav")
                    self.assertEqual(result["sample_rate"], 16000)
            self.assertEqual((engine / "loads.txt").read_text(), "1")
            self.assertTrue(any("already loaded" in line for line in logs))
            self.assertEqual([item["engine"] for item in tts_engines.resident_status()], ["fake_tts"])
            self.assertEqual(tts_engines.stop_resident(), ["fake_tts"])
            self.assertFalse(tts_engines.runtime_file("fake_tts").exists())
        finally:
            tts_engines.stop_resident()
            tts_engines.ENGINES_DIR, tts_engines.RUNTIME_DIR = saved
            shutil.rmtree(tmp, ignore_errors=True)


class ProjectApiTests(TempProjects):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), pipeline_ui.Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, path, data=None, content_type="application/json"):
        headers = {"Content-Type": content_type} if data is not None else {}
        with urlopen(Request(self.base + path, data=data, headers=headers, method="POST" if data is not None else "GET")) as response:
            return json.loads(response.read())

    def upload(self, path, filename, payload, fields=None):
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for key, value in (fields or {}).items():
            body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                   f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
        body.write(payload)
        body.write(f"\r\n--{boundary}--\r\n".encode())
        return self.request(path, body.getvalue(), f"multipart/form-data; boundary={boundary}")

    def test_create_update_and_serve(self):
        wav = io.BytesIO()
        sf.write(wav, np.zeros(22050, dtype=np.float32), 22050, format="WAV")
        created = self.upload("/api/projects", "Мой ролик.wav", wav.getvalue(), {"targets": "Spanish,German"})["project"]
        self.assertEqual(created["targets"], ["Spanish", "German"])
        self.assertEqual(created["active"], "Spanish")
        self.assertEqual([language["code"] for language in created["languages"]], ["es", "de"])
        self.assertEqual(created["name"], "Мой ролик")
        self.assertIn("source", created["files"])

        listed = self.request("/api/projects")["projects"]
        self.assertEqual([item["id"] for item in listed], [created["id"]])

        from urllib.parse import quote
        updated = self.request(f"/api/projects/{quote(created['id'])}/update", json.dumps({"mix": {"voice": 0.5}}).encode())
        self.assertEqual(updated["project"]["mix"]["voice"], 0.5)

        with urlopen(self.base + created["files"]["source"]["url"]) as response:
            self.assertGreater(len(response.read()), 1000)

        with self.assertRaises(Exception):
            urlopen(self.base + f"/api/projects/{quote(created['id'])}/file?path=..%2F..%2Fpipeline_ui.py")

    def test_rejects_non_media_upload(self):
        with self.assertRaises(Exception):
            self.upload("/api/projects", "notes.txt", b"hello")
        self.assertEqual(project.list_projects(), [])


if __name__ == "__main__":
    unittest.main()
