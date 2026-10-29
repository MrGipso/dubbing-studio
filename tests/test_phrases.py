"""Stage 2: phrase editing, shortening and partial re-voice (no models needed)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project  # noqa: E402
from test_pipeline import MockServer  # noqa: E402


def make_project(tmp, voiced=False):
    """A project that already went through prepare (and optionally voice)."""
    source = tmp / "clip.wav"
    t = np.linspace(0, 6, 6 * 22050, endpoint=False)
    sf.write(str(source), (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 22050)
    directory, data = project.create_from_file(source, "Фразы", "English")
    work = directory / "work"
    units = [
        {"id": 1, "start": 0.0, "end": 2.0, "duration": 2.0, "source": "Первая.", "translation": "First one."},
        {"id": 2, "start": 2.5, "end": 4.0, "duration": 1.5, "source": "Вторая фраза.", "translation": "The second rather long phrase here."},
    ]
    (work / "transcript.en.segments.json").write_text(json.dumps({"translation_units": units}, ensure_ascii=False), encoding="utf-8")
    plan = work / "transcript.en.dub.jsonl"
    plan.write_text("".join(
        json.dumps({"id": f"unit_{u['id']:04d}", "text": u["translation"], "duration": u["duration"],
                    "source_start": u["start"], "source_end": u["end"], "language_id": "en"}) + "\n"
        for u in units
    ), encoding="utf-8")
    data = project.load_project(directory)
    if voiced:
        track = directory / "output" / "Фразы.en.voice.wav"
        sf.write(str(track), np.zeros(22050, dtype=np.float32), 22050)
        track.with_name(track.stem + ".report.json").write_text(json.dumps({"segments": [
            {"id": "unit_0001", "speedup": 1.0, "clipped_seconds": 0.0, "natural_seconds": 1.2},
            {"id": "unit_0002", "speedup": 1.18, "clipped_seconds": 0.4, "natural_seconds": 2.4},
        ]}), encoding="utf-8")
        data["voice_track"] = track.relative_to(directory).as_posix()
        data["state"] = {"prepared": True, "voiced": True, "exported": True}
    else:
        data["state"] = {"prepared": True, "voiced": False, "exported": False}
    project.save_project(directory, data)
    return directory, project.load_project(directory)


class PhraseProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = project.PROJECTS_DIR
        project.PROJECTS_DIR = self.tmp / "projects"

    def tearDown(self):
        project.PROJECTS_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_edit_updates_plan_subtitles_and_flags(self):
        directory, data = make_project(self.tmp, voiced=True)
        data = project.set_translations(directory, data, {2: "Second phrase."})
        plan = [json.loads(line) for line in project.plan_path(directory, data).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(plan[1]["text"], "Second phrase.")
        self.assertEqual(plan[0]["text"], "First one.")
        self.assertIn("Second phrase.", project.translated_srt_path(directory, data).read_text(encoding="utf-8"))
        saved = project.load_project(directory)
        self.assertEqual(saved["edited"], [2])
        self.assertEqual(saved["dirty"], [2])
        self.assertFalse(saved["state"]["exported"])
        with self.assertRaises(project.ProjectError):
            project.set_translations(directory, saved, {1: "   "})
        with self.assertRaises(project.ProjectError):
            project.set_translations(directory, saved, {99: "Nope"})

    def test_edit_before_voicing_is_not_dirty(self):
        directory, data = make_project(self.tmp, voiced=False)
        project.set_translations(directory, data, {1: "Changed."})
        saved = project.load_project(directory)
        self.assertEqual(saved["edited"], [1])
        self.assertEqual(saved["dirty"], [])

    def test_long_phrases_come_from_the_fit_report(self):
        directory, data = make_project(self.tmp, voiced=True)
        self.assertEqual(project.long_phrase_ids(directory, data), [2])

    def test_shorten_with_any_llm(self):
        directory, data = make_project(self.tmp, voiced=True)
        server = MockServer()
        try:
            settings = {"llm": {"provider": "custom", "url": server.url, "model": "mock-model", "unloadWhenDone": False}}
            changed = project.shorten_phrases(directory, data, [2], settings)
        finally:
            server.close()
        self.assertEqual(changed, {2: "EN Вторая фраза."})
        saved = project.load_project(directory)
        self.assertEqual(saved["dirty"], [2])
        request = json.loads(server.requests[-1][2]["messages"][-1]["content"])
        self.assertEqual(request["task"], "shorten_dubbing_lines")
        self.assertEqual(request["dubbing_units"][0]["slot_seconds"], 1.5)


class PartialRevoiceTests(unittest.TestCase):
    def test_only_revoices_selected_phrases(self):
        server = MockServer(speech_seconds=1.0)
        tmp = Path(tempfile.mkdtemp())
        try:
            plan = tmp / "clip.en.dub.jsonl"
            items = [
                {"id": "unit_0001", "text": "One", "duration": 1.5, "source_start": 0.0, "source_end": 1.5},
                {"id": "unit_0002", "text": "Two", "duration": 1.5, "source_start": 2.0, "source_end": 3.5},
            ]
            plan.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
            segments = tmp / "segments"
            base = [sys.executable, str(ROOT / "dub.py"), str(plan), "--engine", "openai_speech",
                    "--engine-settings", json.dumps({"base_url": server.url}), "--keep-segments",
                    "--segments-dir", str(segments)]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            first = subprocess.run(base, capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            calls_after_full = len([r for r in server.requests if r[1] == "/v1/audio/speech"])
            kept_mtime = (segments / "unit_0001.wav").stat().st_mtime_ns

            items[1]["text"] = "Two, edited"
            plan.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
            second = subprocess.run(base + ["--only", "unit_0002"], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            speech = [r for r in server.requests if r[1] == "/v1/audio/speech"]
            self.assertEqual(calls_after_full, 2)
            self.assertEqual(len(speech), 3)
            self.assertEqual(speech[-1][2]["input"], "Two, edited")
            self.assertEqual((segments / "unit_0001.wav").stat().st_mtime_ns, kept_mtime)
            report = json.loads((tmp / "clip.en.dub.report.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["id"] for entry in report["segments"]], ["unit_0001", "unit_0002"])

            # Nothing selected and every phrase saved: no engine is started at all.
            third = subprocess.run(base + ["--only", "unit_9999"], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env)
            self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
            self.assertIn("Nothing to re-voice", third.stdout)
            self.assertEqual(len([r for r in server.requests if r[1] == "/v1/audio/speech"]), 3)
        finally:
            server.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
