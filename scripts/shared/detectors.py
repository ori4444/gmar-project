import re
from datetime import datetime, timedelta
from typing import Optional

from .config import LOCAL_TZ, ONLY_RUSSIA
from .text_utils import lower_text, split_sentences


REGION_ALIASES = {
    "краснодарск": "Krasnodar",
    "туапсе": "Tuapse",
    "волгоград": "Volgograd",
    "уфа": "Ufa",
    "башкир": "Bashkortostan",
    "самар": "Samara",
    "сызран": "Syzran",
    "новокуйбышев": "Novokuibyshevsk",
    "рязан": "Ryazan",
    "смоленск": "Smolensk",
    "ярцев": "Yartsevo",
    "саратов": "Saratov",
    "энгельс": "Engels",
    "брянск": "Bryansk",
    "тверск": "Tver",
    "удомл": "Udomlya",
    "курск": "Kursk",
    "астрахан": "Astrakhan",
    "ростов": "Rostov",
    "новошахтин": "Novoshakhtinsk",
    "чертков": "Chertkovo",
    "ленинградск": "Leningrad Oblast",
    "кириш": "Kirishi",
    "чуваш": "Chuvashia",
    "чебоксар": "Cheboksary",
    "серпухов": "Serpukhov",
    "московск": "Moscow Oblast",
    "тамбов": "Tambov",
    "петровск": "Petrovsk",
    "гай-кодзор": "Gay-Kodzor",
    "карачев": "Karachev",
    "воронеж": "Voronezh",
    "лиски": "Liski",
    "липецк": "Lipetsk",
    "узлов": "Uzlovaya",
    "новороссийск": "Novorossiysk",
    "орлов": "Oryol",
    "казан": "Kazan",
    "альметьевск": "Almetyevsk",
    "алексин": "Aleksin",
    "тула": "Tula",
    "набережн": "Naberezhnye Chelny",
    "нижнекамск": "Nizhnekamsk",
    "пермск": "Perm",
    "перм": "Perm",
    "ижевск": "Izhevsk",
    "оренбург": "Orenburg",
    "пенз": "Penza",
    "ульяновск": "Ulyanovsk",
    "нижн": "Nizhny Novgorod",
}

NON_RUSSIA_HINTS = [
    "киев",
    "полтав",
    "одесс",
    "чернигов",
    "сумах",
    "никополь",
    "донецк",
    "харьков",
    "херсон",
    "днепропетров",
    "энергодар",
]

FACILITY_TYPES = [
    (r"нефтебаз\w*", "oil_depot"),
    (r"нефтехранилищ\w*", "oil_depot"),
    (r"\bнпз\b", "refinery"),
    (r"нефтеперерабатыва\w*", "refinery"),
    (r"нефтекомплекс\w*", "refinery"),
    (r"нефтепровод\w*", "pipeline"),
    (r"газопровод\w*", "gas_facility"),
    (r"магистральн\w*\s+газопровод\w*", "gas_facility"),
    (r"газоперерабатыва\w*\s+завод\w*", "gas_facility"),
    (r"компрессорн\w*\s+станц\w*", "gas_facility"),
    (r"нефтеперекачива\w*\s+станц\w*", "pipeline"),
    (r"\bнпс\b", "pipeline"),
    (r"подстанц\w*", "power_facility"),
    (r"энергообъект\w*", "power_facility"),
    (r"\bлэп\b", "power_facility"),
    (r"высоковольтн\w*\s+лини\w*", "power_facility"),
    (r"электростанц\w*", "power_facility"),
    (r"\bтэц\b", "power_facility"),
    (r"\bаэс\b", "power_facility"),
    (r"резервуар\w*\s+с\s+бензин", "oil_depot"),
    (r"резервуар\w*\s+с\s+топлив", "oil_depot"),
    (r"резервуар\w*\s+с\s+нефт", "oil_depot"),
    # LPG / gas terminal vocabulary
    (r"баз\w*\s+сжиженн\w*\s+газ", "gas_facility"),
    (r"сжиженн\w*\s+(?:газ|углеводород)", "gas_facility"),
    (r"цистерн\w*\s+с\s+(?:топлив|газ|бензин|нефт)", "oil_depot"),
    # Transneft pipeline facilities
    (r"транснефт", "pipeline"),
]

CANONICAL_TARGETS = [
    ("Smolensk Oil Depot", "oil_depot", [
        r"смоленскнефтепродукт",
        r"роснефть[-\s]+смоленскнефтепродукт",
        r"нефтебаз\w*.*ярцев",
        r"ярцев.*нефтебаз",
        r"нефтебаз\w*.*смоленск",
        r"смоленск.*нефтебаз\w*",
    ]),
    ("Ryazan Oil Refinery", "refinery", [
        r"рязанск\w*\s+нефтеперерабатыва\w*\s+компан",
        r"\bрнпк\b",
        r"рязан.*нпз",
        r"нпз.*ряза",
    ]),
    ("Syzran Oil Refinery", "refinery", [
        r"сызран.*нпз",
        r"сызран.*нефтеперерабатыва",
    ]),
    ("Novokuibyshevsk Oil Refinery", "refinery", [
        r"новокуйбышевск.*нпз",
        r"новокуйбышевск.*нефтеперерабатыва",
    ]),
    ("Ufa Oil Refinery Complex", "refinery", [
        r"башнефть",
        r"уфанефтехим",
        r"новойл",
        r"унпз",
    ]),
    ("KINEF Oil Refinery", "refinery", [
        r"\bкинеф\b",
        r"киришинефтеоргсинтез",
    ]),
    ("Tuapse Oil Refinery", "refinery", [
        r"туапсинск\w*\s+нпз",
        r"нпз\s+в\s+туапсе",
        r"нефтекомплекс\w*\s+в\s+туапсе",
    ]),
    ("Russkaya Compressor Station", "gas_facility", [
        r"компрессорн\w*\s+станц\w*\s+[«\"]?русск\w*[»\"]?",
        r"турецк\w*\s+поток",
    ]),
    ("Kalinin Nuclear Power Plant", "power_facility", [
        r"калининск\w*\s+аэс",
    ]),
    ("Kursk Power Grid", "power_facility", [
        r"курская\s+аэс\s*[-–]\s*металлургическ",
        r"высоковольтн\w*\s+лини\w*.*курск",
    ]),
    ("Novoshakhtinsk Oil Pipeline", "pipeline", [
        r"нефтепровод\w*\s+куйбышев[-–]лисичанск",
        r"чертковск\w*.*нефтепровод",
    ]),
    ("PSP-915 Pumping Station", "pipeline", [
        r"\bпсп-?915\b",
    ]),
    ("Sokhranovka Gas Infrastructure", "gas_facility", [
        r"сохрановск\w*\s+линейно[- ]производствен",
        r"сохрановк\w*.*магистральн\w*\s+газопровод",
    ]),
    ("Cheboksary Oil Depot", "oil_depot", [
        r"комбинат\s+буревестник",
        r"реконструируем\w*\s+нефтебаз",
        r"чебоксар.*нефтебаз",
    ]),
    ("Oka-Center Oil Depot", "oil_depot", [
        r"нефтебаз\w*\s+[«\"]?ока[- ]центр[»\"]?",
        r"ока[- ]центр",
    ]),
    ("Davydovskaya Compressor Station", "gas_facility", [
        r"давыдовск\w*",
    ]),
    ("Novopetrovskaya Compressor Station", "gas_facility", [
        r"новопетровск\w*",
    ]),
    ("Aksinino Pumping Station", "pipeline", [
        r"аксинино",
        r"нпс\s+[«\"]?аксинино[»\"]?",
    ]),
    ("Volgograd Lukoil Refinery", "refinery", [
        r"лукойл[-–]волгограднефтепереработка",
        r"волгоградск\w*\s+нпз",
    ]),
    ("Yefimovskaya Pipeline Station", "pipeline", [
        r"ефимовск\w*",
        r"диспетчерск\w*\s+станц\w*\s+[«\"]?ефимовск",
    ]),
    ("Petrovsk Gas Infrastructure", "gas_facility", [
        r"петровск\w*\s+линейн\w*\s+производственн\w*\s+управлен",
        r"газов\w*\s+подстанц\w*.*петров",
    ]),
    ("Astrakhan Gas Processing Plant", "gas_facility", [
        r"астраханск\w*\s+газоперерабатыва\w*\s+завод",
        r"астраханск\w*\s+гпз",
    ]),
    ("Ust-Luga Oil Terminal", "oil_depot", [
        r"усть[- ]луга",
        r"нефтяной\s+терминал.*усть[- ]луга",
    ]),
    ("Gazprom LPG Terminal (Kazan)", "gas_facility", [
        r"газпром\s+сжиженн\w*\s+газ",
        r"казан\w*.*баз\w*\s+сжиженн",
        r"баз\w*\s+сжиженн\w*.*казан",
        r"цистерн\w*.*сжиженн\w*.*казан",
    ]),
    ("Aleksin Power Plant (ТЭЦ)", "power_facility", [
        r"алексинск\w*\s+тэц",
        r"тэц.*алексин",
    ]),
    ("Kristal Oil Depot (Engels)", "oil_depot", [
        r"комбинат\s+[«\"]?кристалл[»\"]?",
        r"нефтебаз\w*\s+комбинат\w*\s+кристалл",
        r"нефтебаз\w*.*\bэнгельс\w*\b",
        r"\bэнгельс\w*\b.*нефтебаз\w*",
        r"аэродром\w*\s+[«\"]?энгельс[-–]2[»\"]?.*нефтебаз",
        r"нефтебаз\w*.*аэродром\w*\s+[«\"]?энгельс[-–]2[»\"]?",
    ]),
]

CLEAR_SKIP_PATTERNS = [
    r"новая\s+почта",
    r"посыл",
    r"жил\w*\s+дом",
    r"квартир",
    r"частн\w*\s+дом",
    r"многоквартир",
    r"детск\w*\s+игруш",
    r"зернов\w*\s+терминал",
    r"авиазавод",
    r"завод\s+взрывчат",
    r"взрывчат\w*\s+веществ",
    # editorial / legal articles about past events — not live attack reports
    r"суд\s+(?:обязал|постановил|запретил)",
    r"по\s+иску\s+прокуратур",
    r"обязал\w*\s+яндекс",
    r"скрыть\s+с\s+карт",
]


SUMMARY_SKIP_PATTERNS = [
    r"за\s+прошедшие\s+сутки",
    r"за\s+прошедшую\s+ночь",
    r"за\s+день",
    r"за\s+ночь\b",
    r"итоги\s+дня",
    r"итоги\s+недели",
    r"суточная\s+сводк",
    r"сводка\s+за",
    r"ночной\s+обзор",
    r"ночная\s+сводк",
    r"шесть\s+раз\s+пыталась\s+атаковать",
    r"\bвсего\s+(?:сбито|уничтожено|перехвачено)\s+\d+",
    r"из\s+них\s+(?:сбито|уничтожено)\s+\d+",
]

DONATION_PATTERNS = [
    r"boosty",
    r"buymeacoffee",
    r"\bpatreon\b",
    r"\busdt\b",
    r"задонат\w*",
    r"поддержать\s+astra",
    r"поддержи\s+astra",
    r"сбор\s+на\s+зарплат",
    r"банковск\w*\s+реквизит",
    r"криптовалют\w*",
    r"пожертвован\w*",
]

SUBSCRIPTION_PATTERNS = [
    r"@redastrabot",
    r"подпишитесь\s+на\s+astra",
    r"написать\s+нам\s+в\s+бот",
    r"прислать\s+фото.{0,10}видео.{0,20}информ",
    r"прислать\s+фото.{0,10}видео",
    r"написать\s+боту",
    r"astra\s+в\s+telegram",
]


RUSSIA_ATTACKS_UKRAINE_PATTERNS = [
    r"россия\s+атаковала\s+украин",
    r"вс\s*рф\s+ударили",
    r"вс\s*рф\s+сбросили",
    r"российск\w*\s+атак\w*",
    r"удар\w*\s+по\s+украин",
]

CORRECTION_PATTERNS = [
    r"а\s+не",
    r"как\s+ранее\s+сообщалось",
    r"ранее\s+заявили",
    r"уточнил\w*",
    r"уточнила\w*",
    r"уточнили\w*",
]

UPDATE_ONLY_PATTERNS = [
    r"продолжает\s+гореть",
    r"горит\s+\d+\s+сут",
    r"ликвидирован\w*\s+открыт\w*\s+горен",
    r"локализован\w*",
    r"потушили",
    r"ликвидирован\w*\s+пожар",
    r"площадь\s+пожара",
    r"тушени\w*\s+продолжа",
    r"чс.*для\s+ликвидац",
]

PRIMARY_ATTACK_PATTERNS = [
    r"атак\w*",
    r"удар\w*\s+по",
    r"прилет\w*",
    r"попадани\w*",
    r"поразил\w*",
    r"пытал\w*\s+атаковать",
    r"нанес\w*\s+удар",
    r"беспилотник\w*\s+атак",
    r"дрон\w*\s+атак",
    r"бпла\s+атак",
]

ATTACK_CONTEXT_PATTERNS = [
    r"\bбпла\b",
    r"беспилотник\w*",
    r"дрон\w*",
    r"удар\w*",
    r"атак\w*",
    r"ракет\w*",
    r"нептун",
]

FIRE_PATTERNS = [
    r"пожар\w*",
    r"горит\w*",
    r"загорел\w*",
    r"возгорание",
    r"вспыхнул\w*",
    r"горени\w*",
]

EXPLOSION_PATTERNS = [
    r"взрыв\w*",
    r"детонировал\w*",
    r"детонац",
    r"раздал\w*.*взрыв",
    r"слышн\w*.*взрыв",
]

AIR_DEFENSE_PATTERNS = [
    r"\bпво\b",
    r"сбит\w*",
    r"уничтожен\w*",
    r"перехвачен\w*",
    r"подавлен\w*",
    r"\bрэб\b",
    r"отражен\w*\s+атак",
]

SHUTDOWN_PATTERNS = [
    r"приостановил\w*\s+работ",
    r"работа\s+.*приостанов",
    r"прекращени\w*\s+газоснабжени",
    r"без\s+электричеств",
    r"отключил\w*\s+высоковольтн",
    r"персонал\s+эвакуирован",
    r"эвакуировали\s+работник",
    r"работа\s+предприятия\s+остановлена",
]

DIRECT_HIT_PATTERNS = [
    r"прилет\w*\s+по",
    r"попал\w*\s+в",
    r"поврежден\w*",
    r"поражен\w*",
    r"начался\s+пожар",
    r"загорел\w*",
    r"возник\w*\s+пожар",
]

FOLLOWUP_ONLY_PATTERNS = [
    r"момент атаки",
    r"еще кадры",
    r"новые кадры",
    r"кадры с места",
    r"кадры очевидцев",
    r"прислать фото",
    r"прислать видео",
    r"прислать фото/видео/информацию",
]

RUS_NUM_WORDS = {
    "один": 1, "одна": 1, "одно": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20,
}


def _has_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def extract_area(text: str) -> Optional[str]:
    t = lower_text(text)
    for key, value in REGION_ALIASES.items():
        if key in t:
            return value
    return None


def is_russia_related(text: str) -> bool:
    t = lower_text(text)
    if not ONLY_RUSSIA:
        return True
    if any(h in t for h in NON_RUSSIA_HINTS):
        if not any(k in t for k in REGION_ALIASES.keys()):
            return False
    return True


def extract_local_target_type(text: str) -> Optional[str]:
    t = lower_text(text)
    for pattern, target_type in FACILITY_TYPES:
        if re.search(pattern, t):
            return target_type
    return None


def extract_all_targets(text: str) -> list[tuple[str, str]]:
    t = lower_text(text)
    found: list[tuple[str, str]] = []

    for canonical, target_type, patterns in CANONICAL_TARGETS:
        if any(re.search(p, t) for p in patterns):
            found.append((canonical, target_type))

    if found:
        seen = set()
        out = []
        for item in found:
            if item[0] not in seen:
                out.append(item)
                seen.add(item[0])
        return out

    target_type = extract_local_target_type(t)
    if not target_type:
        # No energy facility keyword found — cannot confirm this is an energy attack.
        return []

    pretty_type = {
        "oil_depot": "Oil Depot",
        "refinery": "Oil Refinery",
        "pipeline": "Pipeline",
        "gas_facility": "Gas Facility",
        "power_facility": "Power Facility",
    }[target_type]

    area = extract_area(t)
    label = f"Unknown {pretty_type} ({area})" if area else f"Unknown {pretty_type}"
    return [(label, target_type)]


def normalize_targets_string(targets: list[tuple[str, str]]) -> str:
    names = [t[0] for t in targets]
    deduped = []
    seen = set()
    for n in names:
        if n not in seen:
            deduped.append(n)
            seen.add(n)
    return " | ".join(deduped)


def merge_target_types(targets: list[tuple[str, str]]) -> str:
    kinds = sorted(set(t[1] for t in targets))
    if not kinds:
        return "unknown"
    if len(kinds) == 1:
        return kinds[0]
    return "mixed"


def detect_primary_attack(text: str) -> bool:
    return _has_any(lower_text(text), PRIMARY_ATTACK_PATTERNS)


def detect_attack_context(text: str) -> bool:
    return _has_any(lower_text(text), ATTACK_CONTEXT_PATTERNS)


def detect_fire(text: str) -> bool:
    return _has_any(lower_text(text), FIRE_PATTERNS)


def detect_explosions(text: str) -> int:
    t = lower_text(text)
    count = 0
    for pattern in EXPLOSION_PATTERNS:
        count += len(re.findall(pattern, t))

    m = re.search(r"как\s+минимум\s+(\d+)\s+взрыв", t)
    if m:
        count = max(count, int(m.group(1)))

    m = re.search(r"не\s+менее\s+(\d+)\s+взрыв", t)
    if m:
        count = max(count, int(m.group(1)))

    if re.search(r"несколько\s+взрыв", t):
        count = max(count, 3)

    if re.search(r"много\s+взрыв", t):
        count = max(count, 5)

    return count


def detect_air_defense(text: str) -> bool:
    return _has_any(lower_text(text), AIR_DEFENSE_PATTERNS)


def detect_shutdown(text: str) -> bool:
    return _has_any(lower_text(text), SHUTDOWN_PATTERNS)


def detect_attack_type(text: str) -> str:
    t = lower_text(text)
    has_drone = re.search(r"(?:бпла|беспилотник\w*|дрон\w*)", t)
    has_missile = re.search(r"(?:ракет\w*|нептун)", t)
    if has_drone and has_missile:
        return "combined"
    if has_missile:
        return "missile"
    if has_drone:
        return "drone"
    return "unknown"


def detect_report_type(text: str) -> str:
    t = lower_text(text)
    if _has_any(t, CORRECTION_PATTERNS):
        return "correction_update"
    if _has_any(t, UPDATE_ONLY_PATTERNS):
        return "indirect_aftermath"
    if any(x in t for x in ["минобороны", "губернатор", "генштаб", "пресс-служб"]):
        return "official_confirmation"
    return "direct_report"


def detect_hit_confirmed(text: str) -> bool:
    t = lower_text(text)
    if _has_any(t, DIRECT_HIT_PATTERNS):
        return True
    if detect_fire(t) or detect_shutdown(t) or detect_explosions(t) > 0:
        return True
    return False


def detect_damage_level(text: str) -> str:
    t = lower_text(text)
    if any(x in t for x in ["масштабн", "сильный пожар", "несколько резервуар", "работа предприятия остановлена"]):
        return "high"
    if any(x in t for x in ["поврежден", "пожар", "взрыв", "возгорание", "разлив"]):
        return "medium"
    if any(x in t for x in ["попытка атаки", "атаковали", "подвергся атаке"]):
        return "low"
    return "low"


def is_update_like(text: str) -> bool:
    return _has_any(lower_text(text), UPDATE_ONLY_PATTERNS)


def should_skip_fast(text: str) -> bool:
    t = lower_text(text)

    if _has_any(t, CLEAR_SKIP_PATTERNS):
        return True
    if _has_any(t, SUMMARY_SKIP_PATTERNS):
        return True
    if _has_any(t, RUSSIA_ATTACKS_UKRAINE_PATTERNS):
        return True

    return False


def is_fast_reject(text: str) -> bool:
    """
    Lightweight pre-filter: discard obvious noise before any expensive processing.
    Returns True if the message is definitely irrelevant.

    IMPORTANT: Only apply patterns that are safe at full-message scope — i.e.,
    if the pattern matches anywhere in the message, the message is definitely
    not an attack report. Do NOT put context-sensitive patterns here (e.g.,
    "российские атаки") because real attack reports often mention Russia
    attacking Ukraine in a subordinate clause. Those patterns belong in
    should_skip_fast(), which operates on short per-window text.
    """
    t = lower_text(text)

    # Fundraising / admin posts — never attack reports
    if _has_any(t, DONATION_PATTERNS):
        return True
    if _has_any(t, SUBSCRIPTION_PATTERNS):
        return True

    # Editorial / legal articles about past events
    if _has_any(t, CLEAR_SKIP_PATTERNS):
        return True

    # Daily/nightly summary posts — aggregate counts, not individual events
    if _has_any(t, SUMMARY_SKIP_PATTERNS):
        return True

    return False


def is_followup_only(text: str) -> bool:
    t = lower_text(text)

    has_followup_marker = _has_any(t, FOLLOWUP_ONLY_PATTERNS)
    has_hard_signal = (
        detect_fire(t)
        or detect_shutdown(t)
        or detect_explosions(t) > 0
        or detect_air_defense(t)
    )

    return has_followup_marker and not has_hard_signal


def is_energy_event_candidate(text: str) -> bool:
    t = lower_text(text)

    if is_followup_only(t):
        return False

    if should_skip_fast(t):
        return False

    if not is_russia_related(t):
        return False

    targets = extract_all_targets(t)
    if not targets:
        return False

    has_attack = detect_primary_attack(t) or detect_attack_context(t)
    has_damage = detect_fire(t) or detect_explosions(t) > 0 or detect_shutdown(t)

    if has_attack or has_damage:
        return True

    return False


def infer_numeric_mentions(text: str) -> list[int]:
    t = lower_text(text)
    nums = []
    drone_word = r"(?:бпла|беспилотник\w*|дрон\w*)"

    patterns = [
        rf"(\d+)\s*{drone_word}",
        rf"{drone_word}\s*(\d+)",
        rf"из\s+(\d+)\s*{drone_word}",
        rf"всего\s+(\d+)\s*{drone_word}",
        r"(\d+)\s+из\s+них",
        r"(?:сбиты|уничтожены|подавлены)\s+(\d+)",
        r"(?:атаковали|нанесли\s+удар|пытались\s+атаковать)\s+(\d+)",
        rf"(?:не\s+менее|как\s+минимум)\s+(\d+)\s*{drone_word}",
        r"около\s+(\d+)\s+беспилотник",
        r"более\s+(\d+)\s+бпла",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, t):
            try:
                nums.append(int(match.group(1)))
            except Exception:
                pass

    for word, value in RUS_NUM_WORDS.items():
        if re.search(rf"\b{word}\b\s*(?:бпла|беспилотник\w*|дрон\w*)", t):
            nums.append(value)

    if re.search(r"\bнесколько\b", t):
        nums.append(3)

    return nums


def infer_drone_scale(text: str, attack_type: str, update_like: bool) -> Optional[str]:
    t = lower_text(text)
    nums = infer_numeric_mentions(t)
    best_num = max(nums) if nums else None

    if best_num is not None:
        if best_num >= 15:
            return "massive"
        if best_num >= 5:
            return "swarm"
        if best_num >= 1:
            return "few"

    if update_like:
        return None

    if attack_type in ("drone", "combined"):
        return "few"

    return None


def detect_combined_strike(text: str, targets: list[tuple[str, str]], existing_event_found: bool) -> bool:
    attack_type = detect_attack_type(text)
    if len(targets) > 1:
        return True
    if existing_event_found:
        return True
    return attack_type == "combined"


def extract_attack_date(msg_dt, text: str):
    t = lower_text(text)

    month_map = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }

    patterns = [
        r"в\s+ночь\s+на\s+(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
        r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
    ]

    msg_local = msg_dt.astimezone(LOCAL_TZ)
    year = msg_local.year

    for pattern in patterns:
        m = re.search(pattern, t)
        if m:
            day = int(m.group(1))
            month = month_map[m.group(2)]
            try:
                return datetime(year, month, day, tzinfo=LOCAL_TZ).date()
            except Exception:
                pass

    # Relative date references
    if re.search(r"\bвчера\w*\b", t):
        return (msg_local - timedelta(days=1)).date()

    return msg_local.date()


def build_windows(text: str) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []

    windows = []
    for i, s in enumerate(sentences):
        st = lower_text(s)
        anchor = (
            detect_primary_attack(st)
            or detect_fire(st)
            or detect_shutdown(st)
            or extract_local_target_type(st) is not None
        )
        if anchor:
            left = max(0, i - 1)
            right = min(len(sentences), i + 3)
            window = " ".join(sentences[left:right]).strip()
            windows.append(window)

    if not windows:
        return [text]

    dedup = []
    seen = set()
    for w in windows:
        key = lower_text(w)
        if key not in seen:
            dedup.append(w)
            seen.add(key)
    return dedup
