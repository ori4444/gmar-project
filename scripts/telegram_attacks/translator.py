import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import ENABLE_TRANSLATION, TRANSLATION_TARGET

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None


def translate_to_hebrew(text: str) -> str:
    if not ENABLE_TRANSLATION:
        return ""

    if not text.strip():
        return ""

    if GoogleTranslator is None:
        return "[Translation unavailable: install deep-translator]"

    try:
        return GoogleTranslator(source="auto", target=TRANSLATION_TARGET).translate(text)
    except Exception as exc:
        return f"[Translation failed: {exc}]"
