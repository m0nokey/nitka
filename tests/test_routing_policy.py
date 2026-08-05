import copy
import hashlib
import unittest
from pathlib import Path

from scripts.routing_policy import import_routing_policy


class RoutingPolicyTests(unittest.TestCase):
    def test_import_accepts_canonical_variable_names(self):
        path = Path(__file__).parent / "fixtures" / "canonical-routing.yml"
        state = {"nodes": {"ingress": {"access": {"xray_reality": {"access_keys": []}}}}}

        result = import_routing_policy(state, "ingress", path)

        xray = result["nodes"]["ingress"]["access"]["xray_reality"]
        self.assertFalse(xray["clash_tcp_concurrent"])
        self.assertEqual(xray["reality_proxy_domains"], ["domain:first.example"])
        self.assertEqual(
            xray["dns_direct_domains"],
            ["domain:direct.example", "full:api.direct.example"],
        )
        self.assertEqual(xray["dns_direct_servers"], [{"address": "1.1.1.1"}])

    def test_import_resolves_aggregates_and_preserves_categories(self):
        path = Path(__file__).parent / "fixtures" / "legacy-routing.yml"
        content = path.read_text(encoding="utf-8")
        state = {"nodes": {"ingress": {"access": {"xray_reality": {"access_keys": []}}}}}
        original = copy.deepcopy(state)
        result = import_routing_policy(state, "ingress", path)

        self.assertEqual(state, original)
        xray = result["nodes"]["ingress"]["access"]["xray_reality"]
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
            xray["routing_policy"]["source_variables"]["access_xray_block_domains"],
            ["geosite:category-ads-all"],
        )


if __name__ == "__main__":
    unittest.main()
