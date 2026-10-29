import argparse
import json
import re
import sys
import time
from pathlib import Path

from llm_client import LLMError, add_llm_arguments, client_from_args, configure_stdio


# Structured-output schema: servers that support it constrain decoding to valid
# JSON, which makes even small local models usable for batch translation.
TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "translation": {"type": "string"},
                },
                "required": ["id", "translation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
SENTENCE_END_RE = re.compile(r"[.!?\u2026]+[\"')\]\u00bb\u201d]*$")


def parse_time(value):
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000
    )


def format_time(seconds):
    millis = int(round(seconds * 1000))
    hours = millis // 3_600_000
    millis %= 3_600_000
    minutes = millis // 60_000
    millis %= 60_000
    secs = millis // 1000
    millis %= 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def parse_srt(path):
    raw = Path(path).read_text(encoding="utf-8-sig")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    segments = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        index = None
        timestamp_line = None
        text_lines = []

        if lines[0].isdigit():
            index = int(lines[0])
            if len(lines) > 1:
                timestamp_line = lines[1]
                text_lines = lines[2:]
        else:
            timestamp_line = lines[0]
            text_lines = lines[1:]

        match = TIMESTAMP_RE.search(timestamp_line or "")
        if not match:
            raise ValueError(f"Could not parse SRT timestamp block: {block!r}")

        start_s = match.group("start")
        end_s = match.group("end")
        start = parse_time(start_s)
        end = parse_time(end_s)
        text = " ".join(text_lines).strip()
        if not text:
            continue

        segment_id = index if index is not None else len(segments) + 1
        segments.append(
            {
                "id": segment_id,
                "start": start,
                "end": end,
                "start_srt": start_s,
                "end_srt": end_s,
                "duration": round(max(0.0, end - start), 3),
                "source": text,
                "translation": "",
            }
        )
    return segments


def sentence_is_closed(text):
    return bool(SENTENCE_END_RE.search((text or "").strip()))


def make_translation_unit(unit_id, group):
    source = " ".join(seg["source"].strip() for seg in group if seg.get("source")).strip()
    start = float(group[0]["start"])
    end = float(group[-1]["end"])
    return {
        "id": unit_id,
        "start": start,
        "end": end,
        "start_srt": format_time(start),
        "end_srt": format_time(end),
        "duration": round(max(0.0, end - start), 3),
        "source": source,
        "translation": "",
        "segment_ids": [int(seg["id"]) for seg in group],
        "segment_count": len(group),
    }


def build_translation_units(segments, merge_phrases, max_gap, max_duration):
    if not merge_phrases:
        return [
            {
                **seg,
                "segment_ids": [int(seg["id"])],
                "segment_count": 1,
            }
            for seg in segments
        ]

    units = []
    group = []
    for seg in segments:
        if not group:
            group = [seg]
            continue

        previous = group[-1]
        gap = float(seg["start"]) - float(previous["end"])
        merged_duration = float(seg["end"]) - float(group[0]["start"])
        can_merge = (
            not sentence_is_closed(previous["source"])
            and gap <= max_gap
            and merged_duration <= max_duration
        )

        if can_merge:
            group.append(seg)
            continue

        units.append(make_translation_unit(len(units) + 1, group))
        group = [seg]

    if group:
        units.append(make_translation_unit(len(units) + 1, group))

    return units


def write_srt(path, segments):
    blocks = []
    for output_index, seg in enumerate(segments, start=1):
        text = seg.get("translation") or seg["source"]
        blocks.append(
            f"{output_index}\n"
            f"{format_time(seg['start'])} --> {format_time(seg['end'])}\n"
            f"{text.strip()}\n"
        )
    Path(path).write_text("\n".join(blocks), encoding="utf-8")


def slugify(value):
    value = value.strip().lower()
    common = {
        "english": "en",
        "russian": "ru",
        "ukrainian": "uk",
        "spanish": "es",
        "german": "de",
        "french": "fr",
        "polish": "pl",
        "italian": "it",
    }
    value = common.get(value, value)
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    return value.strip("-") or "translated"


def extract_json_array(text):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise
        value = json.loads(text[start : end + 1])

    if isinstance(value, dict):
        for key in ("items", "translations", "dubbing_units", "result"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        raise ValueError("Model response is not a JSON array.")
    return value


def build_messages(batch, source_language, target_language, style, context=None):
    payload = []
    for seg in batch:
        duration = seg["duration"]
        payload.append(
            {
                "id": seg["id"],
                "duration_sec": duration,
                "max_words_hint": max(3, round(duration * 2.6)),
                "source_segment_ids": seg.get("segment_ids") or [seg["id"]],
                "source": seg["source"],
            }
        )

    system = (
        "You are a dubbing translator. "
        "Each item may contain one Whisper fragment or several merged fragments from one spoken phrase. "
        "Translate meaning naturally, keep each item concise enough to be spoken "
        "inside its duration, preserve IDs exactly, and return only valid JSON."
    )
    user = {
        "task": "translate_timed_dubbing_units",
        "source_language": source_language,
        "target_language": target_language,
        "style": style,
        "rules": [
            "Return a JSON object only: {\"items\": [...]}.",
            "Each item must be {\"id\": number, \"translation\": string}.",
            "No explanations, markdown, comments, or extra keys.",
            "Keep names, numbers, and intent intact.",
            "Prefer natural spoken phrasing over literal translation.",
            "If an item is too long for its duration, shorten without losing the core meaning.",
            "Keep merged fragments as one natural spoken sentence when possible.",
        ],
        "dubbing_units": payload,
    }
    if context:
        # Already translated neighbours keep names, terms and tone consistent
        # across batches. They are reference only and must not be returned.
        user["rules"].append(
            "previous_lines are already translated; use them only for consistent terms and tone, do not return them."
        )
        user["previous_lines"] = [
            {"source": unit["source"], "translation": unit["translation"]} for unit in context
        ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def translate_batch(llm, batch, args, context=None):
    messages = build_messages(
        batch=batch,
        source_language=args.source_language,
        target_language=args.target_language,
        style=args.style,
        context=context,
    )

    last_error = None
    for attempt in range(1, args.retries + 1):
        try:
            content = llm.complete(
                messages,
                temperature=args.temperature,
                timeout=args.timeout,
                schema=TRANSLATION_SCHEMA,
            )
            parsed = extract_json_array(content)
            by_id = {}
            for item in parsed:
                if not isinstance(item, dict):
                    raise ValueError("Translation item is not an object.")
                item_id = int(item["id"])
                translation = str(item["translation"]).strip()
                if not translation:
                    raise ValueError(f"Empty translation for segment {item_id}.")
                by_id[item_id] = translation

            missing = [seg["id"] for seg in batch if seg["id"] not in by_id]
            if missing:
                raise ValueError(f"Missing translated IDs: {missing}")
            return by_id
        except (LLMError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < args.retries:
                wait = min(2 * attempt, 8)
                print(f"retry {attempt}/{args.retries}: {exc}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Translation failed after {args.retries} tries ({llm.describe()}): {last_error}")


def load_resume(json_path):
    if not json_path.exists():
        return {}
    data = json.loads(json_path.read_text(encoding="utf-8"))
    translated = {}
    for seg in data.get("translation_units") or data.get("segments", []):
        text = (seg.get("translation") or "").strip()
        if text:
            translated[int(seg["id"])] = text
    return translated


def translate_pending(llm, translation_units, args, save):
    pending = [unit for unit in translation_units if not unit.get("translation")]
    for offset in range(0, len(pending), args.batch_size):
        batch = pending[offset : offset + args.batch_size]
        ids = ", ".join(str(unit["id"]) for unit in batch)
        if args.dry_run:
            translations = {unit["id"]: f"[DRY RUN] {unit['source']}" for unit in batch}
        else:
            print(f"Translating dubbing units: {ids}", flush=True)
            first_id = batch[0]["id"]
            context = [
                unit for unit in translation_units
                if unit.get("translation") and unit["id"] < first_id
            ][-args.context_lines:] if args.context_lines > 0 else None
            translations = translate_batch(llm, batch, args, context=context)

        for unit in translation_units:
            if unit["id"] in translations:
                unit["translation"] = translations[unit["id"]]

        # Save progress after every batch for resumability. Incomplete media-facing
        # artifacts use a .partial marker so the next dubbing stage does not pick
        # up empty segment text by accident.
        save(all(unit.get("translation") for unit in translation_units))


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="Translate Whisper SRT segments through any LLM provider.")
    parser.add_argument("srt", help="Input SRT from Whisper/faster-whisper")
    parser.add_argument("--target-language", default="English")
    parser.add_argument("--source-language", default="Russian")
    add_llm_arguments(parser)
    parser.add_argument("--output-dir", default="", help="Defaults to the input SRT folder.")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--context-lines", type=int, default=4, help="Already translated lines sent as context to the next batch.")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--style", default="Natural spoken dubbing, concise timing-preserving translation.")
    parser.add_argument("--merge-phrases", action=argparse.BooleanOptionalAction, default=True, help="Merge adjacent Whisper fragments into sentence-like dubbing units.")
    parser.add_argument("--phrase-max-gap", type=float, default=0.8, help="Maximum pause in seconds between fragments that may be merged.")
    parser.add_argument("--phrase-max-duration", type=float, default=12.0, help="Maximum merged dubbing unit duration in seconds.")
    parser.add_argument("--target-language-id", default="", help="Optional language code (en, de, ...) for the voice JSONL.")
    parser.add_argument("--ref-audio", default="", help="Optional voice reference audio for the voice JSONL.")
    parser.add_argument("--ref-text", default="", help="Optional transcript of the voice reference.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.srt)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    target_slug = slugify(args.target_language)
    stem = input_path.stem
    srt_out = output_dir / f"{stem}.{target_slug}.srt"
    txt_out = output_dir / f"{stem}.{target_slug}.txt"
    json_out = output_dir / f"{stem}.{target_slug}.segments.json"
    jsonl_out = output_dir / f"{stem}.{target_slug}.dub.jsonl"

    segments = parse_srt(input_path)
    if not segments:
        raise RuntimeError(f"No subtitle segments found in {input_path}")

    translation_units = build_translation_units(
        segments,
        merge_phrases=args.merge_phrases,
        max_gap=max(0.0, args.phrase_max_gap),
        max_duration=max(0.1, args.phrase_max_duration),
    )
    if args.merge_phrases:
        print(
            f"Phrase Merge: {len(segments)} Whisper segments -> "
            f"{len(translation_units)} dubbing units",
            flush=True,
        )

    if args.resume:
        resumed = load_resume(json_out)
        for unit in translation_units:
            if unit["id"] in resumed:
                unit["translation"] = resumed[unit["id"]]

    def save(complete):
        write_outputs(
            json_out=json_out,
            srt_out=srt_out,
            txt_out=txt_out,
            jsonl_out=jsonl_out,
            segments=segments,
            translation_units=translation_units,
            args=args,
            input_path=input_path,
            complete=complete,
        )

    llm = client_from_args(args)
    try:
        if not args.dry_run:
            llm.prepare()
        translate_pending(llm, translation_units, args, save)
    finally:
        # Unload even after a failed run so the model does not hold VRAM.
        if not args.dry_run:
            llm.finish()

    if all(unit.get("translation") for unit in translation_units):
        save(True)

    print(f"srt={srt_out.resolve()}")
    print(f"txt={txt_out.resolve()}")
    print(f"segments_json={json_out.resolve()}")
    print(f"dub_jsonl={jsonl_out.resolve()}")


def partial_path(path):
    path = Path(path)
    return path.with_name(f"{path.stem}.partial{path.suffix}")


def write_outputs(json_out, srt_out, txt_out, jsonl_out, segments, translation_units, args, input_path, complete):
    metadata = {
        "input_srt": str(input_path.resolve()),
        "source_language": args.source_language,
        "target_language": args.target_language,
        "llm_provider": args.llm_provider,
        "llm_url": args.llm_url,
        "model": args.model,
        "style": args.style,
        "merge_phrases": args.merge_phrases,
        "phrase_max_gap": args.phrase_max_gap,
        "phrase_max_duration": args.phrase_max_duration,
        "source_segment_count": len(segments),
        "translation_unit_count": len(translation_units),
        "status": "complete" if complete else "partial",
    }
    data = {
        "metadata": metadata,
        "segments": segments,
        "translation_units": translation_units,
    }
    json_out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    media_srt_out = srt_out if complete else partial_path(srt_out)
    media_txt_out = txt_out if complete else partial_path(txt_out)
    media_jsonl_out = jsonl_out if complete else partial_path(jsonl_out)

    write_srt(media_srt_out, translation_units)
    media_txt_out.write_text(
        "\n".join((unit.get("translation") or "").strip() for unit in translation_units).strip() + "\n",
        encoding="utf-8",
    )

    with media_jsonl_out.open("w", encoding="utf-8") as f:
        for unit in translation_units:
            item = {
                "id": f"unit_{int(unit['id']):04d}",
                "text": (unit.get("translation") or "").strip(),
                "duration": unit["duration"],
                "source_start": unit["start"],
                "source_end": unit["end"],
                "source_segment_ids": unit.get("segment_ids") or [int(unit["id"])],
                "source_segment_count": unit.get("segment_count") or 1,
            }
            if args.target_language_id:
                item["language_id"] = args.target_language_id
            if args.ref_audio:
                item["ref_audio"] = args.ref_audio
            if args.ref_text:
                item["ref_text"] = args.ref_text
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    if complete:
        for path in (srt_out, txt_out, jsonl_out):
            pending_path = partial_path(path)
            if pending_path.exists():
                pending_path.unlink()


if __name__ == "__main__":
    main()
