import unittest

from scripts.access_naming import link_name


class AccessNamingTests(unittest.TestCase):
    def test_standalone_name_has_no_topology_segment(self):
        self.assertEqual(
            link_name("RU", "k8m4q2p", "vless-reality-vision"),
            "nitka-ru-k8m4q2p-vless-reality-vision",
        )

    def test_cascade_name_identifies_the_direction_scheme(self):
        self.assertEqual(
            link_name("RU", "k8m4q2p", "vless-reality-vision", "cascade"),
            "nitka-ru-k8m4q2p-cascade-vless-reality-vision",
        )
        self.assertEqual(
            link_name("RU", "k8m4q2p", "vless-reality-vision", "cascade-reverse"),
            "nitka-ru-k8m4q2p-cascade-reverse-vless-reality-vision",
        )

    def test_unknown_topology_cannot_enter_a_client_name(self):
        with self.assertRaisesRegex(ValueError, "unsupported link topology"):
            link_name("RU", "k8m4q2p", "vless-reality-vision", "unknown")
