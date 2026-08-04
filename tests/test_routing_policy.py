import copy
import hashlib
import unittest
from pathlib import Path

from scripts.routing_policy import import_routing_policy


class RoutingPolicyTests(unittest.TestCase):
    def test_import_resolves_aggregates_and_preserves_categories(self):
        path = Path(__file__).parent / "fixtures" / "legacy-routing.yml"
        content = path.read_text(encoding="utf-8")
        state = {"nodes": {"ingress": {"xray": {"access_keys": []}}}}
        original = copy.deepcopy(state)
        result = import_routing_policy(state, "ingress", path)

        self.assertEqual(state, original)
        xray = result["nodes"]["ingress"]["xray"]
        self.assertFalse(xray["clash_tcp_concurrent"])
        self.assertEqual(
            xray["reality_proxy_domains"],
            ["keyword:second", "domain:first.example"],
        )
        self.assertEqual(xray["reality_proxy_ips"], ["192.0.2.0/24"])
        self.assertEqual(
            xray["dns_direct_domains"],
            ["domain:direct.example", "full:api.direct.example"],
        )
        self.assertEqual(
            xray["routing_policy"]["categories"]["reality_proxy_domains"],
            {"first": ["domain:first.example"], "second": ["keyword:second"]},
        )
        self.assertEqual(
            xray["routing_policy"]["source_sha256"],
            hashlib.sha256(content.encode()).hexdigest(),
        )
        self.assertEqual(
            xray["routing_policy"]["source_variables"]["ingress_xray_block_domains"],
            ["geosite:category-ads-all"],
        )


if __name__ == "__main__":
    unittest.main()
