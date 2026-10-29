import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from llm_client import LLMError, add_llm_arguments, client_from_args, configure_stdio


LANGUAGE_CODES = {
    "English": "en",
    "Spanish": "es",
    "Portuguese": "pt",
    "Bulgarian": "bg",
}

TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
    "additionalProperties": False,
}


def slugify(value):
    value = LANGUAGE_CODES.get(value, value)
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower())
    return value.strip("-") or "translated"


def default_output_prefix(text):
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"text_{time.strftime('%Y%m%d_%H%M%S')}_{digest}"


def extract_translation(content):
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict) and str(data.get("translation") or "").strip():
            return str(data["translation"]).strip()
    except json.JSONDecodeError:
        pass

    return content.strip()


def build_messages(text, source_language, target_language, style, ref_text):
    system = (
        "You are a professional translator for voiceover and dubbing. "
        "Translate naturally, preserve meaning, names, numbers, paragraph breaks, and tone. "
        "Return only valid JSON."
    )
    user = {
        "task": "translate_plain_text_for_voiceover",
        "source_language": source_language,
        "target_language": target_language,
        "style": style,
        "rules": [
            "Return exactly one JSON object.",
            "The object must be {\"translation\": string}.",
            "Do not add explanations, markdown, comments, or extra keys.",
            "Keep the translation natural for spoken narration.",
            "If voice_reference_text is provided, use it only to infer tone and speaking style; do not translate it as content.",
        ],
        "text": text,
    }
    if ref_text:
        user["voice_reference_text"] = ref_text
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def translate_one(llm, text, target_language, args):
    messages = build_messages(
        text=text,
        source_language=args.source_language,
        target_language=target_language,
        style=args.style,
        ref_text=args.ref_text,
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
            translation = extract_translation(content)
            if not translation:
                raise ValueError(f"Empty translation for {target_language}.")
            return translation
        except (LLMError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < args.retries:
                wait = min(2 * attempt, 8)
                print(f"retry {attempt}/{args.retries}: {exc}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Translation failed for {target_language} ({llm.describe()}): {last_error}")


def parse_targets(raw):
    targets = []
    for item in raw:
        for part in str(item).split(","):
            name = part.strip()
            if name:
                targets.append(name)
    seen = set()
    result = []
    for target in targets:
        key = target.lower()
        if key not in seen:
            seen.add(key)
            result.append(target)
    return result


def translate_all(llm, model, text, text_path, targets, output_dir, output_prefix, args):
    result = {
        "source_language": args.source_language,
        "source_text_file": str(text_path.resolve()),
        "source_text": text,
        "ref_audio": args.ref_audio,
        "ref_text": args.ref_text,
        "llm_provider": args.llm_provider,
        "llm_url": args.llm_url,
        "model": model,
        "style": args.style,
        "translations": [],
    }

    for target in targets:
        print(f"Translating text to {target}", flush=True)
        translation = translate_one(llm, text, target, args)
        code = LANGUAGE_CODES.get(target, slugify(target))
        txt_out = output_dir / f"{output_prefix}.{code}.txt"
        txt_out.write_text(translation.strip() + "\n", encoding="utf-8")
        result["translations"].append(
            {
                "language": target,
                "language_id": code,
                "text_file": str(txt_out.resolve()),
                "translation": translation,
            }
        )
        print(f"txt={txt_out.resolve()}")

    json_out = output_dir / f"{output_prefix}.translations.json"
    json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return json_out


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="Translate plain text to multiple languages through any LLM provider.")
    parser.add_argument("--text-file", required=True, help="UTF-8 text file to translate.")
    parser.add_argument("--source-language", default="Russian")
    parser.add_argument("--target-language", action="append", default=[])
    add_llm_arguments(parser)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--style", default="Natural spoken voiceover translation.")
    parser.add_argument("--ref-audio", default="")
    parser.add_argument("--ref-text", default="")
    args = parser.parse_args()

    text_path = Path(args.text_file)
    text = text_path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise RuntimeError("Input text is empty.")

    targets = parse_targets(args.target_language) or ["English", "Spanish", "Portuguese", "Bulgarian"]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = args.output_prefix.strip() or default_output_prefix(text)

    llm = client_from_args(args)
    try:
        model = llm.prepare()
        json_out = translate_all(llm, model, text, text_path, targets, output_dir, output_prefix, args)
    finally:
        llm.finish()
    print(f"json={json_out.resolve()}")


if __name__ == "__main__":
    main()
