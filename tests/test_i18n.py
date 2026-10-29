"""Interface translations: every Russian UI string has an entry in locales/*.json.

Run `python tests/test_i18n.py --missing en` to list strings that still need
a translation (as JSON, ready to paste into locales/en.json).
"""

import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import i18n  # noqa: E402

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
JS_FILES = ("core.js", "settings.js", "tools.js", "projects.js", "timeline.js")
# Python files with user-visible messages (prepare_script.py has Russian keywords as data).
PY_FILES = ("project.py", "tts_engines.py", "pipeline_ui.py", "dub.py", "transcribe.py", "translate_srt.py", "tools_setup.py",
            "engines/demucs/adapter.py")
MANIFEST_KEYS = ("name", "description", "license", "install_note", "languages")
PLACEHOLDER = re.compile(r"\{(\w+)(?::[^}]*)?\}")


def normalized(text):
    return " ".join(str(text).split())


def chunks(text):
    """Russian pieces of code between quotes, tags and template braces."""
    for piece in re.split(r"[\"'`<>{}$\n]", text):
        piece = re.sub(r"^/\d+\s", "", normalized(piece))  # f"PROGRESS {done}/100 label"
        if CYRILLIC.search(piece):
            yield piece


def unescape(literal):
    return literal.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")


class HtmlStrings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = set()
        self.stack = []

    def skipping(self):
        return any(skip for _, skip in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        skip = self.skipping() or tag in ("script", "style") or attrs.get("translate") == "no"
        if tag not in ("input", "br", "meta", "link", "img", "hr", "source"):
            # A textarea's text is user content, its placeholder is interface.
            self.stack.append((tag, skip or tag == "textarea"))
        if skip:
            return
        for key in ("placeholder", "title", "aria-label"):
            if attrs.get(key) and CYRILLIC.search(attrs[key]):
                self.found.add(normalized(attrs[key]))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if not self.skipping() and CYRILLIC.search(data):
            self.found.add(normalized(data))


def ui_strings():
    """Every Russian source string the interface can show."""
    found = set()

    parser = HtmlStrings()
    parser.feed((ROOT / "ui" / "index.html").read_text(encoding="utf-8"))
    found |= parser.found

    call = re.compile(r"\bt\(\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
    for name in JS_FILES:
        source = "\n".join(line for line in (ROOT / "ui" / name).read_text(encoding="utf-8").splitlines()
                           if not line.strip().startswith("//"))
        for match in call.finditer(source):
            found.add(unescape(match.group(1)[1:-1]))
        found |= set(chunks(call.sub("t()", source)))

    py_call = re.compile(r"\btr\(\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
    for name in PY_FILES:
        lines = [line for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
                 if not line.strip().startswith("#") and "i18n-ignore" not in line]
        source = "\n".join(lines)
        for match in py_call.finditer(source):
            found.add(unescape(match.group(1)[1:-1]))
        found |= set(chunks(py_call.sub("tr()", source)))

    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in (ROOT / "engines").glob("*/engine.json")]
    for manifest in manifests:
        items = [manifest] + list(manifest.get("settings") or [])
        for item in items:
            for key in MANIFEST_KEYS + ("label", "help"):
                if isinstance(item.get(key), str) and CYRILLIC.search(item[key]):
                    found.add(item[key])
    providers = json.loads((ROOT / "config" / "llm_providers.json").read_text(encoding="utf-8"))["providers"]
    for provider in providers:
        for key in ("label", "hint"):
            if CYRILLIC.search(str(provider.get(key) or "")):
                found.add(provider[key])
    return found


def missing(code):
    catalog = i18n.catalog(code)
    return sorted(text for text in ui_strings() if text not in catalog)


class TranslationTests(unittest.TestCase):
    def test_every_ui_string_is_translated(self):
        for code in i18n.available_languages():
            if code == i18n.SOURCE_LANGUAGE:
                continue
            with self.subTest(language=code):
                self.assertEqual(missing(code), [], f"run: python tests/test_i18n.py --missing {code}")
                unused = sorted(set(i18n.catalog(code)) - ui_strings())
                self.assertEqual(unused, [], "strings no longer used by the interface")

    def test_translations_keep_placeholders(self):
        for code in i18n.available_languages():
            for source, translated in i18n.catalog(code).items():
                with self.subTest(language=code, text=source):
                    self.assertEqual(set(PLACEHOLDER.findall(source)), set(PLACEHOLDER.findall(translated)))
                    self.assertFalse(CYRILLIC.search(translated) and code == "en", translated)

    def test_tr_and_language_choice(self):
        saved = i18n._current
        try:
            i18n.set_language("en-US")
            self.assertEqual(i18n.language(), "en")
            self.assertEqual(i18n.tr("Сначала озвучьте перевод на {target} (шаг «Голос»).", target="German"),
                             "Voice the German translation first (step “Voice”).")
            self.assertEqual(i18n.tr("Текст, которого нет в словаре"), "Текст, которого нет в словаре")
            i18n.set_language("xx")
            self.assertEqual(i18n.language(), "ru")
            self.assertEqual(i18n.tr("Готово."), "Готово.")
        finally:
            i18n._current = saved
        self.assertEqual(i18n.from_accept_language("ru-RU,ru;q=0.9,en;q=0.8"), "ru")
        self.assertEqual(i18n.from_accept_language("uk-UA,uk;q=0.9"), "ru")
        self.assertEqual(i18n.from_accept_language("de-DE,de;q=0.9,en;q=0.5"), "en")
        self.assertEqual(i18n.from_accept_language(""), "en")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--missing":
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps({text: "" for text in missing(sys.argv[2])}, ensure_ascii=False, indent=2))
    else:
        unittest.main()
