"""Сборка запроса к Nominatim: адреса, на которых OSM ложно отвечал «здание не найдено»."""

import unittest
from unittest.mock import patch

from app.services.osm import entrances
from app.services.osm_query import query_variants

CASES = (
    # тип улицы впереди: в OSM «улица Маршала Захарова», а не «Маршала Захарова улица»
    ("Город Москва, ул.Маршала Захарова, д. 18 к 1", "Москва, улица Маршала Захарова, 18 к1"),
    # дробный номер дома не обрезается до «24»
    ("Город Москва, ул.Земляной Вал, д. 24/30 стр. 1", "Москва, улица Земляной Вал, 24/30 с1"),
    # «проезд.» разбирается, улица-прилагательное — названием вперёд
    ("Город Москва, проезд.Ферганский, д. 7 к 5", "Москва, Ферганский проезд, 7 к5"),
    ("Город Москва, пр-кт.Волгоградский, д. 128 к 5", "Москва, Волгоградский проспект, 128 к5"),
    ("Город Москва, б-р.Кленовый, д. 10 к 1", "Москва, Кленовый бульвар, 10 к1"),
    ("Город Москва, ул.3-я Карачаровская, д. 8 к 2", "Москва, 3-я Карачаровская улица, 8 к2"),
    ("Город Москва, пер.Большой Рогожский, д. 10 к 2", "Москва, Большой Рогожский переулок, 10 к2"),
    ("Город Москва, ул.Машкова, д. 13 стр. 1, кв. 15", "Москва, улица Машкова, 13 с1"),
    # адрес из ГИС ЖКХ
    ("109443, г Москва, ул Юных Ленинцев, д. 83, к. 1", "Москва, улица Юных Ленинцев, 83 к1"),
    # адрес из DomMos: полный тип улицы, «дом», «корпус»
    ("Российская Федерация, город Москва, внутригородская территория муниципальный округ Текстильщики, "
     "Волжский бульвар, дом 50, корпус 2", "Москва, Волжский бульвар, 50 к2"),
    ("Российская Федерация, город Москва, внутригородская территория муниципальный округ Можайский, "
     "бульвар Генерала Карбышева, дом 19, корпус 5", "Москва, бульвар Генерала Карбышева, 19 к5"),
)


class OsmQueryTests(unittest.TestCase):
    def test_first_variant(self):
        for address, expected in CASES:
            with self.subTest(address=address):
                self.assertEqual(query_variants(address)[0], expected)

    def test_second_variant_is_other_order(self):
        self.assertEqual(
            query_variants("Город Москва, ул.Маршала Захарова, д. 18 к 1")[1],
            "Москва, Маршала Захарова улица, 18 к1",
        )

    @patch("app.services.osm.time.sleep")
    @patch("app.services.osm._get")
    def test_tries_second_variant_when_first_finds_no_building(self, get, _sleep):
        get.side_effect = [
            [{"osm_type": "way", "class": "highway", "osm_id": 1}],
            [{"osm_type": "way", "class": "building", "osm_id": 42,
              "entrances": [{"extratags": {"ref": "1", "addr:flats": "1-20"}, "lat": "55.1", "lon": "37.1"}]}],
        ]
        result = entrances("Город Москва, ул.Маршала Захарова, д. 18 к 1")
        self.assertTrue(result["found"])
        self.assertEqual(result["osmId"], 42)
        self.assertEqual(get.call_count, 2)

    @patch("app.services.osm.time.sleep")
    @patch("app.services.osm._get")
    def test_timeout_is_retry_not_missing_building(self, get, _sleep):
        get.side_effect = TimeoutError("read timed out")
        result = entrances("Город Москва, ул.Маршала Захарова, д. 18 к 1")
        self.assertFalse(result["found"])
        self.assertTrue(result["retry"])
        self.assertIn("временно недоступен", result["reason"])


if __name__ == "__main__":
    unittest.main()
