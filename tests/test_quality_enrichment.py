import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import openpyxl

from app.scripts.build_gis_index import build
from app.services.canonical import to_canonical
from app.services.dom_mos import pick_best
from app.services.gis_zhkh import address_key
from app.services.osm import _estimated_points, _overpass_get, _query_address, entrances


class CanonicalQualityTests(unittest.TestCase):
    def test_osm_only_result_has_stable_identity_and_address(self):
        model = to_canonical(
            osm={
                "found": True, "osmType": "way", "osmId": 42,
                "address": "Москва, улица Тестовая, дом 1",
            }
        )
        self.assertEqual(model.id, "osm:way:42")
        self.assertEqual(model.address, "Москва, улица Тестовая, дом 1")

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

    def test_dom_mos_rejects_wrong_single_candidate(self):
        candidates = [{"address": "Керамический проезд, дом 77, корпус 2"}]
        self.assertIsNone(pick_best(candidates, "2", "2"))


class OsmEntranceTests(unittest.TestCase):
    def test_normalizes_abbreviation_without_space_after_dot(self):
        self.assertEqual(
            _query_address("Город Москва, б-р.Ореховый, д. 11 к 1"),
            "Москва, Ореховый бульвар, 11 к1",
        )

    def test_compacts_long_mos_ru_address(self):
        self.assertEqual(
            _query_address(
                "Российская Федерация, город Москва, внутригородская территория "
                "муниципальный округ Нагатинский Затон, Кленовый бульвар, "
                "дом 10, корпус 1"
            ),
            "Москва, Кленовый бульвар, 10 к1",
        )

    def test_street_name_prefix_is_not_treated_as_abbreviation(self):
        self.assertEqual(
            _query_address(
                "115088, г Москва, ул Шарикоподшипниковская, д. 28"
            ),
            "Москва, Шарикоподшипниковская улица, 28",
        )
        self.assertEqual(
            _query_address("Москва, Плеханова улица, дом 12"),
            "Москва, улица Плеханова, 12",
        )

    def test_estimates_points_along_longest_building_edge(self):
        points = _estimated_points({
            "type": "Polygon",
            "coordinates": [[
                [37.0, 55.0], [37.3, 55.0], [37.3, 55.1],
                [37.0, 55.1], [37.0, 55.0],
            ]],
        }, 2)

        self.assertEqual(points[0], (55.0, 37.1))
        self.assertAlmostEqual(points[1][0], 55.0)
        self.assertAlmostEqual(points[1][1], 37.2)

    @patch("app.services.osm._get")
    def test_overpass_uses_fallback_endpoint(self, get):
        get.side_effect = [OSError("primary unavailable"), {"elements": []}]

        self.assertEqual(_overpass_get(b"data=query"), {"elements": []})
        self.assertIn("maps.mail.ru", get.call_args_list[1].args[0])

    @patch("app.services.osm._get")
    def test_uses_entrances_embedded_in_nominatim_result(self, get):
        get.return_value = [{
            "osm_type": "way", "category": "building", "osm_id": 42,
            "entrances": [{
                "extratags": {"ref": "1", "addr:flats": "1-20"},
                "lat": "55.1", "lon": "37.1",
            }],
        }]

        result = entrances("Москва, ул.Тестовая, д. 1", flat=10)

        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["flatEntrance"]["entrance"], 1)
        self.assertEqual(result["flatEntrance"]["lat"], "55.1")

    @patch("app.services.osm._get")
    def test_osm_ranges_and_flat_entrance(self, get):
        get.side_effect = [
            [{"osm_type": "way", "class": "building", "osm_id": 42,
              "extratags": {"building:levels": "5", "building:flats": "40"}}],
            {"elements": [
                {"type": "node", "tags": {"entrance": "yes", "ref": "1", "addr:flats": "1-20"}, "lat": 55.1, "lon": 37.1},
                {"type": "node", "tags": {"entrance": "yes", "ref": "2", "addr:flats": "21-40"}, "lat": 55.2, "lon": 37.2},
            ]},
        ]
        result = entrances("Москва, ул. Тестовая, д. 1", flat=25)
        self.assertEqual(result["coverage"], "osm")
        self.assertEqual(result["entrancesCount"], 2)
        self.assertEqual(result["flatEntrance"]["entrance"], 2)
        self.assertEqual(result["levels"], 5)

    @patch("app.services.osm._get")
    def test_uses_later_relation_candidate_and_keeps_unnumbered_entrance(self, get):
        get.side_effect = [
            [
                {"osm_type": "node", "category": "place", "osm_id": 1},
                {"osm_type": "relation", "category": "building", "osm_id": 42},
            ],
            {"elements": [
                {"tags": {"entrance": "yes"}, "lat": 55.1, "lon": 37.1},
            ]},
        ]

        result = entrances("Москва, ул.Тестовая, д. 1")

        self.assertTrue(result["found"])
        self.assertEqual(result["osmType"], "relation")
        self.assertEqual(result["entrances"][0]["lat"], 55.1)
        self.assertIn("rel%2842%29", get.call_args_list[1].args[1].decode())


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
