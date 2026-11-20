import unittest
import app


class AddonContractTests(unittest.TestCase):
    def test_manifest_is_search_and_stream_only(self):
        self.assertEqual(app.manifest["resources"], ["catalog", "meta", "stream"])
        self.assertEqual(
            [catalog["id"] for catalog in app.manifest["catalogs"]],
            ["animux-search"],
        )
        self.assertIn("series", app.manifest["types"])

    def test_payload_round_trip(self):
        data = {
            "anime_id": "animeflv:dragon-ball",
            "episode": "1",
            "title": "Dragon Ball",
        }
        self.assertEqual(app._decode(app._encode(data)), data)

    def test_castellano_is_excluded(self):
        self.assertTrue(app._is_non_latin_release("Anime en Castellano"))
        self.assertFalse(app._is_non_latin_release("Anime Latino"))

    def test_stream_requires_http_url(self):
        self.assertIsNone(
            app._stream_from_result({"url": "not-a-url"}, "1", "Anime")
        )
        stream = app._stream_from_result(
            {
                "url": "https://cdn.example/video.mp4",
                "referer": "https://embed.example/",
                "extractor": "animeav1",
                "ext": "mp4",
            },
            "1",
            "Dragon Ball",
        )
        self.assertEqual(stream["url"], "https://cdn.example/video.mp4")
        self.assertEqual(
            stream["behaviorHints"]["proxyHeaders"]["request"]["Referer"],
            "https://embed.example/",
        )


if __name__ == "__main__":
    unittest.main()
