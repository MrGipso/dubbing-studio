"""Stage 4: speakers. A fake diarizer engine stands in for sherpa-onnx, so no models are needed."""

import argparse
import importlib.util
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

import dub  # noqa: E402
import pipeline_ui  # noqa: E402
import project  # noqa: E402
import tts_engines  # noqa: E402
from test_pipeline import MockServer  # noqa: E402


def load_clustering():
    path = ROOT / "engines" / "sherpa_diarization" / "clustering.py"
    spec = importlib.util.spec_from_file_location("sherpa_clustering", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def timed_words(*items):
    """("word", start, end), ... -> Whisper word timings."""
    return [{"start": start, "end": end, "word": f" {word}"} for word, start, end in items]


class AssignSpeakersTests(unittest.TestCase):
    def test_splits_a_phrase_where_the_voice_changes(self):
        turns = [
            {"start": 0.0, "end": 2.0, "speaker": 4},
            {"start": 2.1, "end": 5.0, "speaker": 1},
            {"start": 5.1, "end": 9.0, "speaker": 4},
            {"start": 5.85, "end": 6.15, "speaker": 7},  # a slip of the diarizer inside one word
        ]
        segments = [
            {"start": 0.0, "end": 4.0, "text": "Ты готов? Да, начинаем прямо сейчас.", "avg_logprob": -0.2,
             "words": timed_words(("Ты", 0.0, 0.5), ("готов?", 0.6, 1.5), ("Да,", 2.2, 2.6),
                                  ("начинаем", 2.7, 3.3), ("прямо", 3.4, 3.7), ("сейчас.", 3.8, 4.0))},
            {"start": 5.2, "end": 7.0, "text": "Отлично ну поехали.",
             "words": timed_words(("Отлично", 5.2, 5.8), ("ну", 5.9, 6.1), ("поехали.", 6.2, 7.0))},
            {"start": 7.2, "end": 8.5, "text": "Без слов."},
        ]
        phrases = project.assign_speakers(segments, turns)
        self.assertEqual([(p["text"], p["speaker"]) for p in phrases], [
            ("Ты готов?", "S1"),
            ("Да, начинаем прямо сейчас.", "S2"),
            ("Отлично ну поехали.", "S1"),
            ("Без слов.", "S1"),
        ])
        self.assertEqual((phrases[1]["start"], phrases[1]["end"]), (2.2, 4.0))
        self.assertEqual(phrases[1]["avg_logprob"], -0.2)

    def test_without_turns_everyone_is_one_speaker(self):
        segments = [{"start": 0.0, "end": 1.0, "text": "Раз."}, {"start": 1.5, "end": 2.0, "text": "Два."}]
        self.assertEqual({p["speaker"] for p in project.assign_speakers(segments, [])}, {"S1"})


class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self.clustering = load_clustering()
        rng = np.random.default_rng(0)
        self.voices = rng.normal(size=(4, 64))
        self.rng = rng

    def prints(self, order):
        return [self.voices[k] + 0.25 * self.rng.normal(size=64) for k in order]

    def test_groups_turns_by_voice(self):
        order = [0, 1, 0, 2, 1, 0, 2, 1]
        durations = [3.0] * len(order)
        self.assertEqual(self.clustering.group_turns(self.prints(order), durations), order)
        self.assertEqual(len(set(self.clustering.group_turns(self.prints(order), durations, num_speakers=2))), 2)

    def test_short_turns_and_stray_voices_join_real_speakers(self):
        order = [0, 1, 0, 1, 2, 3]
        durations = [4.0, 4.0, 4.0, 4.0, 0.5, 1.2]  # voice 2 only in a short turn, voice 3 barely speaks
        labels = self.clustering.group_turns(self.prints(order), durations)
        self.assertEqual(labels[:4], [0, 1, 0, 1])
        self.assertLessEqual(len(set(labels)), 2)


class TranslationUnitTests(unittest.TestCase):
    def test_units_never_mix_speakers_and_keep_the_label(self):
        server = MockServer()
        tmp = Path(tempfile.mkdtemp())
        try:
            phrases = tmp / "transcript.json"
            phrases.write_text(json.dumps({"segments": [
                {"start": 0.0, "end": 1.0, "text": "Привет,", "speaker": "S1"},
                {"start": 1.2, "end": 2.0, "text": "как дела", "speaker": "S2"},
                {"start": 2.1, "end": 3.0, "text": "отлично.", "speaker": "S2"},
            ]}, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "translate_srt.py"), str(phrases), "--llm-provider", "custom",
                 "--llm-url", server.url, "--model", "mock-model", "--target-language", "English"],
                capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            plan = [json.loads(line) for line in (tmp / "transcript.en.dub.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["speaker"] for item in plan], ["S1", "S2"])
            self.assertEqual(plan[1]["source_segment_ids"], [2, 3])
            request = json.loads(server.requests[-1][2]["messages"][-1]["content"])
            self.assertEqual([unit["speaker"] for unit in request["dubbing_units"]], ["S1", "S2"])
            self.assertTrue(any("speaker" in rule for rule in request["rules"]))
        finally:
            server.close()
            shutil.rmtree(tmp, ignore_errors=True)


class DubVoiceTests(unittest.TestCase):
    def test_each_speaker_gets_their_voice(self):
        session = argparse.Namespace(sample_rate=24000)
        args = argparse.Namespace(ref_audio="", ref_text="", voice_id="")
        voices = {"S1": {"ref_audio": "a.wav", "ref_text": "Голос А"}, "S2": {"voice_id": "alloy"}}
        synth = dub.Synthesizer(session, {"supports": {}}, args, Path("."), voices)
        self.assertEqual(synth.voice_for({"speaker": "S1"}), {"ref_audio": str(Path("a.wav").resolve()), "ref_text": "Голос А"})
        self.assertEqual(synth.voice_for({"speaker": "S2"}), {"voice_id": "alloy"})
        self.assertEqual(synth.voice_for({})["ref_text"], "Голос А")  # lines without a speaker belong to S1
        args.ref_audio, args.ref_text = "b.wav", "Для всех"
        self.assertEqual(synth.voice_for({"speaker": "S2"})["ref_text"], "Для всех")


FAKE_DIARIZER = '''
import soundfile as sf

def load(settings, device):
    return {"sample_rate": 16000}

def diarize(input_path, num_speakers=None):
    assert sf.info(input_path).samplerate == 16000
    print("PROGRESS 1/1 diarize", flush=True)
    return {"turns": [
        {"start": 0.0, "end": 10.2, "speaker": 0},
        {"start": 10.4, "end": 14.2, "speaker": 1},
        {"start": 14.4, "end": 20.0, "speaker": 0},
    ]}
'''


class SpeakerProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = project.PROJECTS_DIR, tts_engines.ENGINES_DIR
        project.PROJECTS_DIR = self.tmp / "projects"
        engines = self.tmp / "engines"
        shutil.copytree(ROOT / "engines", engines, ignore=shutil.ignore_patterns(".venv", "models", "engine.local.json"))
        fake = engines / "fake_diar"
        fake.mkdir()
        (fake / "engine.json").write_text(json.dumps({"kind": "diarizer", "name": "Fake", "runtime": "main"}), encoding="utf-8")
        (fake / "adapter.py").write_text(FAKE_DIARIZER, encoding="utf-8")
        tts_engines.ENGINES_DIR = engines

    def tearDown(self):
        project.PROJECTS_DIR, tts_engines.ENGINES_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_project(self):
        source = self.tmp / "dialog.wav"
        t = np.linspace(0, 20, 20 * 16000, endpoint=False)
        sf.write(str(source), (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), 16000)
        directory, _ = project.create_from_file(source, "Диалог", ["English"], speaker_count=0)
        shutil.copy(source, directory / "work" / "audio.wav")
        segments = [
            {"start": 0.0, "end": 5.0, "text": "Первая длинная фраза ведущего.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
            {"start": 5.2, "end": 10.0, "text": "И вторая фраза ведущего", "avg_logprob": -0.2, "no_speech_prob": 0.01},
            {"start": 10.5, "end": 12.0, "text": "Гость отвечает", "avg_logprob": -0.3, "no_speech_prob": 0.01},
            {"start": 12.3, "end": 14.0, "text": "Коротко.", "avg_logprob": -0.3, "no_speech_prob": 0.01},
            {"start": 14.5, "end": 20.0, "text": "Ведущий заканчивает.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
        ]
        (directory / "work" / "whisper.json").write_text(json.dumps({"language": "ru", "segments": segments}, ensure_ascii=False),
                                                         encoding="utf-8")
        data = project.load_project(directory)
        data["transcribed"] = True
        project.save_project(directory, data)
        return directory, project.load_project(directory)

    def test_speakers_from_diarization_to_voices(self):
        directory, data = self.make_project()
        server = MockServer()
        try:
            settings = {
                "diarization": {"engine": "fake_diar"},
                "llm": {"provider": "custom", "url": server.url, "model": "mock-model", "unloadWhenDone": False},
            }
            self.assertEqual(project.plan_steps(data, "translate")[:2], [("diarize", None), ("sample", None)])
            project.stage_diarize(directory, data, settings)
            self.assertTrue(data["diarized"])
            self.assertEqual(list(data["speakers"]), ["S1", "S2"])
            phrases = project.load_phrases(directory)
            self.assertEqual([p["speaker"] for p in phrases], ["S1", "S1", "S2", "S2", "S1"])
            self.assertFalse((directory / "work" / "speech_16k.wav").exists())

            project.stage_voice_sample(directory, data)
            host, guest = data["speakers"]["S1"]["voice"], data["speakers"]["S2"]["voice"]
            self.assertEqual(host["auto_file"], "voice/original_s1.wav")
            self.assertNotIn("parts", host["candidates"][0])
            # The guest never speaks 6 s in a row: the sample is glued from short phrases.
            self.assertEqual(guest["candidates"][0]["parts"], [[10.5, 12.0], [12.3, 14.0]])
            self.assertEqual(guest["auto_text"], "Гость отвечает Коротко.")
            self.assertAlmostEqual(sf.info(str(directory / guest["auto_file"])).duration, 1.74 + 1.94 + 0.3, places=1)

            project.stage_translate(directory, data, settings, "English")
            plan = [json.loads(line) for line in project.plan_path(directory, "English").read_text(encoding="utf-8").splitlines()]
            # The guest's two phrases merge into one unit; nothing merges across speakers.
            self.assertEqual([item["speaker"] for item in plan], ["S1", "S1", "S2", "S1"])
            self.assertEqual(plan[2]["source_segment_ids"], [3, 4])
            project.save_project(directory, data)

            voices = project.speaker_voices(directory, data, settings)
            self.assertEqual(Path(voices["S2"]["ref_audio"]).name, "original_s2.wav")
            self.assertEqual(voices["S2"]["ref_text"], "Гость отвечает Коротко.")

            detail = pipeline_ui.project_detail(directory)
            self.assertEqual([item["id"] for item in detail["speaker_list"]], ["S1", "S2"])
            self.assertEqual(detail["speaker_list"][1]["phrases"], 2)
            self.assertEqual([segment["speaker"] for segment in detail["segments"]], ["S1", "S1", "S2", "S1"])
        finally:
            server.close()

        # The user moves the last phrase to the guest: every language follows, a voiced one must re-voice it.
        data = project.load_project(directory)
        data["tracks"]["en"].update(voiced=True, exported=True)
        project.save_project(directory, data)
        project.set_phrase_speakers(directory, project.load_project(directory), {4: "S2", 3: "S1"})
        data = project.load_project(directory)
        self.assertEqual([p["speaker"] for p in project.load_phrases(directory)], ["S1", "S1", "S1", "S1", "S2"])
        self.assertEqual([u["speaker"] for u in project.load_units(directory, "English")], ["S1", "S1", "S1", "S2"])
        plan = [json.loads(line) for line in project.plan_path(directory, "English").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(plan[3]["speaker"], "S2")
        self.assertEqual(data["tracks"]["en"]["dirty"], [3, 4])
        self.assertFalse(data["tracks"]["en"]["exported"])
        with self.assertRaises(project.ProjectError):
            project.set_phrase_speakers(directory, data, {1: "S9"})

        # Names and voices are per speaker; a new speaker count re-splits the phrases.
        data = project.update_project(directory, {"speakers": {"S2": {"name": "Гость", "voice": {"mode": "engine", "voice_id": "alloy"}}}})
        self.assertEqual(data["speakers"]["S2"]["name"], "Гость")
        self.assertFalse(data["tracks"]["en"]["voiced"])
        self.assertTrue(data["tracks"]["en"]["translated"])
        data = project.update_project(directory, {"speaker_count": 1})
        self.assertFalse(data["diarized"])
        self.assertFalse(data["tracks"]["en"]["translated"])
        project.stage_diarize(directory, data, {"diarization": {"engine": "fake_diar"}})
        self.assertEqual(list(data["speakers"]), ["S1"])
        self.assertEqual({p["speaker"] for p in project.load_phrases(directory)}, {"S1"})

    def test_missing_engine_falls_back_to_one_voice(self):
        directory, data = self.make_project()
        project.stage_diarize(directory, data, {"diarization": {"engine": "no_such_engine"}})
        self.assertEqual(list(data["speakers"]), ["S1"])
        self.assertTrue(data["diarized"])


if __name__ == "__main__":
    unittest.main()
