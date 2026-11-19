import unittest
import app

class AddonContractTests(unittest.TestCase):
    def test_manifest_has_stremio_resources(self):
        self.assertEqual(app.manifest["resources"], ["catalog", "meta", "stream"])
        self.assertIn("series", app.manifest["types"])
    def test_payload_round_trip(self):
        data={"anime_id":"animeflv:dragon-ball","episode":"1","title":"Dragon Ball"}
        self.assertEqual(app._decode(app._encode(data)),data)
    def test_castellano_is_excluded(self):
        self.assertTrue(app._is_non_latin_release("Anime en Castellano"))
        self.assertFalse(app._is_non_latin_release("Anime Latino"))

if __name__ == "__main__": unittest.main()
