"""Сборка запросов к Nominatim по адресу дома.

Адрес приходит в разных видах: из заявки («Город Москва, ул.Земляной Вал, д. 24/30 стр. 1»),
из ГИС ЖКХ («109443, г Москва, ул Юных Ленинцев, д. 83, к. 1») или из DomMos
(«…, Волжский бульвар, дом 50, корпус 2»). Nominatim находит здание надёжно по виду
«Москва, <улица в написании OSM>, <номер> к<корпус> с<строение>».

В OSM улицы-прилагательные пишутся названием вперёд («Волгоградский проспект»,
«Ферганский проезд»), остальные — типом вперёд («улица Маршала Захарова»,
«улица Земляной Вал»). Поэтому отдаём два варианта: сначала более вероятный, потом запасной.
"""

import re

# сокращение -> полный тип улицы; сокращение не должно продолжаться буквой («ул» ≠ «улица»)
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
    "проспект", "проезд", "улица", "бульвар", "переулок", "набережная", "шоссе", "площадь",
    "тупик", "аллея", "линия", "просек", "квартал", "микрорайон",
)
_FULL_RE = "|".join(sorted(_FULL_TYPES, key=len, reverse=True))
_ADJ_END = re.compile(r"(?:ский|цкий|ской|цкой|ный|ной|ний|ый|ий|ой|ая|яя|ое|ее)$", re.I)
_NUMBER = r"(\d+[А-ЯЁA-Zа-яё]?(?:/\d+[А-ЯЁA-Zа-яё]?)?)"


def _street(value: str) -> tuple[str | None, str | None, str]:
    """(тип, название, остаток строки после улицы)."""
    for part in [p.strip() for p in value.split(",")]:
        if not part:
            continue
        for short, full in _SHORT_TYPES:
            m = re.match(rf"^(?:{short})(?![а-яё])\.?\s*(.+)$", part, flags=re.I)
            if m and not re.match(r"^(?:\d+\s*$|(?:дом|д))", m.group(1), flags=re.I):
                return full, m.group(1).strip(), value[value.index(part) + len(part):]
        m = re.search(rf"(?<![а-яё])({_FULL_RE})(?![а-яё])", part, flags=re.I)
        if m:
            name = (part[:m.start()] + part[m.end():]).strip(" .")
            if name and not re.match(r"^(?:дом|д)\b", name, flags=re.I):
                return m.group(1).lower(), re.sub(r"\s+", " ", name), value[value.index(part) + len(part):]
    return None, None, value


def query_variants(address: str) -> list[str]:
    """Варианты запроса к Nominatim; пробовать по очереди до первого здания."""
    value = re.sub(r"^.*?\bМосква,\s*", "", str(address or ""), flags=re.I)
    value = re.sub(r",\s*(?:кв|квартира)\.?\s*\d+.*$", "", value, flags=re.I)
    street_type, street, rest = _street(value)
    house = (re.search(rf"\b(?:дом|д)\.?\s*{_NUMBER}", rest, flags=re.I)
             or re.search(rf"(?:^|,)\s*{_NUMBER}(?!\d)", rest))
    if not street or not house:
        return ["Москва, " + re.sub(r"\s+", " ", value).strip(" ,")]
    tail = rest[house.end():]
    corpus = re.search(r"(?<![а-яё])(?:корпус|корп|к)\.?\s*(\d+[А-ЯЁA-Zа-яё]?)", tail, flags=re.I)
    structure = re.search(r"(?<![а-яё])(?:строение|стр|с)\.?\s*(\d+[А-ЯЁA-Zа-яё]?)", tail, flags=re.I)
    number = house.group(1)
    if corpus:
        number += f" к{corpus.group(1)}"
    if structure:
        number += f" с{structure.group(1)}"
    adjective = bool(_ADJ_END.search(street.split()[-1]))
    first = f"{street} {street_type}" if adjective else f"{street_type} {street}"
    second = f"{street_type} {street}" if adjective else f"{street} {street_type}"
    return [f"Москва, {first}, {number}", f"Москва, {second}, {number}"]
