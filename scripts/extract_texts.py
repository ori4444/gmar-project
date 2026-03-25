import os
import re

INPUT_DIR = "data/raw_reports"


def clean_text(text):
    text = text.replace("\r\n", "\n")

    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)

    lines = text.split("\n")
    cleaned_lines = []

    junk_patterns = [
        r"^\s*download the full assessment\s*$",
        r"^\s*previous\s*$",
        r"^\s*next\s*$",
        r"^\s*related research\s*$",
        r"^\s*back to top\s*$",
        r"^\s*share\s*$",
        r"^\s*subscribe\s*$",
        r"^\s*institute for the study of war\s*$",
        r"^\s*isw\s*$",
        r"^\s*endnotes\s*$",
        r"^\s*interactive maps\s*$",
        r"^\s*reference materials\s*$",
        r"^\s*read more on this product line\s*$",
        r"^\s*all rights reserved\.?\s*$",
        r"^\s*privacy policy\s*$",
        r"^\s*facebook\s*$",
        r"^\s*instagram\s*$",
        r"^\s*youtube\s*$",
        r"^\s*linkedin\s*$",
        r"^\s*x-twitter\s*$",
    ]

    for line in lines:
        stripped = line.strip()
        low = stripped.lower()

        if not stripped:
            cleaned_lines.append("")
            continue

        if re.search(r"!\[.*?\]\(.*?\)", stripped):
            continue

        if re.fullmatch(r"(https?://\S+|www\.\S+)", stripped):
            continue

        if any(re.match(p, low) for p in junk_patterns):
            continue

        cleaned_lines.append(stripped)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sentences(text):
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def cond_drones(sentence):
    t = sentence.lower()
    return any(p in t for p in ["drone", "drones", "uav", "uavs", "shahed"])


def cond_energy(sentence):
    t = sentence.lower()
    patterns = [
        "oil", "refinery", "refineries",
        "oil depot", "fuel depot", "fuel",
        "pipeline", "oil pipeline", "druzhba",
        "terminal", "petroleum", "oil products",
        "oil pumping station", "pumping station",
        "switchgear", "boiler equipment warehouse",
        "power facility", "energy facility",
        "gas", "gas facility", "gas infrastructure",
        "plant", "metallurgical plant",
        "water aeration station",
    ]
    return any(p in t for p in patterns)


def cond_russian_air_defense(sentence):
    t = sentence.lower()
    patterns = [
        "air defense",
        "air defenses",
        "air defence",
        "air defences",
        "russian air defense",
        "russian air defenses",
        "russian air defence",
        "russian air defences",
        "downed",
        "intercepted",
        "debris",
        "wreckage",
    ]
    return any(p in t for p in patterns)


def cond_attack(sentence):
    t = sentence.lower()
    patterns = [
        "strike",
        "struck",
        "attack",
        "attacked",
        "targeted",
        "hit",
        "launched",
        "destroyed",
        "damaged",
        "fire",
        "explosion",
        "explosions",
        "conducted a strike",
        "conducted a drone strike",
    ]
    return any(p in t for p in patterns)


def cond_starts_ukrainian_forces(sentence):
    s = sentence.strip().lower()

    return (
        s.startswith("ukrainian forces")
        or s.startswith("ukraine’s security service")
        or s.startswith("ukraine's security service")
        or s.startswith("sbu")
        or s.startswith("the sbu")
        or s.startswith("gur")
        or s.startswith("the gur")
    )


def is_suspected_russian_attack(sentence):
    t = sentence.lower()

    patterns = [
        "russian forces launched",
        "russian forces struck",
        "russian forces attacked",
        "russian strike",
        "russian strikes",
        "russian attack",
        "russian attacks",
        "russian missile",
        "russian missiles",
        "russian drones struck",
        "russian drones attacked",
        "russian drones targeted",
        "against ukraine",
        "against ukrainian positions",
        "against ukrainian infrastructure",
        "against ukraine from",
        "damaged infrastructure in kyiv",
        "damaged infrastructure in kharkiv",
        "damaged infrastructure in chernihiv",
        "damaged infrastructure in cherkasy",
        "damaged infrastructure in sumy",
        "damaged infrastructure in odesa",
        "damaged infrastructure in mykolaiv",
        "damaged infrastructure in dnipropetrovsk",
        "damaged infrastructure in kherson",
    ]

    return any(p in t for p in patterns)


def is_fast_russian_defense_entry(sentence):
    t = sentence.lower()

    patterns = [
        ("russian forces", "downed"),
        ("russian forces", "intercepted"),
        ("russian air defense", "downed"),
        ("russian air defense", "intercepted"),
        ("russian air defenses", "downed"),
        ("russian air defenses", "intercepted"),
        ("russian air defence", "downed"),
        ("russian air defence", "intercepted"),
        ("russian air defences", "downed"),
        ("russian air defences", "intercepted"),
    ]

    for left, right in patterns:
        if left in t and right in t:
            return True

    return False


def evaluate_sentence(sentence):
    matched = []

    if cond_drones(sentence):
        matched.append("drones")

    if cond_energy(sentence):
        matched.append("energy")

    if cond_russian_air_defense(sentence):
        matched.append("russian_air_defense")

    if cond_attack(sentence):
        matched.append("attack")

    if cond_starts_ukrainian_forces(sentence):
        matched.append("starts_with_ukrainian_actor")

    if is_fast_russian_defense_entry(sentence):
        matched.append("fast_russian_defense_entry")

    return matched


def passes_threshold(matched_conditions):
    matched_set = set(matched_conditions)

    if "fast_russian_defense_entry" in matched_set:
        return True

    if "starts_with_ukrainian_actor" in matched_set:
        return len(matched_set) >= 2

    return len(matched_set) >= 4


def extract_relevant_sentences(text):
    text = clean_text(text)
    sentences = split_sentences(text)

    results = []

    for sentence in sentences:
        matched = evaluate_sentence(sentence)

        if not passes_threshold(matched):
            continue

        if is_suspected_russian_attack(sentence):
            continue

        results.append({
            "sentence": sentence,
            "matched_conditions": matched,
            "matched_count": len(set(matched)),
        })

    return results


def process_file(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    return extract_relevant_sentences(text)

def extract_from_text(text):
    return extract_relevant_sentences(text)

if __name__ == "__main__":
    files = sorted([f for f in os.listdir(INPUT_DIR) if f.endswith(".txt")])

    print("\n==== SUMMARY ====\n")

    total = 0

    for f in files:
        path = os.path.join(INPUT_DIR, f)
        results = process_file(path)

        print(f"\n===== {f} =====")
        print(f"Relevant sentences: {len(results)}")

        for i, item in enumerate(results, 1):
            print(f"\n--- Sentence {i} ---")
            print(item["sentence"])
            print(f"Matched conditions ({item['matched_count']}): {', '.join(item['matched_conditions'])}")

        total += len(results)

    print(f"\n\nTOTAL RELEVANT SENTENCES: {total}")
