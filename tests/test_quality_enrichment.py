import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import openpyxl

from app.scripts.build_gis_index import build
from app.services.canonical import to_canonical
from app.services.gis_zhkh import address_key
from app.services.osm import entrances


class CanonicalQualityTests(unittest.TestCase):
    def test_official_passport_has_priority_and_preserves_zero(self):
        model = to_canonical(
            {"id": "1", "fullAddress": "Москва, дом 1", "square": 90, "floorsCount": 5},
            {
                "fiasId": "fias",
                "apartments": "12",
                "lifts": "1",
                "resourceProviders": [{"name": "РСО", "phone": "+7"}],
                "electronicPassport": {
                    "apartments": 20,
                    "lifts": 2,
                    "parkingPlaces": 0,
                    "buildingAreaSqm": 100.5,
                    "elements": [{"element": "Фасад", "wearPct": 0}],
                },
            },
            osm={"entrancesCount": 2, "coverage": "estimate"},
        )
        self.assertEqual(model.apartments_count, 20)
        self.assertEqual(model.lifts_count, 2)
        self.assertEqual(model.parking_places_count, 0)
        self.assertEqual(model.total_area_sqm, 100.5)
        self.assertEqual(model.resource_providers[0].name, "РСО")
        self.assertEqual(model.building_elements[0].wear_pct, 0)
        self.assertEqual(model.entrances_coverage, "estimate")
        self.assertEqual(model.field_sources["apartments_count"], "gis_zhkh_passport")
        self.assertEqual(model.field_sources["entrances_count"], "osm")

    def test_address_key_supports_index_and_corpus(self):
        self.assertEqual(
            address_key("109439, Город Москва, пр-кт.Волгоградский, д. 128 к 5"),
            "волгоградский|128|к5",
        )


class OsmEntranceTests(unittest.TestCase):
    @patch("app.services.osm._get")
    def test_osm_ranges_and_flat_entrance(self, get):
        get.side_effect = [
            [{"osm_type": "way", "class": "building", "osm_id": 42,
              "extratags": {"building:levels": "5", "building:flats": "40"}}],
            {"elements": [
                {"tags": {"ref": "1", "addr:flats": "1-20"}, "lat": 55.1, "lon": 37.1},
                {"tags": {"ref": "2", "addr:flats": "21-40"}, "lat": 55.2, "lon": 37.2},
            ]},
        ]
        result = entrances("Москва, ул. Тестовая, д. 1", flat=25)
        self.assertEqual(result["coverage"], "osm")
        self.assertEqual(result["entrancesCount"], 2)
        self.assertEqual(result["flatEntrance"]["entrance"], 2)
        self.assertEqual(result["levels"], 5)


class GisIndexTests(unittest.TestCase):
    @patch("app.scripts.build_gis_index._download_registry")
    def test_builds_active_management_index_atomically(self, download):
        with TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append([
                "Код дома по ФИАС", "Адрес многоквартирного дома",
                "Дата начала полномочий по управлению домом", "Статус лицензии",
                "Наименование/ ФИО лицензиата", "ИНН", "ОГРН/ ОГРНИП",
                "Номер лицензии в ЕРУЛ",
            ])
            sheet.append([
                "FIAS-1", "г. Москва, ул. Тестовая, д. 1", "01.01.2020",
                "Действующая лицензия", "Тестовая УК", "7701", "10277", "Л-1",
            ])
            workbook.save(registry)
            download.return_value = registry
            output = Path(directory) / "index.json"
            result = build(["77"], output)
            self.assertEqual(result["тестовая|1"]["fias"], "fias-1")
            self.assertTrue(output.exists())
            self.assertFalse(output.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
