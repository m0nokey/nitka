import unittest

from scripts.transport_registry import (
    TRANSPORT_SSH_TUN,
    TRANSPORT_XRAY_REALITY,
    TRANSPORT_OPERATIONS,
    adapter_lifecycle,
    get_transport_adapter,
    validate_access_transport,
    validate_deployment_transports,
    validate_transport_plan,
)


class TransportRegistryTests(unittest.TestCase):
    def test_standalone_has_only_one_access_server_binding(self):
        plan = validate_transport_plan("standalone", TRANSPORT_XRAY_REALITY)

        self.assertEqual(plan["bindings"], [{
            "node_role": "standalone",
            "plane": "access",
            "transport": "xray-reality",
            "endpoint": "server",
        }])

    def test_cascade_pairs_one_backhaul_between_ingress_and_egress(self):
        plan = validate_transport_plan(
            "cascade", TRANSPORT_XRAY_REALITY, TRANSPORT_SSH_TUN
        )

        self.assertEqual(
            [(item["node_role"], item["plane"], item["endpoint"])
             for item in plan["bindings"]],
            [
                ("ingress", "access", "server"),
                ("ingress", "backhaul", "client"),
                ("egress", "backhaul", "server"),
            ],
        )
        self.assertEqual(
            [item["transport"] for item in plan["bindings"]],
            ["xray-reality", "ssh-tun", "ssh-tun"],
        )

    def test_canonical_xray_name_is_preserved(self):
        plan = validate_transport_plan("standalone", "xray-reality")

        self.assertEqual(plan["bindings"][0]["transport"], "xray-reality")

    def test_unimplemented_adapter_cannot_be_selected(self):
        with self.assertRaisesRegex(ValueError, "not implemented"):
            validate_transport_plan("cascade", "naiveproxy", "ssh-tun")

    def test_standalone_access_selection_is_canonicalized(self):
        self.assertEqual(validate_access_transport("xray-reality"), "xray-reality")
        self.assertEqual(validate_access_transport("ssh-proxy"), "ssh-proxy")

        with self.assertRaisesRegex(ValueError, "not implemented"):
            validate_access_transport("hysteria2")

    def test_deployment_contract_requires_backhaul_for_cascade(self):
        with self.assertRaisesRegex(ValueError, "requires a backhaul"):
            validate_deployment_transports({
                "topology": "cascade",
                "transports": {"access": {"transport": "xray-reality"}},
            })

    def test_implemented_adapters_expose_the_same_lifecycle(self):
        for plane, names in (("access", ("xray-reality", "ssh-proxy")), ("backhaul", ("ssh-tun",))):
            for name in names:
                adapter = get_transport_adapter(name, plane)
                lifecycle = adapter_lifecycle(adapter)
                self.assertEqual(lifecycle["operations"], TRANSPORT_OPERATIONS)
                self.assertEqual(lifecycle["deploy_tasks"], "deploy")
                self.assertEqual(lifecycle["remove_tasks"], "remove")


if __name__ == "__main__":
    unittest.main()
