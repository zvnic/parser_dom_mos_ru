"""Скачивает реестры лицензий ГИС ЖКХ и строит индекс адрес -> ФИАС/УК."""

import io
import json
import os
import re
import sys
import urllib.parse
import zipfile
from datetime import date
from pathlib import Path

import openpyxl

from app.core.config import settings
from app.services.gis_zhkh import BASE, _request, address_key


def _iso(value) -> str | None:
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", str(value or "").strip())
    return f"{match[3]}-{match[2]}-{match[1]}" if match else None


def _download_registry(region: str, directory: Path) -> Path:
    link = _request(f"{BASE}/rls/api/rest/services/license/public/report-link?regionCode={region}")
    url = BASE + urllib.parse.quote(link["shortFileUrl"], safe="/?=&")
    archive = zipfile.ZipFile(io.BytesIO(_request(url, raw=True)))
    xlsx = next(name for name in archive.namelist() if name.lower().endswith(".xlsx"))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"reestr_{region}.xlsx"
    path.write_bytes(archive.read(xlsx))
    return path


def build(regions: list[str], output: Path) -> dict:
    today = date.today().isoformat()
    index: dict = {}
    directory = output.parent
    for region in regions:
        path = _download_registry(region, directory)
        sheet = openpyxl.load_workbook(path, read_only=True).active
        header = None
        for row in sheet.iter_rows(values_only=True):
            if header is None:
                cells = [str(cell).strip() if cell else "" for cell in row]
                if "Код дома по ФИАС" in cells:
                    header = {str(cell).strip(): position for position, cell in enumerate(row) if cell}
                continue

            def get(name):
                position = header.get(name)
                return row[position] if position is not None and position < len(row) else None

            fias = get("Код дома по ФИАС")
            address = get("Адрес многоквартирного дома")
            started = _iso(get("Дата начала полномочий по управлению домом"))
            if not fias or not address or not started or started > today:
                continue
            ended = _iso(get("Дата окончания полномочий по управлению домом")) or _iso(
                get("Дата исключения дома из реестра")
            )
            if ended and ended < today:
                continue
            if not ended and str(get("Статус лицензии") or "").strip() != "Действующая лицензия":
                continue
            key = address_key(address)
            if not key:
                continue
            index[key] = {
                "fias": str(fias).strip().lower(), "address": address, "region": region,
                "uk_name": get("Наименование/ ФИО лицензиата"),
                "uk_inn": str(get("ИНН") or ""),
                "uk_ogrn": str(get("ОГРН/ ОГРНИП") or ""),
                "license": get("Номер лицензии в ЕРУЛ"), "managed_since": started,
            }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output)
    return index


if __name__ == "__main__":
    target = Path(settings.gis_zhkh_index_path or "/app/data/gis_zhkh_index.json")
    regions = sys.argv[1:] or ["77", "50"]
    result = build(regions, target)
    print(f"Индекс записан в {target}: {len(result)} домов")
