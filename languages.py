"""Language names (used in LLM prompts) and ISO codes (used by Whisper, TTS and file names)."""

LANGUAGE_NAMES_BY_ID = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "es": "Spanish",
    "pt": "Portuguese",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "pl": "Polish",
    "bg": "Bulgarian",
    "tr": "Turkish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "he": "Hebrew",
    "ar": "Arabic",
    "hi": "Hindi",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ms": "Malay",
    "sw": "Swahili",
}
LANGUAGE_IDS = {name.lower(): code for code, name in LANGUAGE_NAMES_BY_ID.items()}


def language_id_for(language):
    """'English' -> 'en'; an ISO code is returned as is; unknown -> ''."""
    value = str(language or "").strip()
    if value.lower() in LANGUAGE_IDS:
        return LANGUAGE_IDS[value.lower()]
    if value.lower() in LANGUAGE_NAMES_BY_ID:
        return value.lower()
    return ""


def language_name_for(code_or_name):
    """'en' -> 'English'; a name or unknown code is returned as is."""
    value = str(code_or_name or "").strip()
    return LANGUAGE_NAMES_BY_ID.get(value.lower(), value)
