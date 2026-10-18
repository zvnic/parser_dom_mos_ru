"""Сборка устойчивых вариантов адреса для Nominatim."""

import re


_SHORT_TYPES = (
    (r"пр-кт|просп", "проспект"),
    (r"пр-д", "проезд"),
    (r"ул", "улица"),
    (r"б-р|бул", "бульвар"),
    (r"пер", "переулок"),
    (r"наб", "набережная"),
    (r"ш", "шоссе"),
    (r"пл", "площадь"),
    (r"туп", "тупик"),
    (r"ал", "аллея"),
    (r"кв-л", "квартал"),
    (r"мкр", "микрорайон"),
)
_FULL_TYPES = (
    "проспект", "проезд", "улица", "бульвар", "переулок", "набережная",
    "шоссе", "площадь", "тупик", "аллея", "линия", "просек", "квартал",
    "микрорайон",
)
_FULL_RE = "|".join(sorted(_FULL_TYPES, key=len, reverse=True))
_ADJ_END = re.compile(
    r"(?:ский|цкий|ской|цкой|ный|ной|ний|ый|ий|ой|ая|яя|ое|ее|ские|ные)$",
    re.I,
)
_NUMBER = r"(\d+[А-ЯЁA-Zа-яё]?(?:/\d+[А-ЯЁA-Zа-яё]?)?)"


def _street(value: str) -> tuple[str | None, str | None, str]:
    """Возвращает тип, название улицы и остаток строки после неё."""
    offset = 0
    for raw_part in value.split(","):
        part = raw_part.strip()
        part_start = offset + len(raw_part) - len(raw_part.lstrip())
        offset += len(raw_part) + 1
        if not part:
            continue
        for short, full in _SHORT_TYPES:
            match = re.match(rf"^(?:{short})(?![а-яё])\.?\s*(.+)$", part, flags=re.I)
            if match and not re.match(r"^(?:\d+\s*$|(?:дом|д)\b)", match.group(1), re.I):
                end = part_start + len(part)
                return full, match.group(1).strip(), value[end:]
        match = re.search(rf"(?<![а-яё])({_FULL_RE})(?![а-яё])", part, flags=re.I)
        if match:
            name = (part[:match.start()] + part[match.end():]).strip(" .")
            if name and not re.match(r"^(?:дом|д)\b", name, flags=re.I):
                end = part_start + len(part)
                return match.group(1).lower(), re.sub(r"\s+", " ", name), value[end:]
    return None, None, value


def building_parts(address: str) -> tuple[str | None, str | None, str | None]:
    """Извлекает дом, корпус и строение без потери дробного номера."""
    value = str(address or "")
    house = (
        re.search(rf"\b(?:дом|д)\.?\s*{_NUMBER}", value, flags=re.I)
        or re.search(rf"(?:^|,)\s*{_NUMBER}(?!\d)", value)
    )
    if not house:
        return None, None, None
    tail = value[house.end():]
    corpus = re.search(
        r"(?<![а-яё])(?:корпус|корп|к)\.?\s*(\d+[А-ЯЁA-Zа-яё]?)",
        tail,
        flags=re.I,
    )
    structure = re.search(
        r"(?<![а-яё])(?:строение|стр|с)\.?\s*(\d+[А-ЯЁA-Zа-яё]?)",
        tail,
        flags=re.I,
    )
    return (
        house.group(1),
        corpus.group(1) if corpus else None,
        structure.group(1) if structure else None,
    )


def query_variants(address: str) -> list[str]:
    """Строит оба порядка типа улицы, сначала наиболее вероятный."""
    value = re.sub(r"^.*?\bМосква,\s*", "", str(address or ""), flags=re.I)
    value = re.sub(r",\s*(?:кв|квартира)\.?\s*\d+.*$", "", value, flags=re.I)
    street_type, street, rest = _street(value)
    house, corpus, structure = building_parts(rest)
    if not street or not house:
        return ["Москва, " + re.sub(r"\s+", " ", value).strip(" ,")]

    number = house
    if corpus:
        number += f" к{corpus}"
    if structure:
        number += f" с{structure}"
    adjective = bool(_ADJ_END.search(street.split()[-1]))
    first = f"{street} {street_type}" if adjective else f"{street_type} {street}"
    second = f"{street_type} {street}" if adjective else f"{street} {street_type}"
    return [f"Москва, {first}, {number}", f"Москва, {second}, {number}"]
