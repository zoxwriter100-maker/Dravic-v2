import unittest

from bot import parse_spotify_track_id


class SpotifyTrackIdTests(unittest.TestCase):
    TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"

    def test_canonical_track_url_with_query(self):
        self.assertEqual(
            parse_spotify_track_id(f"https://open.spotify.com/track/{self.TRACK_ID}?si=abc123"),
            self.TRACK_ID,
        )

    def test_locale_prefixed_track_url(self):
        self.assertEqual(
            parse_spotify_track_id(f"https://open.spotify.com/intl-en/track/{self.TRACK_ID}"),
            self.TRACK_ID,
        )

    def test_track_uri(self):
        self.assertEqual(parse_spotify_track_id(f"spotify:track:{self.TRACK_ID}"), self.TRACK_ID)

    def test_non_spotify_search_is_unchanged(self):
        self.assertIsNone(parse_spotify_track_id("Daft Punk One More Time"))

    def test_album_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "single track"):
            parse_spotify_track_id("https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC")

    def test_spoofed_spotify_domain_is_not_accepted(self):
        self.assertIsNone(
            parse_spotify_track_id(f"https://open.spotify.com.evil.example/track/{self.TRACK_ID}")
        )


if __name__ == "__main__":
    unittest.main()
