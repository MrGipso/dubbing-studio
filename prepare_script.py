import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from llm_client import LLMError, add_llm_arguments, client_from_args, configure_stdio


SYSTEM_PROMPT = """\
You prepare a finished script for voiceover. Critical rule: do not rewrite,
shorten, expand, or translate the author's spoken words. Remove only production
notes that should not be spoken, such as Markdown headings, timecodes, visual
directions, and bracketed stage notes. Split spoken text into short natural
voiceover segments. Add emotion/tone labels and pauses for narration.
Bracketed notes such as [бодро], [спокойно], [шепотом], [пауза] are control
hints: use them to set emotion, tone, or pause, but never include them in text.

Return only valid JSON:
{
  "title": "short title",
  "language": "ru",
  "segments": [
    {
      "id": 0,
      "text": "spoken words only",
      "emotion": "neutral | excited | happy | sad | angry | whisper | surprised | serious | calm | laughing | sigh",
      "pause_before_ms": 0,
      "pause_after_ms": 300,
      "visual_prompt": "short English visual prompt for future video"
    }
  ]
}
Pauses must be 0-1200 ms. Keep 1 sentence or 1 short thought per segment.
"""

EMOTION_INSTRUCT = {
    "neutral": "",
    "calm": "",
    "serious": "low pitch",
    "excited": "high pitch",
    "happy": "high pitch",
    "sad": "low pitch",
    "angry": "low pitch",
    "whisper": "whisper",
    "surprised": "high pitch",
    "laughing": "high pitch",
    "sigh": "low pitch",
}

HINT_RULES = [
    (("бодр", "энерг", "живо", "динами", "excited", "energetic"), "excited", "high pitch"),
    (("весел", "радост", "улыб", "happy", "cheerful"), "happy", "high pitch"),
    (("спокой", "мягк", "тихо", "calm", "soft"), "calm", ""),
    (("серьез", "серьёз", "строг", "serious"), "serious", "low pitch"),
    (("груст", "печаль", "sad"), "sad", "low pitch"),
    (("зло", "сердит", "angry"), "angry", "low pitch"),
    (("шеп", "whisper"), "whisper", "whisper"),
    (("удив", "surpris"), "surprised", "high pitch"),
    (("смех", "сме", "laugh"), "laughing", "high pitch"),
    (("вздох", "sigh"), "sigh", "low pitch"),
]

VALID_OMNIVOICE_INSTRUCTS = {
    "american accent",
    "australian accent",
    "british accent",
    "canadian accent",
    "child",
    "chinese accent",
    "elderly",
    "female",
    "high pitch",
    "indian accent",
    "japanese accent",
    "korean accent",
    "low pitch",
    "male",
    "middle-aged",
    "moderate pitch",
    "portuguese accent",
    "russian accent",
    "teenager",
    "very high pitch",
    "very low pitch",
    "whisper",
    "young adult",
}
OMNIVOICE_INSTRUCT_CATEGORIES = {
    "male": "gender",
    "female": "gender",
    "child": "age",
    "teenager": "age",
    "young adult": "age",
    "middle-aged": "age",
    "elderly": "age",
    "very low pitch": "pitch",
    "low pitch": "pitch",
    "moderate pitch": "pitch",
    "high pitch": "pitch",
    "very high pitch": "pitch",
    "whisper": "style",
    "american accent": "accent",
    "australian accent": "accent",
    "british accent": "accent",
    "canadian accent": "accent",
    "chinese accent": "accent",
    "indian accent": "accent",
    "japanese accent": "accent",
    "korean accent": "accent",
    "portuguese accent": "accent",
    "russian accent": "accent",
}


def extract_json_object(text):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
            data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("Model response is not a JSON object.")
    return data


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def is_production_note_line(line):
    normalized = normalize_text(re.sub(r"\[[^\[\]]{1,80}\]", " ", line)).lower()
    normalized = normalized.strip(" -*_`#:\t")
    if not normalized:
        return True
    production_prefixes = (
        "видео-сценарий",
        "сценарий:",
        "пометки ",
        "пометка ",
        "курсивом ",
        "что показывать",
        "подсказки ",
        "техническ",
        "на экране",
        "в кадре",
        "кадр:",
        "визуал",
        "камера",
        "показать",
        "показываем",
        "сцена:",
        "титр",
        "экран:",
        "sfx",
        "звук:",
        "b-roll",
    )
    if normalized.startswith(production_prefixes):
        return True
    production_headings = {
        "холодный ввод",
        "вступление",
        "интро",
        "финал",
        "заключение",
        "основная часть",
        "крючок",
        "hook",
        "cold open",
        "intro",
        "outro",
    }
    if normalized in production_headings:
        return True
    return False


def clean_voiceover_text(raw_text, keep_hints=False):
    cleaned = []
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            continue
        if is_production_note_line(line):
            continue
        line = re.sub(r"\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]", " ", line)
        if not keep_hints:
            line = re.sub(r"\[[^\[\]]{1,80}\]", " ", line)
        line = re.sub(r"^[-–—\s]*#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip(" -*_`#\t")
        if line:
            cleaned.append(line)
    return " ".join(cleaned).strip()


def split_sentences(text):
    text = re.sub(r"\s+", " ", text.strip())
    return [part.strip() for part in re.split(r"(?<=[.!?\u2026])\s+", text) if part.strip()]


def hint_metadata(note):
    normalized = normalize_text(note).lower()
    pause_before = 0
    pause_after = 300
    if any(word in normalized for word in ("пауза", "pause", "выдерж", "тишина")):
        pause_before = 650
        pause_after = 650
    for markers, emotion, instruct in HINT_RULES:
        if any(marker in normalized for marker in markers):
            return {
                "emotion": emotion,
                "instruct": instruct,
                "pause_before_ms": pause_before,
                "pause_after_ms": pause_after,
            }
    if pause_before or pause_after != 300:
        return {
            "emotion": "neutral",
            "instruct": "",
            "pause_before_ms": pause_before,
            "pause_after_ms": pause_after,
        }
    return None


def sanitize_omnivoice_instruct(value):
    normalized = normalize_text(value).lower()
    if not normalized:
        return ""

    items = []
    used_categories = set()

    def add_item(item):
        if item not in VALID_OMNIVOICE_INSTRUCTS:
            return
        category = OMNIVOICE_INSTRUCT_CATEGORIES.get(item, item)
        if category in used_categories or item in items:
            return
        items.append(item)
        used_categories.add(category)

    for raw in re.split(r"[,;，]+", normalized):
        item = raw.strip()
        add_item(item)

    phrase_rules = [
        (("whisper", "whispering", "шеп"), "whisper"),
        (("very low pitch", "очень низ"), "very low pitch"),
        (("very high pitch", "очень высок"), "very high pitch"),
        (("low pitch", "low voice", "serious", "sad", "sigh", "низ", "серьез", "серьёз", "груст", "вздох"), "low pitch"),
        (("high pitch", "excited", "happy", "bright", "energetic", "laugh", "бодр", "энерг", "весел", "радост", "смех", "удив"), "high pitch"),
        (("moderate pitch", "calm", "neutral", "спокой", "нейтр"), "moderate pitch"),
        (("male", "муж"), "male"),
        (("female", "жен"), "female"),
        (("young adult", "молод"), "young adult"),
        (("middle-aged", "средн"), "middle-aged"),
        (("elderly", "стар"), "elderly"),
        (("child", "реб"), "child"),
        (("russian accent", "русск"), "russian accent"),
        (("portuguese accent", "португал"), "portuguese accent"),
    ]
    for markers, item in phrase_rules:
        if any(marker in normalized for marker in markers):
            add_item(item)

    return ", ".join(items)


def append_hinted_sentences(segments, text, state):
    for sentence in split_sentences(text):
        segment = {
            "id": len(segments),
            "text": sentence,
            "emotion": state.get("emotion", "neutral"),
            "pause_before_ms": state.get("pause_before_ms", 0),
            "pause_after_ms": state.get("pause_after_ms", 300),
            "visual_prompt": "",
        }
        if state.get("instruct"):
            segment["instruct"] = sanitize_omnivoice_instruct(state["instruct"])
        segments.append(segment)
        state["pause_before_ms"] = 0


def simple_script(raw_text, language="ru"):
    segments = []
    text = clean_voiceover_text(raw_text, keep_hints=True)
    state = {"emotion": "neutral", "instruct": "", "pause_before_ms": 0, "pause_after_ms": 300}
    for part in re.split(r"(\[[^\[\]]{1,80}\])", text):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            metadata = hint_metadata(part[1:-1])
            if metadata:
                state.update(metadata)
            continue
        append_hinted_sentences(segments, part, state)
    return {"title": "Prepared script", "language": language, "segments": segments}


def build_messages(text, language, style):
    user = {
        "task": "prepare_script_for_omnivoice_voiceover",
        "language": language,
        "style": style,
        "script_text": text,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "language": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "text": {"type": "string"},
                    "emotion": {"type": "string"},
                    "instruct": {"type": "string"},
                    "pause_before_ms": {"type": "integer"},
                    "pause_after_ms": {"type": "integer"},
                    "visual_prompt": {"type": "string"},
                },
                "required": ["id", "text", "emotion", "pause_before_ms", "pause_after_ms", "visual_prompt"],
            },
        },
    },
    "required": ["title", "language", "segments"],
}


def call_llm(llm, text, args):
    messages = build_messages(text, args.language, args.style)
    last_error = None
    for attempt in range(1, args.retries + 1):
        try:
            content = llm.complete(
                messages,
                temperature=args.temperature,
                timeout=args.timeout,
                schema=SCRIPT_SCHEMA,
            )
            return extract_json_object(content)
        except (LLMError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < args.retries:
            wait = min(2 * attempt, 8)
            print(f"retry {attempt}/{args.retries}: {last_error}; waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Script preparation failed ({llm.describe()}): {last_error}")


def normalize_script(data, fallback_title, language):
    raw_segments = data.get("segments") or []
    segments = []
    for raw in raw_segments:
        text = normalize_text(raw.get("text") if isinstance(raw, dict) else "")
        text = normalize_text(re.sub(r"\[[^\[\]]{1,80}\]", " ", text))
        if is_production_note_line(text):
            continue
        if not text:
            continue
        emotion = normalize_text(raw.get("emotion", "neutral")).lower() or "neutral"
        segment = {
            "id": len(segments),
            "text": text,
            "emotion": emotion,
            "pause_before_ms": max(0, min(1200, int(raw.get("pause_before_ms", 0) or 0))),
            "pause_after_ms": max(0, min(1200, int(raw.get("pause_after_ms", 300) or 300))),
            "visual_prompt": normalize_text(raw.get("visual_prompt", "")),
        }
        instruct = sanitize_omnivoice_instruct(raw.get("instruct", ""))
        if instruct:
            segment["instruct"] = instruct
        segments.append(segment)
    if not segments:
        raise RuntimeError("No voiceover segments were produced.")
    return {
        "title": normalize_text(data.get("title")) or fallback_title,
        "language": normalize_text(data.get("language")) or language,
        "segments": segments,
    }


def estimate_voice_duration(text):
    words = len(re.findall(r"\S+", text))
    chars = len(text)
    return round(max(1.2, words / 2.45 + 0.55, chars / 18.0), 2)


def language_slug(language):
    return re.sub(r"[^a-z0-9_-]+", "-", str(language or "script").lower()).strip("-") or "script"


def safe_prefix(value, fallback):
    raw = str(value or "").strip() or fallback
    safe = re.sub(r"\s+", "_", raw)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", safe).strip("_")
    return safe or fallback


def write_outputs(script, args, input_path):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fallback_prefix = f"script_{time.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1(input_path.read_bytes()).hexdigest()[:8]}"
    prefix = safe_prefix(args.output_prefix, fallback_prefix)
    script_json = output_dir / f"{prefix}.script.json"
    jsonl = output_dir / f"{prefix}.{language_slug(args.language_id)}.script.dub.jsonl"

    payload = {
        "source_text_file": str(input_path.resolve()),
        "title": script["title"],
        "language": script["language"],
        "language_id": args.language_id,
        "style": args.style,
        "segments": script["segments"],
    }
    script_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cursor = 0.0
    with jsonl.open("w", encoding="utf-8") as f:
        for segment in script["segments"]:
            cursor += int(segment.get("pause_before_ms") or 0) / 1000.0
            duration = estimate_voice_duration(segment["text"])
            item = {
                "id": f"script_{int(segment['id']):04d}",
                "text": segment["text"],
                "duration": duration,
                "source_start": round(cursor, 3),
                "source_end": round(cursor + duration, 3),
                "language_id": args.language_id,
                "emotion": segment.get("emotion", "neutral"),
                "source_segment_id": int(segment["id"]),
            }
            instruct_parts = []
            if segment.get("instruct"):
                instruct_parts.append(str(segment["instruct"]))
            emotion_instruct = EMOTION_INSTRUCT.get(str(segment.get("emotion") or "").lower(), "")
            if emotion_instruct and emotion_instruct not in instruct_parts:
                instruct_parts.append(emotion_instruct)
            instruct = sanitize_omnivoice_instruct(", ".join(instruct_parts))
            if instruct:
                item["instruct"] = instruct
            if args.ref_audio:
                item["ref_audio"] = args.ref_audio
            if args.ref_text:
                item["ref_text"] = args.ref_text
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            cursor += duration + int(segment.get("pause_after_ms") or 0) / 1000.0

    return script_json, jsonl


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="Prepare a script for voiceover through any LLM provider.")
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--language-id", default="ru")
    add_llm_arguments(parser)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--style", default="Natural YouTube narration, clear pacing, keep author's wording.")
    parser.add_argument("--ref-audio", default="")
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--simple", action="store_true")
    parser.add_argument("--no-fallback-simple-on-error", action="store_false", dest="fallback_simple_on_error")
    parser.set_defaults(fallback_simple_on_error=True)
    args = parser.parse_args()

    input_path = Path(args.text_file)
    raw_text = input_path.read_text(encoding="utf-8", errors="replace")
    spoken_text = clean_voiceover_text(raw_text, keep_hints=False)
    hinted_text = clean_voiceover_text(raw_text, keep_hints=True)
    if not spoken_text:
        raise RuntimeError("Input script is empty after cleanup.")

    if args.simple:
        data = simple_script(raw_text, args.language)
    else:
        llm = client_from_args(args)
        try:
            llm.prepare()
            print("Preparing script segments through the LLM", flush=True)
            data = call_llm(llm, hinted_text, args)
        except (RuntimeError, LLMError) as exc:
            if not args.fallback_simple_on_error:
                raise
            print(f"WARNING: {exc}", file=sys.stderr)
            print("Falling back to deterministic sentence split.", file=sys.stderr)
            data = simple_script(raw_text, args.language)
        finally:
            llm.finish()

    script = normalize_script(data, fallback_title=input_path.stem, language=args.language)
    script_json, jsonl = write_outputs(script, args, input_path)

    print(f"segments={len(script['segments'])}")
    print(f"script_json={script_json.resolve()}")
    print(f"dub_jsonl={jsonl.resolve()}")


if __name__ == "__main__":
    main()
