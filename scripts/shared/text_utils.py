import re


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = text.replace("​", " ")
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"__+", "", text)
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def lower_text(text: str) -> str:
    return clean_text(text).lower()


def snippet(text: str, n: int = 4000) -> str:
    t = clean_text(text)
    return t if len(t) <= n else t[:n] + "..."


def safe_bool_str(value):
    if value is None:
        return "None"
    return "True" if value else "False"


def split_sentences(text: str) -> list[str]:
    t = clean_text(text)
    if not t:
        return []
    parts = re.split(r"(?<=[\.!?])\s+|\s+—\s+|\s+[-–]\s+", t)
    return [p.strip() for p in parts if p.strip()]
