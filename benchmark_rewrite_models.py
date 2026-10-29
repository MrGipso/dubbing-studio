"""Benchmark LLMs (any provider) for subtitle-to-script rewriting.

Runs the same subtitles through several models and compares:
  - adherence to the target word count / duration
  - target language correctness
  - rewrite novelty (how far the text moved away from the source)
  - internal repetition
  - generation speed

Usage examples:
  python benchmark_rewrite_models.py --subtitles inputs/youtube/video.srt --list-models
  python benchmark_rewrite_models.py --subtitles inputs/youtube/video.srt ^
      --models "gemma-4-26b-a4b-it-qat,qwen3-32b,mistral-small-3.2" ^
      --target-language Russian --target-duration-minutes 10
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from llm_client import add_llm_arguments, client_from_args
from generate_content_from_subtitles import (
    build_source_material,
    configure_stdio,
    count_words,
    detect_language,
    generate_script_with_length_control,
    parse_subtitles,
    slugify,
    transcript_text,
    words_per_minute,
)


def ngrams(text, n):
    words = re.findall(r"[^\W\d_]+", str(text or "").lower(), flags=re.UNICODE)
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def source_overlap(script, source, n=4):
    """Share of script n-grams that also appear in the source. Lower = deeper rewrite."""
    script_grams = ngrams(script, n)
    if not script_grams:
        return 0.0
    return len(script_grams & ngrams(source, n)) / len(script_grams)


def repetition_ratio(script, n=5):
    """Share of repeated n-grams inside the script itself. Lower = cleaner text."""
    words = re.findall(r"[^\W\d_]+", str(script or "").lower(), flags=re.UNICODE)
    total = max(0, len(words) - n + 1)
    if total == 0:
        return 0.0
    unique = len({tuple(words[i : i + n]) for i in range(total)})
    return 1.0 - unique / total


def composite_score(metrics):
    """0-100, higher is better. Purely mechanical - always review the scripts yourself."""
    score = 100.0
    score -= 100.0 * min(1.0, metrics["word_deviation"])          # off-target length
    score -= 40.0 * metrics["source_overlap"]                     # too close to the source
    score -= 60.0 * min(1.0, metrics["repetition"] * 4)           # repetitive text
    if not metrics["language_ok"]:
        score -= 50.0                                             # wrong language
    return max(0.0, round(score, 1))


def run_model(model_name, source_material, source_transcript, args):
    run_args = argparse.Namespace(**vars(args))
    run_args.unload_other_models = True
    run_args.unload_model_when_done = args.unload_after_each

    print(f"\n=== Model: {model_name} ===")
    llm = client_from_args(run_args, model=model_name)

    started = time.time()
    error = ""
    script = ""
    try:
        llm.prepare()
        script = generate_script_with_length_control(source_material, llm, run_args)
    except Exception as exc:  # keep benchmarking the remaining models
        error = str(exc)
        print(f"ERROR: {model_name}: {exc}", file=sys.stderr)
    finally:
        llm.finish()
    elapsed = time.time() - started

    words = count_words(script)
    target = args.target_word_count
    detected = detect_language(script) if script else ""
    metrics = {
        "model": model_name,
        "error": error,
        "seconds": round(elapsed, 1),
        "words": words,
        "target_words": target,
        "word_deviation": round(abs(words - target) / target, 3) if target else 0.0,
        "estimated_minutes": round(words / words_per_minute(args.target_language), 1),
        "detected_language": detected,
        "language_ok": detected.lower() == args.target_language.lower(),
        "source_overlap": round(source_overlap(script, source_transcript), 3),
        "repetition": round(repetition_ratio(script), 3),
    }
    metrics["score"] = composite_score(metrics) if script else 0.0
    return metrics, script


def write_report(results, args, output_dir):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"benchmark_{stamp}.md"
    ranked = sorted(results, key=lambda item: item["score"], reverse=True)

    lines = [
        "# Rewrite model benchmark",
        "",
        f"Subtitles: `{args.subtitles}`",
        f"Target: {args.target_language}, {args.target_duration_minutes:g} min "
        f"(~{args.target_word_count} words), mode `{args.mode}`, depth `{args.rewrite_depth}`",
        "",
        "| # | Model | Score | Words (target) | ~Min | Lang | Source overlap | Repetition | Time s | Error |",
        "|---|-------|-------|----------------|------|------|----------------|------------|--------|-------|",
    ]
    for index, item in enumerate(ranked, start=1):
        lines.append(
            f"| {index} | {item['model']} | {item['score']} "
            f"| {item['words']} ({item['target_words']}) | {item['estimated_minutes']} "
            f"| {'OK' if item['language_ok'] else item['detected_language'] or '-'} "
            f"| {item['source_overlap']} | {item['repetition']} | {item['seconds']} "
            f"| {item['error'][:80]} |"
        )
    lines += [
        "",
        "How to read this:",
        "",
        "- Score is mechanical. It checks length, language, novelty, and repetition,",
        "  not style or storytelling. Read the top 2-3 scripts side by side before deciding.",
        "- Source overlap: below ~0.15 means a real rewrite; above ~0.35 means the model",
        "  mostly translated or copied the source.",
        "- Repetition above ~0.05 usually means the model loops or pads with filler.",
        "",
        "Scripts:",
        "",
    ]
    for item in ranked:
        lines.append(f"- {item['model']}: `{item.get('script_file', '-')}`")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subtitles", required=True)
    parser.add_argument("--models", default="", help="Comma-separated model ids. Empty = ask.")
    parser.add_argument("--list-models", action="store_true", help="List available LLMs and exit.")
    parser.add_argument("--target-language", default="Russian")
    parser.add_argument("--target-duration-minutes", type=float, default=10.0)
    parser.add_argument("--mode", default="YouTube 8-15 minutes")
    parser.add_argument("--rewrite-depth", choices=["balanced", "strong", "new_script"], default="strong")
    parser.add_argument("--style", default="Documentary YouTube narration, clear and engaging.")
    add_llm_arguments(parser)
    parser.add_argument("--temperature", type=float, default=0.45)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--direct-chars", type=int, default=22000)
    parser.add_argument("--chunk-chars", type=int, default=12000)
    parser.add_argument("--output-dir", default="outputs/benchmark")
    parser.add_argument("--unload-after-each", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    args.source_language = "auto"
    args.voice_only = True

    if args.list_models:
        llm = client_from_args(args)
        print(f"Available models ({llm.label}):")
        for model in llm.list_models():
            print(f"  {model['id']}  {model['details']}")
        return

    model_names = [name.strip() for name in args.models.split(",") if name.strip()]
    if not model_names:
        raise SystemExit("Pass --models model1,model2,... (see --list-models).")

    subtitles = Path(args.subtitles)
    if not subtitles.is_file():
        raise SystemExit(f"Subtitles file was not found: {subtitles}")

    segments = parse_subtitles(subtitles)
    if not segments:
        raise SystemExit("No subtitle text was found.")
    source_transcript = transcript_text(segments)

    args.source_language = detect_language(" ".join(item["text"] for item in segments))
    print(f"Detected source language: {args.source_language}")
    args.target_word_count = max(50, int(round(args.target_duration_minutes * words_per_minute(args.target_language))))
    print(f"Target: {args.target_duration_minutes:g} min = ~{args.target_word_count} words in {args.target_language}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Source material (chunk summaries) is built once with the first model to keep
    # the comparison fair: every model rewrites the same input.
    first_args = argparse.Namespace(**vars(args))
    first_args.unload_other_models = True
    first_llm = client_from_args(first_args, model=model_names[0])
    first_llm.prepare()
    source_material, _ = build_source_material(source_transcript, first_llm, args)

    results = []
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for model_name in model_names:
        metrics, script = run_model(model_name, source_material, source_transcript, args)
        if script:
            script_file = output_dir / f"bench_{stamp}_{slugify(model_name)[:50]}.script.txt"
            script_file.write_text(script.strip() + "\n", encoding="utf-8")
            metrics["script_file"] = str(script_file)
        results.append(metrics)
        print(json.dumps(metrics, ensure_ascii=False))

    report_path = write_report(results, args, output_dir)
    print(f"\nReport: {report_path.resolve()}")
    best = max(results, key=lambda item: item["score"])
    print(f"Best mechanical score: {best['model']} ({best['score']})")
    print("Read the top scripts before choosing - the score does not judge storytelling.")


if __name__ == "__main__":
    main()
