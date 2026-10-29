import argparse
import hashlib
import html
import json
import re
import sys
import time
from pathlib import Path

from llm_client import LLMError, add_llm_arguments, client_from_args


TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3}|\d{1,2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3}|\d{1,2}:\d{2}[,.]\d{3})"
)


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_time(value):
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_clock(seconds):
    total = max(0, int(round(seconds)))
    hours = total // 3600
    total %= 3600
    minutes = total // 60
    secs = total % 60
    if hours:
        return f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{minutes:02}:{secs:02}"


def strip_subtitle_text(line):
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"\{\\[^}]+\}", " ", line)
    line = re.sub(r"^\s*[-–—]\s*", "", line)
    line = html.unescape(line)
    return re.sub(r"\s+", " ", line).strip()


def parse_subtitles(path):
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", raw)
    segments = []

    for block in blocks:
        lines = [line.strip("\ufeff ") for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue

        timestamp_index = -1
        timestamp_match = None
        for index, line in enumerate(lines[:3]):
            timestamp_match = TIME_RE.search(line)
            if timestamp_match:
                timestamp_index = index
                break
        if timestamp_index < 0 or not timestamp_match:
            continue

        text_lines = []
        for line in lines[timestamp_index + 1 :]:
            if re.fullmatch(r"\d+", line):
                continue
            cleaned = strip_subtitle_text(line)
            if cleaned:
                text_lines.append(cleaned)
        text = " ".join(text_lines).strip()
        if not text:
            continue

        segments.append(
            {
                "start": parse_time(timestamp_match.group("start")),
                "end": parse_time(timestamp_match.group("end")),
                "text": text,
            }
        )

    deduped = []
    last_text = ""
    for item in segments:
        normalized = re.sub(r"\W+", "", item["text"].lower())
        if normalized and normalized == last_text:
            continue
        last_text = normalized
        deduped.append(item)
    return deduped


def transcript_text(segments):
    lines = []
    for item in segments:
        lines.append(f"[{format_clock(item['start'])} - {format_clock(item['end'])}] {item['text']}")
    return "\n".join(lines)


WORDS_PER_MINUTE = {
    # calibrated from real OmniVoice dub output (126-132 wpm measured for Russian)
    "russian": 130,
    "english": 145,
    "spanish": 150,
    "portuguese": 140,
    "bulgarian": 120,
}


def words_per_minute(language):
    return WORDS_PER_MINUTE.get(str(language or "").strip().lower(), 130)


def count_words(text):
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", str(text or ""), flags=re.UNICODE))


LATIN_STOPWORDS = {
    "English": {"the", "and", "of", "to", "is", "that", "was", "with", "for", "this", "have", "are"},
    "Spanish": {"que", "de", "la", "el", "en", "los", "las", "una", "por", "para", "está", "pero"},
    "Portuguese": {"que", "de", "não", "uma", "para", "com", "por", "mais", "isso", "você", "está", "são"},
}


def detect_language(text):
    sample = str(text or "")[:20000].lower()
    letters = re.findall(r"[^\W\d_]", sample, flags=re.UNICODE)
    if not letters:
        return "English"
    cyrillic = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    if cyrillic / len(letters) > 0.4:
        return "Russian" if "ы" in sample else "Bulgarian"
    words = set(re.findall(r"[^\W\d_]+", sample, flags=re.UNICODE))
    scores = {lang: len(words & stops) for lang, stops in LATIN_STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "English"


def slugify(value):
    value = re.sub(r"\s+", "_", str(value or "").strip())
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return value or "content"


def default_output_prefix(path):
    resolved = str(Path(path).resolve()).lower()
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
    return f"content_{time.strftime('%Y%m%d_%H%M%S')}_{digest}_{slugify(Path(path).stem)[:60]}"


def extract_json_object(text):
    text = str(text or "").strip()
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
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object.")
    return data


def chat_json(llm, messages, args):
    last_error = None
    for attempt in range(1, args.retries + 1):
        try:
            content = llm.complete(messages, temperature=args.temperature, timeout=args.timeout, json_mode=True)
            return extract_json_object(content)
        except (LLMError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < args.retries:
                wait = min(2 * attempt, 8)
                print(f"retry {attempt}/{args.retries}: {exc}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Content generation failed ({llm.describe()}): {last_error}")


def chat_text(llm, messages, args, label):
    last_error = None
    for attempt in range(1, args.retries + 1):
        try:
            content = llm.complete(messages, temperature=args.temperature, timeout=args.timeout).strip()
            fenced = re.search(r"```(?:text|markdown)?\s*(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
            if fenced:
                content = fenced.group(1).strip()
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and parsed.get("script"):
                    content = str(parsed["script"]).strip()
            except json.JSONDecodeError:
                pass
            if not content:
                raise ValueError(f"Empty {label} response.")
            return content
        except (LLMError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < args.retries:
                wait = min(2 * attempt, 8)
                print(f"retry {attempt}/{args.retries}: {exc}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"{label} generation failed ({llm.describe()}): {last_error}")


def chunk_text(text, max_chars):
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def summarize_chunk(llm, chunk, index, total, args):
    messages = [
        {
            "role": "system",
            "content": (
                "You extract useful source material from subtitles for ethical YouTube script writing. "
                "Return only valid JSON."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "summarize_subtitle_chunk",
                    "chunk_index": index,
                    "chunk_count": total,
                    "source_language": args.source_language,
                    "rules": [
                        "Return exactly one JSON object.",
                        "Keys: summary, key_points, names_and_facts, usable_hooks, visual_moments.",
                        "Do not invent facts that are not in the subtitles.",
                    ],
                    "subtitles": chunk,
                },
                ensure_ascii=False,
            ),
        },
    ]
    return chat_json(llm, messages, args)


def build_source_material(transcript, llm, args):
    if len(transcript) <= args.direct_chars:
        return transcript, []

    chunks = chunk_text(transcript, args.chunk_chars)
    summaries = []
    for index, chunk in enumerate(chunks, start=1):
        print(f"Summarizing subtitle chunk {index}/{len(chunks)}")
        summaries.append(summarize_chunk(llm, chunk, index, len(chunks), args))

    compact_lines = []
    for index, summary in enumerate(summaries, start=1):
        compact_lines.append(f"Chunk {index}: {summary.get('summary', '')}")
        for key in ("key_points", "names_and_facts", "usable_hooks", "visual_moments"):
            values = summary.get(key) or []
            if values:
                compact_lines.append(f"{key}:")
                compact_lines.extend(f"- {item}" for item in values)
    return "\n".join(compact_lines), summaries


def rewrite_depth_rules(depth):
    depth = (depth or "strong").strip().lower()
    common = [
        "Treat the subtitles as research notes, not as a draft script.",
        "Do not translate line by line.",
        "Do not keep the same sentence count, paragraph order, or subtitle rhythm.",
        "Do not preserve the original hook, intro, transitions, or ending if a stronger structure is possible.",
        "Keep factual claims grounded in the source; do not invent unverifiable facts.",
    ]
    if depth in ("balanced", "careful", "light"):
        return common + [
            "Keep the same main topic and most important beats, but rewrite every section in fresh spoken language.",
            "You may keep the broad order only when it is already the clearest order.",
            "Remove subtitle fragments, repetitions, filler, and weak transitions.",
        ]
    if depth in ("new_script", "new-script", "original"):
        return common + [
            "Build a new script angle from scratch: new hook, new section order, new transitions, new conclusion.",
            "Use the source only as a fact bank and inspiration.",
            "Recombine ideas into acts or chapters that feel intentionally written for YouTube narration.",
            "The finished script should not be recognizable as a sentence-by-sentence version of the source.",
        ]
    return common + [
        "Strongly reorganize the material into a better YouTube story arc.",
        "Create a new hook and ending; combine, split, and reorder points where it improves retention.",
        "Rewrite examples, setups, and transitions in your own words.",
        "The result should feel like an original script based on the same topic, not a translation.",
    ]


def build_script_messages(source_material, args):
    system = (
        "You are a senior YouTube scriptwriter and story editor. "
        "Your job is rewriting and restructuring, not translation. "
        "Create an original voiceover script from source subtitles and research notes. "
        "If source and target languages differ, understand the source first, then write a new script in the target language. "
        "Do not return JSON. Return only the final script text."
    )
    user = {
        "task": "write_original_youtube_script_from_subtitles",
        "source_language": args.source_language,
        "target_language": args.target_language,
        "mode": args.mode,
        "rewrite_depth": args.rewrite_depth,
        "target_duration_minutes": args.target_duration_minutes,
        "target_word_count": getattr(args, "target_word_count", 0),
        "narration_style": args.style,
        "rules": [
            "Write in target_language.",
            "Use the source for facts and topic context, but create a new structure.",
            "Make the opening hook strong and natural.",
            "Keep it suitable for spoken voiceover.",
            (
                f"The script must contain about {getattr(args, 'target_word_count', 0)} words "
                f"(acceptable range {int(getattr(args, 'target_word_count', 0) * 0.9)}-"
                f"{int(getattr(args, 'target_word_count', 0) * 1.1)} words). This is a hard requirement."
            ),
            "Do not add markdown headings unless they should be spoken.",
            "Do not mention subtitles, transcript, or source file.",
        ],
        "rewrite_rules": rewrite_depth_rules(args.rewrite_depth),
        "source_material": source_material,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def build_visual_messages(script, args):
    visual_count_hint = max(3, int(args.visual_count or 12))
    system = (
        "You are a YouTube storyboard artist and image prompt writer. "
        "Return only valid JSON."
    )
    user = {
        "task": "create_storyboard_and_image_prompts_from_script",
        "target_language": args.target_language,
        "image_aspect_ratio": args.aspect_ratio,
        "image_style": args.image_style,
        "visual_count_hint": visual_count_hint,
        "rules": [
            "Return exactly one JSON object.",
            "Keys: title, hook, description, storyboard, image_prompts, publish.",
            "Storyboard should group the script into meaningful visual scenes, not one image per sentence.",
            "Each storyboard item: scene, narration, visual, image_prompt, duration_hint_sec.",
            "Each image_prompts item: id, scene, prompt, negative_prompt, aspect_ratio.",
            "Image prompts must be in English and specific enough for image generation.",
            "Publish should include youtube_title, description, tags.",
            "Do not include the full script in this JSON.",
        ],
        "script": script,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def script_chunks_for_visuals(script, count):
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n+", script) if item.strip()]
    if not paragraphs:
        paragraphs = [re.sub(r"\s+", " ", script).strip()]
    target = max(1, min(count, len(paragraphs)))
    chunks = []
    for index in range(target):
        start = round(index * len(paragraphs) / target)
        end = round((index + 1) * len(paragraphs) / target)
        chunk = " ".join(paragraphs[start:end]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks or paragraphs[:target]


def fallback_visual_package(script, args):
    chunks = script_chunks_for_visuals(script, max(3, int(args.visual_count or 12)))
    storyboard = []
    image_prompts = []
    for index, chunk in enumerate(chunks, start=1):
        excerpt = chunk[:240].strip()
        storyboard.append(
            {
                "scene": index,
                "narration": excerpt,
                "visual": f"Visual scene based on narration segment {index}",
                "image_prompt": f"{args.image_style}, {excerpt}, no text overlay, {args.aspect_ratio}",
                "duration_hint_sec": 30,
            }
        )
        image_prompts.append(
            {
                "id": f"image_{index:03d}",
                "scene": index,
                "prompt": f"{args.image_style}, documentary visual inspired by: {excerpt}, no text overlay, {args.aspect_ratio}",
                "negative_prompt": "low quality, blurry, unreadable text, watermark, logo",
                "aspect_ratio": args.aspect_ratio,
            }
        )
    return {
        "title": "Generated YouTube Script",
        "hook": chunks[0][:180] if chunks else "",
        "description": "",
        "storyboard": storyboard,
        "image_prompts": image_prompts,
        "publish": {
            "youtube_title": "Generated YouTube Script",
            "description": "",
            "tags": [],
        },
    }


def build_length_fix_messages(script, actual_words, args):
    target = getattr(args, "target_word_count", 0)
    direction = "expand" if actual_words < target else "shorten"
    system = (
        "You are a senior YouTube script editor. "
        "Adjust the length of the script without changing its topic, structure, facts, or language. "
        "Return only the final script text."
    )
    user = {
        "task": f"{direction}_script_to_target_length",
        "target_language": args.target_language,
        "current_word_count": actual_words,
        "target_word_count": target,
        "rules": [
            f"Rewrite the script so it contains about {target} words "
            f"({int(target * 0.9)}-{int(target * 1.1)} words). This is a hard requirement.",
            "Keep the same language, hook, structure, and facts.",
            "Expand with detail and natural spoken elaboration, or trim redundancy - do not pad with filler.",
            "Return only the script text, no comments.",
        ],
        "script": script,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def report_script_length(script, args, label=""):
    words = count_words(script)
    wpm = words_per_minute(args.target_language)
    minutes = words / wpm if wpm else 0
    tag = f" ({label})" if label else ""
    print(
        f"Script length{tag}: {words} words ~= {minutes:.1f} min at {wpm} wpm "
        f"(target {getattr(args, 'target_word_count', 0)} words / {args.target_duration_minutes:g} min)"
    )
    return words


SECTION_WORD_BUDGET = 400
SECTIONED_THRESHOLD_WORDS = 700


def build_outline_messages(source_material, args, section_count, words_per_section):
    system = (
        "You are a senior YouTube story editor. "
        "Design a section-by-section outline for a voiceover script. "
        "Return only valid JSON."
    )
    user = {
        "task": "build_script_outline",
        "target_language": args.target_language,
        "mode": args.mode,
        "narration_style": args.style,
        "target_word_count": args.target_word_count,
        "section_count": section_count,
        "words_per_section": words_per_section,
        "rules": [
            "Return exactly one JSON object with key: sections.",
            f"sections must be a list of exactly {section_count} items.",
            "Each item: title, goal, key_points (list of 2-5 strings).",
            "Section 1 is the hook, the last section is the conclusion.",
            "Cover the most interesting material from the source; do not repeat points across sections.",
            "Write titles and points in target_language.",
        ],
        "rewrite_rules": rewrite_depth_rules(args.rewrite_depth),
        "source_material": source_material,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def build_section_messages(source_material, section, index, total, previous_tail, args, word_budget):
    system = (
        "You are a senior YouTube scriptwriter. "
        "Write one section of a voiceover script. "
        "Return only the section text, no headings, no comments."
    )
    user = {
        "task": "write_script_section",
        "target_language": args.target_language,
        "narration_style": args.style,
        "section_index": index,
        "section_count": total,
        "section": section,
        "previous_section_ending": previous_tail,
        "target_word_count": word_budget,
        "rules": [
            "Write in target_language.",
            f"The section must contain about {word_budget} words "
            f"({int(word_budget * 0.85)}-{int(word_budget * 1.2)} words). This is a hard requirement.",
            "Continue naturally from previous_section_ending, do not repeat it.",
            "No greetings or wrap-up unless this is the first or last section.",
            "Keep it suitable for spoken voiceover; no markdown headings.",
            "Do not mention subtitles, transcript, sections, or source file.",
            "Ground facts in the source material.",
        ],
        "source_material": source_material,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def generate_sectioned_script(source_material, llm, args):
    target = args.target_word_count
    section_count = max(3, int(round(target / SECTION_WORD_BUDGET)))
    # models systematically undershoot, so ask for ~15% more than the raw budget
    words_per_section = int(round(target / section_count * 1.15))
    print(f"Sectioned generation: {section_count} sections x ~{words_per_section} words")

    outline = chat_json(llm, build_outline_messages(source_material, args, section_count, words_per_section), args)
    sections = outline.get("sections") or []
    if not isinstance(sections, list) or not sections:
        raise ValueError("Outline generation returned no sections.")
    sections = sections[: section_count + 2]

    parts = []
    previous_tail = ""
    for index, section in enumerate(sections, start=1):
        print(f"Writing section {index}/{len(sections)}")
        text = chat_text(
            llm,
            build_section_messages(source_material, section, index, len(sections), previous_tail, args, words_per_section),
            args,
            f"section {index}",
        )
        words = count_words(text)
        if words < int(words_per_section * 0.8):
            print(f"Section {index} is short ({words} words), expanding")
            try:
                expanded = chat_text(
                    llm,
                    build_length_fix_messages(text, words, argparse.Namespace(**{**vars(args), "target_word_count": words_per_section})),
                    args,
                    f"section {index} expand",
                )
                if count_words(expanded) > words:
                    text = expanded
            except Exception as exc:
                print(f"WARNING: section expand failed: {exc}", file=sys.stderr)
        parts.append(text.strip())
        previous_tail = " ".join(text.strip().split()[-40:])

    return "\n\n".join(parts)


def generate_script_with_length_control(source_material, llm, args):
    target = getattr(args, "target_word_count", 0)
    script = ""
    if target >= SECTIONED_THRESHOLD_WORDS:
        try:
            script = generate_sectioned_script(source_material, llm, args)
        except Exception as exc:
            print(f"WARNING: sectioned generation failed, falling back to single pass: {exc}", file=sys.stderr)
    if not script:
        print("Generating script through the LLM", flush=True)
        script = chat_text(llm, build_script_messages(source_material, args), args, "script")
    words = report_script_length(script, args, "draft")

    target = getattr(args, "target_word_count", 0)
    if target and abs(words - target) / target > 0.1:
        print("Word count is off target by more than 10%, running one corrective pass")
        try:
            fixed = chat_text(llm, build_length_fix_messages(script, words, args), args, "length fix")
            fixed_words = count_words(fixed)
            if abs(fixed_words - target) < abs(words - target):
                script = fixed
                report_script_length(script, args, "after fix")
            else:
                print("Corrective pass did not improve length, keeping the draft", file=sys.stderr)
        except Exception as exc:
            print(f"WARNING: length correction failed, keeping the draft: {exc}", file=sys.stderr)
    return script


def generate_content_package(source_material, llm, args):
    script = generate_script_with_length_control(source_material, llm, args)

    if getattr(args, "voice_only", False):
        print("Voice-only mode: skipping storyboard and image prompt generation")
        return normalize_content({"script": script, "title": "Voice-only script"}, args)

    print("Generating storyboard and image prompts through the LLM", flush=True)
    try:
        visual_data = chat_json(llm, build_visual_messages(script, args), args)
    except Exception as exc:
        print(f"WARNING: visual JSON generation failed, using fallback prompts: {exc}", file=sys.stderr)
        visual_data = fallback_visual_package(script, args)

    visual_data["script"] = script
    return normalize_content(visual_data, args)


def build_content_messages(source_material, args):
    visual_count_hint = max(3, int(args.visual_count or 12))
    system = (
        "You are a senior YouTube content producer, scriptwriter, and story editor. "
        "Create original content from source subtitles by rewriting and restructuring, not by translating line by line. "
        "Return only valid JSON."
    )
    user = {
        "task": "create_youtube_content_package_from_subtitles",
        "source_language": args.source_language,
        "target_language": args.target_language,
        "mode": args.mode,
        "rewrite_depth": args.rewrite_depth,
        "target_duration_minutes": args.target_duration_minutes,
        "narration_style": args.style,
        "image_aspect_ratio": args.aspect_ratio,
        "image_style": args.image_style,
        "visual_count_hint": visual_count_hint,
        "rules": [
            "Return exactly one JSON object.",
            "Keys: title, hook, description, script, storyboard, image_prompts, publish.",
            "Write script in target_language.",
            "Use the source for facts and topic context, but write a new original script with a new structure.",
            "Do not mention that the source was subtitles.",
            "Storyboard should group the script into meaningful visual scenes, not one image per sentence.",
            "Each storyboard item: scene, narration, visual, image_prompt, duration_hint_sec.",
            "Each image_prompts item: id, scene, prompt, negative_prompt, aspect_ratio.",
            "Image prompts should be in English and specific enough for image generation.",
            "Publish should include youtube_title, description, tags.",
        ],
        "rewrite_rules": rewrite_depth_rules(args.rewrite_depth),
        "source_material": source_material,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def normalize_content(data, args):
    data.setdefault("title", "Untitled")
    data.setdefault("hook", "")
    data.setdefault("description", "")
    data.setdefault("script", "")
    data.setdefault("storyboard", [])
    data.setdefault("image_prompts", [])
    data.setdefault("publish", {})
    if not isinstance(data["storyboard"], list):
        data["storyboard"] = []
    if not isinstance(data["image_prompts"], list):
        data["image_prompts"] = []
    for index, item in enumerate(data["storyboard"], start=1):
        if isinstance(item, dict):
            item.setdefault("scene", index)
            item.setdefault("duration_hint_sec", 0)
            item.setdefault("narration", "")
            item.setdefault("visual", "")
            item.setdefault("image_prompt", "")
    for index, item in enumerate(data["image_prompts"], start=1):
        if isinstance(item, dict):
            item.setdefault("id", f"image_{index:03d}")
            item.setdefault("scene", index)
            item.setdefault("prompt", "")
            item.setdefault("negative_prompt", "low quality, blurry, unreadable text, watermark")
            item.setdefault("aspect_ratio", args.aspect_ratio)
    return data


def write_outputs(content, summaries, segments, args):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = slugify(args.output_prefix) if args.output_prefix else default_output_prefix(args.subtitles)

    result = {
        "source_subtitles": str(Path(args.subtitles).resolve()),
        "source_language": args.source_language,
        "target_language": args.target_language,
        "mode": args.mode,
        "rewrite_depth": args.rewrite_depth,
        "target_duration_minutes": args.target_duration_minutes,
        "style": args.style,
        "aspect_ratio": args.aspect_ratio,
        "image_style": args.image_style,
        "subtitle_segments": len(segments),
        "chunk_summaries": summaries,
        "content": content,
    }

    json_path = output_dir / f"{prefix}.content.json"
    script_path = output_dir / f"{prefix}.content.script.txt"
    storyboard_path = output_dir / f"{prefix}.content.storyboard.txt"
    prompts_path = output_dir / f"{prefix}.content.prompts.txt"
    prompts_jsonl_path = output_dir / f"{prefix}.content.prompts.jsonl"

    result["script_word_count"] = count_words(content.get("script"))
    result["estimated_minutes"] = round(result["script_word_count"] / words_per_minute(args.target_language), 1)

    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    script_path.write_text(str(content.get("script") or "").strip() + "\n", encoding="utf-8")

    if getattr(args, "voice_only", False):
        return {
            "json": str(json_path.resolve()),
            "script": str(script_path.resolve()),
        }

    storyboard_lines = []
    for item in content.get("storyboard") or []:
        if not isinstance(item, dict):
            continue
        storyboard_lines.append(f"Scene {item.get('scene')}: {item.get('duration_hint_sec', 0)}s")
        storyboard_lines.append(f"Narration: {item.get('narration', '')}")
        storyboard_lines.append(f"Visual: {item.get('visual', '')}")
        storyboard_lines.append(f"Prompt: {item.get('image_prompt', '')}")
        storyboard_lines.append("")
    storyboard_path.write_text("\n".join(storyboard_lines).strip() + "\n", encoding="utf-8")

    prompt_lines = []
    with prompts_jsonl_path.open("w", encoding="utf-8") as f:
        for item in content.get("image_prompts") or []:
            if not isinstance(item, dict):
                continue
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            prompt_lines.append(f"{item.get('id')} ({item.get('aspect_ratio', args.aspect_ratio)})")
            prompt_lines.append(str(item.get("prompt") or "").strip())
            negative = str(item.get("negative_prompt") or "").strip()
            if negative:
                prompt_lines.append(f"Negative: {negative}")
            prompt_lines.append("")
    prompts_path.write_text("\n".join(prompt_lines).strip() + "\n", encoding="utf-8")

    return {
        "json": str(json_path.resolve()),
        "script": str(script_path.resolve()),
        "storyboard": str(storyboard_path.resolve()),
        "prompts": str(prompts_path.resolve()),
        "prompts_jsonl": str(prompts_jsonl_path.resolve()),
    }


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="Generate a YouTube content package from downloaded subtitles.")
    parser.add_argument("--subtitles", required=True)
    parser.add_argument("--source-language", default="auto")
    parser.add_argument("--voice-only", action="store_true")
    parser.add_argument("--target-language", default="Russian")
    parser.add_argument("--mode", default="YouTube 8-15 minutes")
    parser.add_argument("--rewrite-depth", choices=["balanced", "strong", "new_script"], default="strong")
    parser.add_argument("--target-duration-minutes", type=float, default=10.0)
    parser.add_argument("--style", default="Documentary YouTube narration, clear and engaging.")
    parser.add_argument("--image-style", default="Cinematic realistic documentary frame, high detail.")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--visual-count", type=int, default=16)
    add_llm_arguments(parser)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--temperature", type=float, default=0.45)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--direct-chars", type=int, default=22000)
    parser.add_argument("--chunk-chars", type=int, default=12000)
    args = parser.parse_args()

    subtitles = Path(args.subtitles)
    if not subtitles.is_file():
        raise RuntimeError(f"Subtitles file was not found: {subtitles}")

    print(f"Reading subtitles: {subtitles}")
    segments = parse_subtitles(subtitles)
    if not segments:
        raise RuntimeError("No subtitle text was found.")
    print(f"subtitle_segments={len(segments)}")

    if not args.source_language or args.source_language.strip().lower() in ("auto", "detect", ""):
        args.source_language = detect_language(" ".join(item["text"] for item in segments))
        print(f"Detected source language: {args.source_language}")

    args.target_word_count = max(50, int(round(args.target_duration_minutes * words_per_minute(args.target_language))))
    print(
        f"Target: {args.target_duration_minutes:g} min x {words_per_minute(args.target_language)} wpm "
        f"({args.target_language}) = {args.target_word_count} words"
    )

    llm = client_from_args(args)
    try:
        llm.prepare()
        transcript = transcript_text(segments)
        source_material, summaries = build_source_material(transcript, llm, args)
        content = generate_content_package(source_material, llm, args)
    finally:
        llm.finish()
    outputs = write_outputs(content, summaries, segments, args)

    for key, value in outputs.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
